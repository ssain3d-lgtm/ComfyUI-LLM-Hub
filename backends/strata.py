# -*- coding: utf-8 -*-
"""Strata 백엔드: 실행 버튼 → (꺼져 있으면) 서버 기동 → (내려가 있으면) 모델 적재 → 생성 → (원하면) 언로드.

Strata(https://github.com/Niko1221/Strata)는 Qwen3.8-Flash-Next 를 게이밍 PC 한 대에서 돌리는 엔진이다.
`serve/server.py` 가 OpenAI 호환 API 를 내고, 기본 포트는 llama.cpp 와 같은 8080 이다. 그래서 포트만으로는
둘을 못 가른다 -- /health 의 "service": "strata" 로 가른다.

NInfer 와 달리 서버 자체에 모델을 올리고 내리는 API 가 있다 (serve/server.py 0.1.41 에서 읽었다):
  GET  /health   {"service": "strata", "model": <이름>, "loaded": bool}  모델이 다 올라와야 답한다
  POST /unload   {}  엔진에 QUIT -> VRAM 과 고정 RAM 을 돌려준다. 서버 프로세스는 남아서 계속 듣는다.
                 200 {"status": "unloaded" | "not loaded" | "unsupported"}, 409 {"status": "busy"}
  POST /load     {}  엔진을 다시 띄운다(1~3분, 끝날 때까지 답하지 않는다). 503 = 남이 VRAM 을 쥐고 있다.
  둘 다 Content-Type: application/json 이고 브라우저 Origin 이 없어야 받는다 -- requests 가 그렇게 보낸다.
  요청의 model 은 아무 이름이나 받고, 답에는 서버의 이름(설정의 model_name)이 실린다.

그래서 여기서 언로드는 진짜 언로드다: 프로세스는 두고 엔진만 내린다. 서버가 아예 꺼져 있을 때만 프로세스를
띄운다 -- Strata 의 MCP 도구(tools/strata_mcp.py)가 띄우는 것과 같은 명령이다:
  <root>/.venv/Scripts/python.exe <root>/serve/server.py --engine strata --config <root>/strata-<모델>.json --port 8080
`strata.root` 가 비어 있으면 띄우지 않고 어디서 띄울지만 말한다. 출력은 노드팩 폴더의 strata-server.log 에 쌓는다.

모델 바꾸기: 서버에는 모델을 바꾸는 API 가 없다. 이 노드가 띄운 프로세스면 내리고(/unload) 끝낸 뒤 고른
설정으로 다시 띄운다. 남이 띄운 서버(run-<모델>.bat 창)는 건드리지 않고, 그렇다고 말한다.

관리는 "이 PC 의 Strata 포트(기본 8080)" 를 쓸 때만 한다. openai_base_url 에 다른 주소를 적었으면 그냥 OpenAI
호환 서버로 다룬다. 언로드만은 Strata 자신의 API 라 어느 주소든 /unload 를 보낸다 -- 드롭다운에서 strata 를
골랐다는 것이 그 주소가 Strata 라는 뜻이다.

실측(2026-10-10, 엔진 0.1.41, qwen3.8-flash-next-iq3_xxs, RTX 5090): 생성 -> /unload -> /load -> 생성이 그대로 돌았고,
다시 올리는 데 18초. 서버를 띄우는 것과 모델 바꾸기는 가짜 서버로만 돌려 봤다(tests/test_strata_lifecycle.py).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import replace
from urllib.parse import urlparse

from ..utils import cancel
from .base import LLMRequest, LLMResponse, server_error_reason, tail_lines, truncate_debug
from .lmstudio import is_loopback
from .ninfer import emit, free_comfy_vram
from .openai_compat import KNOWN_SERVERS, OpenAICompatBackend

# 테스트 스위트는 이 변수를 "" 로 세워 둔다(tests/__init__.py). 안 그러면 개발 PC 의 config.json 에 적힌
# Strata 폴더를 읽어 진짜 Strata 를 띄울 수 있다. 값이 있으면(빈 값이어도) config 보다 우선한다.
ROOT_ENV = "LLMHUB_STRATA_ROOT"
DEFAULT_PORT = 8080
# 기동 대기 상한. Strata 문서 기준 모델 적재는 1~3분, SSD 가 아니면 더 걸린다.
DEFAULT_START_TIMEOUT_S = 600
POLL_S = 1.0
HEALTH_TIMEOUT_S = 2
# /unload 는 엔진이 끝날 때까지 기다렸다 답한다. Strata 의 MCP 도구도 120초를 준다.
UNLOAD_TIMEOUT_S = 120
STOP_WAIT_S = 30
CONFIG_PREFIX = "strata-"
# serve/server.py 가 설정에 model_name 이 없을 때 쓰는 이름. /health 와 /v1/models 에 이 이름이 나온다.
DEFAULT_MODEL_NAME = "qwen3.8-flash-next"
_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_LOG = os.path.join(_PACK_ROOT, "strata-server.log")

# 이 노드가 띄운 서버 프로세스. 모델을 바꿀 때 끝내도 되는 것은 이것뿐이다 -- 사용자가 창에서 띄운
# Strata 를 여기서 죽이면 안 된다. ComfyUI 를 다시 켜면 비므로, 그 뒤로는 그 서버도 남이 띄운 것으로 본다.
_STARTED = {"proc": None, "config": ""}


class StrataBackend(OpenAICompatBackend):
    name = "strata"

    def __init__(self, config: dict = None):
        super().__init__(config=config, base_url_default=KNOWN_SERVERS["strata"])
        section = self.config.get("strata", {}) or {}
        root = os.environ[ROOT_ENV] if ROOT_ENV in os.environ else section.get("root")
        self.root = (root or "").strip()
        self.default_config = (section.get("model") or "").strip()
        self.auto_start = bool(section.get("auto_start", True))
        self.port = int(section.get("port") or DEFAULT_PORT)
        self.start_timeout_s = int(section.get("start_timeout_s") or DEFAULT_START_TIMEOUT_S)
        self.free_comfy_before_load = bool(section.get("free_comfy_vram", True))

    # -- 관리 대상인가 --------------------------------------------------------

    def managed(self) -> bool:
        """띄우고 올리고 내릴 서버인가. 이 PC 의 Strata 포트일 때만 그렇다."""
        if not self.auto_start or not is_loopback(self.base_url):
            return False
        try:
            return urlparse(self.base_url).port == self.port
        except ValueError:
            return False

    # -- 서버 HTTP -------------------------------------------------------------

    def _health(self):
        """GET /health. 반환: 서버의 JSON(dict). 아무도 안 들으면 None."""
        import requests

        try:
            resp = requests.get(self.base_url + "/health", timeout=HEALTH_TIMEOUT_S)
        except Exception:
            return None
        try:
            data = resp.json()
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    # -- 설정 파일 -------------------------------------------------------------

    def _config_for(self, name: str) -> str:
        """이름이 가리키는 strata-<모델>.json. <모델> 태그나 model_name 둘 다 받는다. 없으면 ""."""
        if not name:
            return ""
        for path in list_configs(self.root):
            if name in (_tag(path), _model_name(path)):
                return path
        return ""

    def _python(self) -> str:
        if os.name == "nt":
            return os.path.join(self.root, ".venv", "Scripts", "python.exe")
        return os.path.join(self.root, ".venv", "bin", "python")

    # -- 생성 -----------------------------------------------------------------

    def _generate(self, req: LLMRequest) -> LLMResponse:
        notes: list = []
        note = self.reasoning_note(req)
        if note:
            notes.append(note)
        if not self.managed():
            response = super()._generate(req)
            if notes:
                response.raw_debug = truncate_debug("\n".join(notes + [response.raw_debug]).strip())
            return response
        started = time.time()
        error, model_id = self._ensure_loaded(req, notes)
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

    def _ensure_loaded(self, req: LLMRequest, notes: list) -> tuple:
        """서버가 떠 있고 모델이 올라와 있게 만든다. 반환: (오류 status 또는 "", 요청에 실을 모델 이름)."""
        wanted = (req.model or "").strip()
        target = self._config_for(wanted)
        health = self._health()

        if health is None:
            if not self.root:
                return (
                    f"error: Strata is not running at {self.base_url}. Start it (run-<model>.bat in the "
                    "Strata folder), or set strata.root in config.json so the node starts it for you"
                ), ""
            config = target
            if not config and self.default_config:
                config = self._config_for(self.default_config)
                if not config:
                    return (
                        f"error: strata - strata.model '{self.default_config}' matches no "
                        f"strata-<model>.json in {self.root}"
                    ), ""
            if not config:
                configs = list_configs(self.root)
                if not configs:
                    return f"error: strata - no strata-<model>.json in {self.root} (run Strata's setup first)", ""
                config = configs[0]
                notes.append(
                    f"strata: {'no model chosen' if not wanted else repr(wanted) + ' matches no strata-<model>.json'}"
                    f" -> starting '{_tag(config)}' (the most recently used; pick one in server_model, "
                    "or set strata.model in config.json)"
                )
            error, health = self._start(req, config, notes)
            if error:
                return error, ""
            return "", (wanted or health.get("model") or "")

        if health.get("service") != "strata":
            return (
                f"error: strata - something else answers at {self.base_url} (not Strata; llama.cpp uses "
                "port 8080 too). Stop it, or run Strata on another port and put that address in openai_base_url"
            ), ""

        running = (health.get("model") or "").strip()
        if target and _model_name(target) != running:
            if not self._owned():
                return (
                    f"error: strata - '{running}' is running at {self.base_url} and was not started by this "
                    f"ComfyUI session, so it is not switched to '{wanted}'. Pick '{running}' in server_model "
                    "(or (auto)), or stop that Strata and run again"
                ), ""
            emit(req, f"strata: stopping '{running}' to switch to '{wanted}'...")
            self._stop_owned()
            notes.append(f"strata: stopped '{running}' to switch to '{wanted}'")
            error, _health = self._start(req, target, notes)
            return error, wanted

        if not health.get("loaded"):
            error = self._load(req, notes)
            if error:
                return error, ""
        return "", (wanted or running)

    def _start(self, req: LLMRequest, config: str, notes: list) -> tuple:
        """serve/server.py 를 띄우고 모델이 올라올 때까지 기다린다. 반환: (오류 status 또는 "", /health)."""
        tag = _tag(config)
        python = self._python()
        if not os.path.isfile(python):
            return f"error: strata - {python} does not exist (run Strata's START-HERE.bat once to create .venv)", {}
        server = os.path.join(self.root, "serve", "server.py")
        if not os.path.isfile(server):
            return f"error: strata - {server} does not exist (is strata.root the Strata folder?)", {}
        if self.free_comfy_before_load and free_comfy_vram():
            notes.append("strata: unloaded ComfyUI's cached models before starting (they load again when needed)")
        cmd = [python, server, "--engine", "strata", "--config", config, "--port", str(self.port)]
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        flags = 0
        if os.name == "nt":
            # ComfyUI 를 꺼도 Strata 는 살아 있어야 한다. 창은 띄우지 않는다.
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            with open(SERVER_LOG, "ab") as log:
                log.write(f"\n===== LLM Hub: start {tag} on port {self.port} at "
                          f"{time.strftime('%Y-%m-%d %H:%M:%S')} =====\n".encode("utf-8"))
                proc = subprocess.Popen(
                    cmd, cwd=self.root, env=env, stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, creationflags=flags, start_new_session=(os.name != "nt"),
                    close_fds=True,
                )
        except OSError as exc:
            return f"error: strata - could not start Strata - {exc}", {}
        _STARTED["proc"] = proc
        _STARTED["config"] = config
        emit(req, f"strata: starting {tag} (1-3 minutes)...")
        return self._wait_until_ready(req, tag, proc, notes)

    def _wait_until_ready(self, req: LLMRequest, tag: str, proc, notes: list) -> tuple:
        node_id = getattr(req.emitter, "node_id", None)
        started = time.time()
        deadline = started + self.start_timeout_s
        while time.time() < deadline:
            if cancel.is_stopped(node_id):
                return "stopped: cancelled while Strata was starting (it keeps loading in the background)", {}
            if proc.poll() is not None:
                _STARTED["proc"] = None
                return (
                    f"error: strata - Strata exited while starting (exit code {proc.returncode}) - {_log_tail()}"
                ), {}
            health = self._health() or {}
            if health.get("service") == "strata" and health.get("loaded"):
                notes.append(f"strata: '{health.get('model')}' is up ({time.time() - started:.0f}s)")
                return "", health
            emit(req, f"strata: loading {tag}... {time.time() - started:.0f}s")
            time.sleep(POLL_S)
        return (
            f"error: strata - {tag} was not ready after {self.start_timeout_s}s "
            f"(raise strata.start_timeout_s in config.json; the server log is {SERVER_LOG})"
        ), {}

    def _load(self, req: LLMRequest, notes: list) -> str:
        """내려가 있는 모델을 POST /load 로 올린다. 끝날 때까지 답이 없으므로 기동 상한만큼 기다린다."""
        if self.free_comfy_before_load and free_comfy_vram():
            notes.append("strata: unloaded ComfyUI's cached models before loading (they load again when needed)")
        emit(req, "strata: loading the model (1-3 minutes)...")
        started = time.time()
        try:
            resp = self._post_json("/load", {}, self.start_timeout_s)
        except Exception as exc:
            return f"error: strata - the model could not be loaded - {type(exc).__name__}"
        if resp.status_code == 200:
            notes.append(f"strata: model loaded ({time.time() - started:.0f}s)")
            return ""
        reason = server_error_reason(resp.text or "") or f"HTTP {resp.status_code}"
        return f"error: strata - the model could not be loaded - {reason}"

    # -- 이 노드가 띄운 프로세스 ----------------------------------------------------

    def _owned(self) -> bool:
        proc = _STARTED["proc"]
        return proc is not None and proc.poll() is None

    def _stop_owned(self) -> None:
        """내리고(/unload) 끝낸다. 모델을 바꿀 때만 쓴다 -- 언로드는 프로세스를 끝내지 않는다."""
        proc = _STARTED["proc"]
        try:
            self._post_json("/unload", {}, UNLOAD_TIMEOUT_S)
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(STOP_WAIT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(STOP_WAIT_S)
        _STARTED["proc"] = None
        _STARTED["config"] = ""

    # -- 언로드 ---------------------------------------------------------------

    def unload_model(self, model_id: str) -> str:
        try:
            resp = self._post_json("/unload", {}, UNLOAD_TIMEOUT_S)
        except Exception as exc:
            return f"unload: Strata did not answer ({type(exc).__name__})"
        try:
            status = (resp.json() or {}).get("status")
        except ValueError:
            status = None
        if resp.status_code == 200 and status == "unloaded":
            return (
                "unload: Strata unloaded the model - VRAM and RAM freed; the server keeps listening and "
                "the next run loads it again"
            )
        if resp.status_code == 200 and status == "not loaded":
            return "unload: Strata's model was already unloaded"
        if resp.status_code == 409:
            return "unload: Strata is busy (a request is running or queued), so the model stays loaded"
        reason = status or server_error_reason(resp.text or "") or f"HTTP {resp.status_code}"
        return f"unload: Strata did not unload the model - {reason}"


# ---------------------------------------------------------------------------
# 도우미
# ---------------------------------------------------------------------------


def _tag(config: str) -> str:
    """strata-iq3_xxs.json -> iq3_xxs. run-<태그>.bat 와 MCP 도구의 model 인자가 쓰는 이름이다."""
    return os.path.basename(config)[len(CONFIG_PREFIX):-len(".json")]


def _model_name(config: str) -> str:
    """설정의 model_name = 서버가 /health 와 /v1/models 에 내는 이름. 못 읽으면 ""."""
    try:
        with open(config, encoding="utf-8-sig") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return ""
    if not isinstance(cfg, dict):
        return ""
    return cfg.get("model_name") or DEFAULT_MODEL_NAME


def _log_tail(lines: int = 3) -> str:
    try:
        with open(SERVER_LOG, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 65536))
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return f"see {SERVER_LOG}"
    return tail_lines(text, lines) or f"see {SERVER_LOG}"


def list_configs(root: str) -> list:
    """<root>/strata-<모델>.json 들, 최근에 쓴 것부터. setup 과 MCP 도구가 '마지막에 쓴 모델' 을 고르는 순서다.

    strata-<모델>.shared-settings.json 은 채팅 설정 파일이라 뺀다. .bak 들은 .json 으로 안 끝나서 안 걸린다.
    """
    if not root:
        return []
    try:
        names = os.listdir(root)
    except OSError:
        return []
    paths = [
        os.path.join(root, name) for name in names
        if name.startswith(CONFIG_PREFIX) and name.endswith(".json")
        and not name.endswith(".shared-settings.json")
    ]

    def modified(path):
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    paths.sort(key=modified, reverse=True)
    return paths


def list_strata_models() -> list:
    """server_model 드롭다운에 넣을 이름들: strata.root 의 설정들이 쓰는 model_name.

    서버가 꺼져 있어도 모델을 고를 수 있어야 실행 버튼 하나로 띄울 수 있다. 서버가 떠 있으면
    /v1/models 가 같은 이름을 주므로 list_server_models 가 합치면서 겹치는 것을 지운다.
    """
    if os.environ.get("LLMHUB_SKIP_MODEL_PROBE"):
        return []
    try:
        from ..utils.config import load_config

        section = load_config().get("strata", {}) or {}
    except Exception:
        return []
    root = os.environ[ROOT_ENV] if ROOT_ENV in os.environ else (section.get("root") or "")
    names = []
    for path in list_configs(root.strip()):
        name = _model_name(path)
        if name and name not in names:
            names.append(name)
    return names


__all__ = ["StrataBackend", "list_strata_models"]
