# -*- coding: utf-8 -*-
"""NInfer 백엔드: 실행 버튼 → 서버 자동 기동 → 생성 → (원하면) 서버 종료.

NInfer(`ninfer-serve`)에는 모델을 올리고 내리는 API 가 없다. 모델은 프로세스를 띄울 때
정해지고, 프로세스가 사는 동안 VRAM 에 상주한다. 그래서 예전에는 NInfer 설정 UI
(https://github.com/ssain3d-lgtm/NInfer-lgtm, 기본 http://127.0.0.1:8093)를 열어
시작/중지 버튼을 손으로 눌러야 했다 -- ComfyUI 에서 프롬프트 한 번 뽑자고 창을 오가야 했다.

이 백엔드는 그 설정 UI 의 HTTP API 를 대신 누른다.

  GET  /api/state   지금 상태 (stopped / starting / running / failed / external) + 돌고 있는 artifact
  GET  /api/models  모델 폴더의 .ninfer 목록 (server_model 드롭다운에도 들어간다)
  POST /api/start   {"artifact": "<이름>.ninfer"[, "force": true]}  저장된 프로필 값으로 띄운다
  POST /api/stop    {}  설정 UI 가 띄운 서버만, 신원을 확인한 뒤 종료한다

흐름:
  1. 생성 직전 상태를 본다. 꺼져 있으면 고른 artifact 로 띄우고 /health 가 200 이 될 때까지 기다린다.
     다른 artifact 가 돌고 있으면 내리고 고른 것으로 다시 띄운다.
  2. 요청의 model 은 서버의 공개 ID 여야 한다(NInfer 가 다른 이름을 거절한다).
     그래서 기동 후 /v1/models 에서 읽은 ID 로 바꿔서 보낸다.
  3. unload_after_generation 이 켜져 있으면 생성 뒤 /api/stop 으로 서버를 내려 VRAM 을 돌려준다.

VRAM 사전 점검(preflight)에 걸리면 ComfyUI 가 캐시해 둔 모델을 먼저 내리고 한 번 더 시도한다.
그래도 모자라면(blocked) 띄우지 않고 이유를 status 로 말한다. 들어가긴 하지만 빠듯한 경우(warn)는
force 로 띄우고 debug 에 적는다 -- 사용자가 실행 버튼을 눌렀다는 것 자체가 띄우라는 뜻이다.

관리는 "이 PC 의 NInfer 포트(기본 8081)" 를 쓸 때만 한다. 노드의 openai_base_url 로 다른 주소를
적었다면 그건 설정 UI 가 관리하는 서버가 아니므로 예전처럼 OpenAI 호환 서버로만 다룬다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from urllib.parse import urlparse

from ..utils import cancel
from .base import LLMRequest, LLMResponse, truncate_debug
from .lmstudio import is_loopback
from .openai_compat import KNOWN_SERVERS, OpenAICompatBackend

ARTIFACT_SUFFIX = ".ninfer"
DEFAULT_CONFIG_UI_URL = "http://127.0.0.1:8093"
# 테스트 스위트는 이 변수로 죽은 주소를 넣어 둔다(tests/__init__.py).
# 안 그러면 개발 PC 에서 테스트가 진짜 설정 UI 에 /api/stop 을 보낼 수 있다.
CONFIG_UI_ENV = "LLMHUB_NINFER_CONFIG_UI_URL"
DEFAULT_PORT = 8081
# 기동 대기 상한. 실측(2026-09-26): 모델이 WSL 디스크면 14초, /mnt/d 면 2분 39초.
DEFAULT_START_TIMEOUT_S = 600
POLL_S = 1.0
# /api/start 직후 잠깐은 PID 파일이 아직 안 보여 "stopped" 로 읽힐 수 있다.
STOPPED_GRACE_S = 8.0
# 설정 UI 의 stop 은 최대 20초 기다린다(STOP_WAIT_SECONDS). 여유를 둔다.
STOP_TIMEOUT_S = 45
STATE_TIMEOUT_S = 15
START_TIMEOUT_S = 120
CONFIG_UI_BOOT_S = 20

# 마지막으로 돌고 있던 artifact. (auto) 로 실행했는데 서버가 꺼져 있을 때 무엇을 띄울지 정한다.
# 직전 실행이 언로드로 서버를 내렸어도 다음 실행이 같은 모델로 다시 뜨게 한다.
_LAST_ARTIFACT = {"name": ""}

_GIB = 1024 ** 3


class NInferBackend(OpenAICompatBackend):
    name = "ninfer"

    def __init__(self, config: dict = None):
        super().__init__(config=config, base_url_default=KNOWN_SERVERS["ninfer"])
        section = self.config.get("ninfer", {}) or {}
        self.config_ui_url = (
            os.environ.get(CONFIG_UI_ENV)
            or section.get("config_ui_url")
            or DEFAULT_CONFIG_UI_URL
        ).rstrip("/")
        self.config_ui_script = (section.get("config_ui_script") or "").strip()
        self.default_artifact = (section.get("default_artifact") or "").strip()
        self.auto_start = bool(section.get("auto_start", True))
        self.port = int(section.get("port") or DEFAULT_PORT)
        self.start_timeout_s = int(section.get("start_timeout_s") or DEFAULT_START_TIMEOUT_S)

    # -- 관리 대상인가 --------------------------------------------------------

    def managed(self) -> bool:
        """설정 UI 로 켜고 끌 서버인가. 이 PC 의 NInfer 포트일 때만 그렇다."""
        if not self.auto_start or not is_loopback(self.base_url):
            return False
        try:
            return urlparse(self.base_url).port == self.port
        except ValueError:
            return False

    # -- 설정 UI HTTP ----------------------------------------------------------

    def _ui(self, method: str, path: str, body=None, timeout_s: float = STATE_TIMEOUT_S):
        """설정 UI 에 요청한다. 반환: (HTTP 코드, JSON). 연결 실패면 (None, {})."""
        import requests

        try:
            if method == "GET":
                resp = requests.get(self.config_ui_url + path, timeout=timeout_s)
            else:
                # 설정 UI 는 POST 에 Content-Type: application/json 을 요구한다(CSRF 방어).
                resp = requests.post(self.config_ui_url + path, json=body or {}, timeout=timeout_s)
        except Exception:
            return None, {}
        try:
            data = resp.json()
        except ValueError:
            data = {}
        return resp.status_code, data if isinstance(data, dict) else {}

    def _state(self):
        code, data = self._ui("GET", "/api/state")
        return data if code == 200 and data.get("status") else None

    def _artifacts(self) -> list:
        code, data = self._ui("GET", "/api/models")
        if code != 200:
            return []
        return [
            m.get("name") for m in data.get("models") or []
            if isinstance(m, dict) and m.get("name") and m.get("supported", True)
        ]

    def _health(self) -> bool:
        import requests

        try:
            return requests.get(self.base_url + "/health", timeout=2).status_code == 200
        except Exception:
            return False

    def _public_id(self) -> str:
        """서버가 받는 모델 이름. NInfer 는 이것 말고는 거절한다."""
        import requests

        try:
            resp = requests.get(self.base_url + "/v1/models", headers=self._headers(), timeout=10)
            if resp.status_code != 200:
                return ""
            data = (resp.json() or {}).get("data") or []
        except Exception:
            return ""
        return next((m.get("id") for m in data if isinstance(m, dict) and m.get("id")), "")

    def _launch_config_ui(self) -> str:
        """설정 UI 가 안 떠 있으면 창 없이 띄운다(config.json 의 ninfer.config_ui_script)."""
        script = self.config_ui_script
        if not script:
            return ""
        if not os.path.isfile(script):
            return f"ninfer.config_ui_script does not exist: {script}"
        exe = shutil.which("pythonw") or shutil.which("python") or sys.executable
        flags = 0
        if os.name == "nt":
            # ComfyUI 를 꺼도 설정 UI(와 그 아래의 서버)는 살아 있어야 한다.
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen(
                [exe, script], cwd=os.path.dirname(os.path.dirname(script)),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True,
            )
        except OSError as exc:
            return f"could not start the NInfer config UI - {exc}"
        deadline = time.time() + CONFIG_UI_BOOT_S
        while time.time() < deadline:
            if self._state() is not None:
                return "started the NInfer config UI"
            time.sleep(0.5)
        return f"started the NInfer config UI but it did not answer within {CONFIG_UI_BOOT_S}s"

    # -- 생성 -----------------------------------------------------------------

    def _generate(self, req: LLMRequest) -> LLMResponse:
        if not self.managed():
            return super()._generate(req)
        started = time.time()
        notes: list = []
        error, model_id = self._ensure_running(req, notes)
        if error:
            return LLMResponse(
                status=error, duration_s=time.time() - started,
                raw_debug=truncate_debug("\n".join(notes)),
            )
        if model_id:
            req = replace(req, model=model_id)
        response = super()._generate(req)
        if notes:
            response.raw_debug = truncate_debug("\n".join(notes + [response.raw_debug]).strip())
        response.duration_s = time.time() - started
        return response

    def _ensure_running(self, req: LLMRequest, notes: list) -> tuple:
        """서버가 원하는 artifact 로 떠 있게 만든다. 반환: (오류 status 또는 "", 공개 모델 ID)."""
        choice = (req.model or "").strip()
        wanted = choice if choice.lower().endswith(ARTIFACT_SUFFIX) else ""

        state = self._state()
        if state is None:
            launched = self._launch_config_ui()
            if launched:
                notes.append(f"ninfer: {launched}")
            state = self._state()
        if state is None:
            if self._health():
                notes.append(
                    f"ninfer: the config UI ({self.config_ui_url}) is not running, so the server that is "
                    "already up is used as it is (it cannot be switched or stopped from here)"
                )
                return "", self._public_id()
            return (
                "error: NInfer is not running and its config UI "
                f"({self.config_ui_url}) is not reachable. Start the NInfer config UI once "
                "(or set ninfer.config_ui_script in config.json so the node starts it for you)"
            ), ""

        status = state.get("status")
        running = ((state.get("run") or {}).get("artifact") or "").strip()

        if status == "external":
            notes.append(
                f"ninfer: a server the config UI did not start is answering on port {state.get('port')} "
                "- using it as it is (it will not be switched or stopped)"
            )
            return "", self._public_id()

        if status in ("running", "starting") and running and wanted and running != wanted:
            emit(req, f"ninfer: switching {running} -> {wanted}...")
            code, data = self._ui("POST", "/api/stop", {}, STOP_TIMEOUT_S)
            if code != 200:
                return (
                    f"error: ninfer - could not stop '{running}' to switch to '{wanted}' - "
                    f"{data.get('code') or data.get('error') or code}"
                ), ""
            notes.append(f"ninfer: stopped '{running}' to switch to '{wanted}'")
            status = "stopped"

        if status in ("running", "starting"):
            if running:
                _LAST_ARTIFACT["name"] = running
            if status == "starting":
                error = self._wait_until_ready(req, running or wanted, notes, time.time())
                if error:
                    return error, ""
            return "", self._public_id()

        # stopped / failed -> 띄운다.
        artifact = wanted or self.default_artifact or _LAST_ARTIFACT["name"]
        if not artifact:
            available = self._artifacts()
            if not available:
                return (
                    "error: ninfer - no .ninfer model was found in the config UI's model folders"
                ), ""
            artifact = available[0]
            notes.append(
                f"ninfer: no model chosen -> starting '{artifact}' (pick one in server_model, "
                "or set ninfer.default_artifact in config.json)"
            )
        error = self._start(req, artifact, notes)
        if error:
            return error, ""
        return "", self._public_id()

    def _start(self, req: LLMRequest, artifact: str, notes: list) -> str:
        emit(req, f"ninfer: starting {artifact}...")
        started = time.time()
        freed = False
        force = False
        while True:
            body = {"artifact": artifact}
            if force:
                body["force"] = True
            code, data = self._ui("POST", "/api/start", body, START_TIMEOUT_S)
            if code == 200:
                break
            error = data.get("error") or ""
            if code == 409 and error == "already_running":
                break  # 누가 먼저 띄웠다. 아래에서 준비될 때까지 기다린다.
            if code == 409 and error == "preflight":
                verdict = data.get("preflight") or {}
                if not freed and free_comfy_vram():
                    freed = True
                    notes.append("ninfer: not enough free VRAM -> unloaded ComfyUI's cached models first")
                    continue
                if verdict.get("level") == "warn" and not force:
                    force = True
                    notes.append(
                        f"ninfer: VRAM is tight ({_vram_line(verdict)}) - started anyway; "
                        "NInfer may run slower if it spills into shared memory"
                    )
                    continue
                return f"error: ninfer - not enough free VRAM to start '{artifact}' ({_vram_line(verdict)})"
            if code is None:
                return f"error: ninfer - the config UI ({self.config_ui_url}) stopped answering"
            reason = data.get("message") or error or f"HTTP {code}"
            return f"error: ninfer - the config UI could not start '{artifact}' - {reason}"
        _LAST_ARTIFACT["name"] = artifact
        return self._wait_until_ready(req, artifact, notes, started)

    def _wait_until_ready(self, req: LLMRequest, artifact: str, notes: list, started: float) -> str:
        """/health 가 200 이 될 때까지 기다린다. 실패·중지·시간 초과면 오류 status."""
        node_id = getattr(req.emitter, "node_id", None)
        deadline = started + self.start_timeout_s
        while time.time() < deadline:
            if cancel.is_stopped(node_id):
                return "stopped: cancelled while NInfer was starting"
            state = self._state() or {}
            status = state.get("status")
            progress = state.get("progress") or {}
            if status == "running":
                notes.append(f"ninfer: '{artifact}' is up ({time.time() - started:.0f}s)")
                return ""
            if status == "failed":
                line = progress.get("failure_line") or "see the config UI log"
                return f"error: ninfer - '{artifact}' failed to start - {line}"
            if status == "stopped" and time.time() - started > STOPPED_GRACE_S:
                tail = [line for line in state.get("log_tail") or [] if str(line).strip()]
                last = tail[-1].strip() if tail else "see the config UI log"
                return f"error: ninfer - '{artifact}' exited while starting - {last}"
            emit(req, f"ninfer: loading {artifact}... {_progress_line(progress, time.time() - started)}")
            time.sleep(POLL_S)
        return (
            f"error: ninfer - '{artifact}' was not ready after {self.start_timeout_s}s "
            "(raise ninfer.start_timeout_s in config.json if the model is on a slow disk)"
        )

    # -- 언로드 = 서버 종료 ------------------------------------------------------

    def unload_model(self, model_id: str) -> str:
        if not self.managed():
            return super().unload_model(model_id)
        state = self._state()
        if state is None:
            return (
                f"unload: the NInfer config UI ({self.config_ui_url}) is not reachable, so the server "
                "was not stopped - stop it from the config UI or end the ninfer-serve process"
            )
        status = state.get("status")
        if status in ("stopped", "failed"):
            return "unload: NInfer is already stopped"
        if status == "external":
            return (
                "unload: the NInfer server on this port was not started by the config UI, so it was "
                "not stopped - end that ninfer-serve process to free the VRAM"
            )
        code, data = self._ui("POST", "/api/stop", {}, STOP_TIMEOUT_S)
        if code == 200:
            return "unload: NInfer server stopped - VRAM freed (the next run starts it again)"
        reason = data.get("code") or data.get("error") or (f"HTTP {code}" if code else "no answer")
        return f"unload: NInfer was not stopped - {reason}"


# ---------------------------------------------------------------------------
# 도우미
# ---------------------------------------------------------------------------


def emit(req: LLMRequest, message: str) -> None:
    if req.emitter is not None:
        try:
            req.emitter.set_status(message)
        except Exception:
            pass


def free_comfy_vram() -> bool:
    """ComfyUI 가 캐시해 둔 모델을 VRAM 에서 내린다. ComfyUI 밖이면 False."""
    try:
        import comfy.model_management as mm

        mm.unload_all_models()
        mm.soft_empty_cache()
        return True
    except Exception:
        return False


def _gib(value) -> str:
    return f"{value / _GIB:.1f} GiB" if isinstance(value, (int, float)) else "?"


def _vram_line(verdict: dict) -> str:
    return f"free {_gib(verdict.get('free_bytes'))}, needs {_gib(verdict.get('needed_bytes'))}"


def _progress_line(progress: dict, elapsed: float) -> str:
    stage = str(progress.get("stage") or "").replace("_", " ")
    percent = progress.get("load_percent")
    parts = [f"{elapsed:.0f}s"]
    if stage:
        parts.append(stage)
    if isinstance(percent, (int, float)):
        parts.append(f"{percent:.0f}%")
    return " / ".join(parts)


def list_artifacts(timeout_s: float = 1.5) -> list:
    """server_model 드롭다운에 넣을 .ninfer 이름들. 설정 UI 가 꺼져 있으면 빈 리스트.

    서버가 꺼져 있어도 모델을 고를 수 있어야 실행 버튼 하나로 띄울 수 있다.
    """
    if os.environ.get("LLMHUB_SKIP_MODEL_PROBE"):
        return []
    import requests

    try:
        from ..utils.config import load_config

        section = load_config().get("ninfer", {}) or {}
        url = (
            os.environ.get(CONFIG_UI_ENV) or section.get("config_ui_url") or DEFAULT_CONFIG_UI_URL
        ).rstrip("/")
        if not is_loopback(url):
            return []
        resp = requests.get(url + "/api/models", timeout=timeout_s)
        if resp.status_code != 200:
            return []
        models = (resp.json() or {}).get("models") or []
    except Exception:
        return []
    return [
        m.get("name") for m in models
        if isinstance(m, dict) and m.get("name") and m.get("supported", True)
    ]


__all__ = ["NInferBackend", "list_artifacts"]
