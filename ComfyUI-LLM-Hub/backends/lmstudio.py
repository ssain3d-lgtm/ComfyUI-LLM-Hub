# -*- coding: utf-8 -*-
"""LM Studio 백엔드 (OpenAI 호환 HTTP) — DESIGN §8.1.

file_access=True 이면 노드가 직접 제공하는 list_dir/read_file 툴 루프를 돌린다.
네이티브 MCP(/api/v1/chat + integrations)는 v1.5 예정.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import replace

from ..utils import fs_tools
from ..utils.config import load_config
from .base import (
    BaseBackend,
    LLMRequest,
    LLMResponse,
    detect_rate_limit,
    frames_for_unsupported_video,
    tail_lines,
    truncate_debug,
    validate_workspace,
    workspace_hint,
)

CONNECT_ERROR_MSG = "error: LM Studio 서버 응답 없음 (127.0.0.1:1234 실행/포트 확인)"


class LMStudioBackend(BaseBackend):
    name = "lmstudio"

    def __init__(self, config: dict = None):
        self.config = config or load_config()
        ls = self.config.get("lmstudio", {}) or {}
        self.base_url = (ls.get("base_url") or "http://127.0.0.1:1234").rstrip("/")
        self.api_token = ls.get("api_token") or ""
        self.default_model = ls.get("default_model") or ""
        self.max_iters = int(self.config.get("tool_loop_max_iters", 8) or 8)
        self.max_file_read_bytes = int(self.config.get("max_file_read_bytes", 262144) or 262144)
        self.default_ttl_sec = int(ls.get("ttl_sec", 0) or 0)
        self.default_unload_after = bool(ls.get("unload_after", False))

    # -- HTTP ---------------------------------------------------------------

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        # api_token 은 debug 출력에 절대 싣지 않는다 (DESIGN §10).
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def _post_chat(self, payload: dict, timeout_s: int):
        import requests

        url = f"{self.base_url}/v1/chat/completions"
        return requests.post(url, headers=self._headers(), json=payload, timeout=timeout_s)

    def _first_loaded_model(self, timeout_s: int) -> str:
        """/v1/models 에서 첫 번째 모델 id 를 얻는다 (모델 미지정 대비, DESIGN §5)."""
        import requests

        try:
            resp = requests.get(
                f"{self.base_url}/v1/models", headers=self._headers(), timeout=min(timeout_s, 30)
            )
            if resp.status_code != 200:
                return ""
            data = resp.json().get("data") or []
            if data:
                return data[0].get("id") or ""
        except Exception:
            return ""
        return ""

    # -- 메시지 구성 ---------------------------------------------------------

    def _build_messages(self, req: LLMRequest) -> list:
        messages = []

        system_parts = []
        if (req.system_prompt or "").strip():
            system_parts.append(req.system_prompt.strip())
        hint = workspace_hint(req)
        if hint:
            system_parts.append(hint)
        if system_parts:
            messages.append({"role": "system", "content": "\n".join(system_parts)})

        if req.image_paths:
            content = [{"type": "text", "text": req.user_prompt}]
            for path in req.image_paths:
                data_uri = _png_data_uri(path)
                if data_uri:
                    content.append({"type": "image_url", "image_url": {"url": data_uri}})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": req.user_prompt})

        return messages

    def _build_payload(self, req: LLMRequest, messages: list, model: str) -> dict:
        payload = {
            "messages": messages,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
            "stream": False,
        }
        if model:
            payload["model"] = model
        # 유휴 TTL(초). LM Studio 가 이 시간 동안 요청이 없으면 VRAM 에서 모델을 내린다.
        # OpenAI 호환 엔드포인트가 ttl 필드를 받는다(공식 문서 확인).
        # 요청이 지정하지 않았으면(None) config.json 의 lmstudio.ttl_sec 를 쓴다.
        ttl = self.default_ttl_sec if req.ttl_sec is None else req.ttl_sec
        if ttl and ttl > 0:
            payload["ttl"] = int(ttl)
        if req.file_access:
            payload["tools"] = fs_tools.TOOL_SCHEMAS
            payload["tool_choice"] = "auto"
        return payload

    # -- 메인 ---------------------------------------------------------------

    def generate(self, req: LLMRequest) -> LLMResponse:
        """생성 후 요청에 따라 VRAM 에서 모델을 내린다."""
        response = self._generate(req)
        unload = (
            self.default_unload_after if req.unload_after is None else req.unload_after
        )
        if unload:
            note = self.unload_model(self._served_model or (req.model or "").strip())
            if note:
                response.raw_debug = truncate_debug(
                    (response.raw_debug + "\n" + note).strip()
                )
        return response

    def unload_model(self, model_id: str) -> str:
        """`lms unload` 로 모델을 VRAM 에서 즉시 내린다.

        lms CLI 가 없으면 조용히 실패하고 안내만 남긴다 — TTL 이 백업 역할을 한다.
        반환값: debug 에 남길 안내 문구.
        """
        from ..utils.proc import CliNotFoundError, resolve_cli, run_cli

        if not model_id:
            return "unload: 대상 모델을 알 수 없어 건너뜀 (TTL 만료 시 자동 해제됨)"

        try:
            exe = resolve_cli("lms")
        except CliNotFoundError:
            return (
                "unload: lms CLI 를 찾을 수 없어 즉시 언로드를 건너뜀. "
                "LM Studio 설치 폴더의 lms 를 PATH 에 추가하거나 config.json 의 "
                "cli_paths.lms 에 절대경로를 넣으세요. (TTL 만료 시에는 자동 해제됩니다)"
            )

        code, _stdout, stderr, _dur = run_cli(
            [exe, "unload", model_id], cwd=None, stdin_text=None, timeout_s=60
        )
        if code == 0:
            return f"unload: '{model_id}' 를 VRAM 에서 내렸습니다"
        return f"unload: 실패(exit {code}) — {tail_lines(stderr, 3)}"

    def _generate(self, req: LLMRequest) -> LLMResponse:
        started = time.time()
        debug_notes = []
        self._served_model = ""

        ws_error = validate_workspace(req)
        if ws_error:
            return LLMResponse(status=ws_error, duration_s=time.time() - started)

        if req.mcp_config:
            debug_notes.append("lmstudio: mcp_config는 v1.5 예정, 내장 툴 루프 사용")
        if req.extra_args:
            debug_notes.append("lmstudio: extra_args 는 HTTP 백엔드에서 무시됨")

        try:
            import requests  # noqa: F401
        except ImportError:
            return LLMResponse(
                status="error: requests 패키지가 없습니다 (install.bat 실행 또는 pip install requests)",
                duration_s=time.time() - started,
                raw_debug="\n".join(debug_notes),
            )

        model = (req.model or "").strip() or self.default_model

        # OpenAI 호환 chat/completions 에는 비디오 콘텐츠 타입이 없다.
        # → 프레임을 뽑아 이미지로 넣는다 (VLM 모델 필요).
        if req.video_paths:
            frames, video_notes = frames_for_unsupported_video(req, "lmstudio")
            debug_notes.extend(video_notes)
            if frames:
                req = replace(req, image_paths=list(req.image_paths or []) + frames)

        messages = self._build_messages(req)

        try:
            text, notes = self._run_loop(req, messages, model)
        except Exception as exc:  # 모든 예외를 status 로 변환 (DESIGN §6)
            return self._map_exception(exc, started, debug_notes)

        debug_notes.extend(notes)
        duration = time.time() - started

        if isinstance(text, LLMResponse):  # _run_loop 가 오류 응답을 그대로 돌려준 경우
            text.duration_s = duration
            text.raw_debug = truncate_debug("\n".join(debug_notes + [text.raw_debug]))
            return text

        return LLMResponse(
            text=(text or "").strip(),
            status="ok" if (text or "").strip() else "error: 빈 응답 (모델이 텍스트를 내지 않음)",
            duration_s=duration,
            raw_debug=truncate_debug("\n".join(debug_notes)),
        )

    def _stream_chat(self, req: LLMRequest, payload: dict):
        """SSE 스트리밍으로 토큰을 받아 모니터링 창에 흘린다.

        반환: (resp, text) — 200 이 아니면 text 는 None 이라 호출부가 폴백한다.
        """
        import requests

        payload = dict(payload)
        payload["stream"] = True
        resp = requests.post(
            f"{self.base_url}/v1/chat/completions",
            headers=self._headers(), json=payload,
            timeout=req.timeout_s, stream=True,
        )
        if resp.status_code != 200:
            return resp, None

        # SSE 응답에는 charset 이 없는 경우가 많은데, 그러면 requests 가
        # ISO-8859-1 로 디코딩해서 한글이 깨진다(테스트로 확인).
        # → UTF-8 을 명시한다.
        resp.encoding = "utf-8"

        # stream=True 일 때 requests 의 timeout 은 "청크 사이 간격"만 재기 때문에
        # 모델이 계속 토큰을 뱉으면 timeout_s 가 전체 시간을 못 막는다.
        # → 벽시계 기준 상한을 직접 건다.
        deadline = time.time() + req.timeout_s

        text = ""
        for raw in resp.iter_lines(decode_unicode=True):
            if time.time() > deadline:
                resp.close()
                req.emitter.set_status(f"timeout({req.timeout_s}s) — 받은 부분까지만 사용")
                break
            if not raw:
                continue
            if raw.startswith("data:"):
                raw = raw[5:].strip()
            if raw == "[DONE]":
                break
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if obj.get("model"):
                self._served_model = obj["model"]
            for choice in obj.get("choices") or []:
                piece = (choice.get("delta") or {}).get("content") or ""
                if piece:
                    text += piece
                    req.emitter.append(piece)
        return resp, text

    def _run_loop(self, req: LLMRequest, messages: list, model: str):
        """툴 루프 (DESIGN §8.1). 반환: (text, debug_notes) 또는 (LLMResponse, notes)."""
        notes = []
        retried_with_model = False
        last_text = ""

        # 툴을 선언한 요청(file_access=True)은 delta 로 오는 tool_calls 조립이
        # 까다로워 스트리밍하지 않는다. 대신 도구 진행 상황을 status 로 보여준다.
        streaming = (
            req.emitter is not None and req.emitter.enabled and not req.file_access
        )

        for iteration in range(self.max_iters):
            payload = self._build_payload(req, messages, model)

            if streaming:
                resp, streamed = self._stream_chat(req, payload)
                if streamed is not None:
                    return streamed, notes
                # 200 이 아니면 아래 비스트리밍 경로가 오류/모델 폴백을 처리한다.
                streaming = False
            else:
                resp = self._post_chat(payload, req.timeout_s)

            if resp.status_code != 200:
                body = (resp.text or "")[:1000]
                # 모델 미지정/오지정으로 실패하면 /v1/models 첫 모델로 한 번만 재시도한다.
                if not retried_with_model and _looks_like_model_error(resp.status_code, body):
                    fallback = self._first_loaded_model(req.timeout_s)
                    if fallback:
                        retried_with_model = True
                        model = fallback
                        notes.append(f"lmstudio: 모델 미지정 → /v1/models 의 '{fallback}' 사용")
                        continue
                status = "rate_limited" if detect_rate_limit(body) else f"error: LM Studio HTTP {resp.status_code}"
                return (
                    LLMResponse(status=status, raw_debug=f"HTTP {resp.status_code}\n{body}"),
                    notes,
                )

            data = resp.json()
            # 실제로 어떤 모델이 응답했는지 기록해 둔다(언로드 대상 파악용).
            if data.get("model"):
                self._served_model = data["model"]
            choices = data.get("choices") or []
            if not choices:
                return (
                    LLMResponse(
                        status="error: LM Studio 응답에 choices 가 없음",
                        raw_debug=json.dumps(data, ensure_ascii=False)[:2000],
                    ),
                    notes,
                )

            message = choices[0].get("message") or {}
            last_text = message.get("content") or last_text
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                return message.get("content") or "", notes

            # 툴 호출 결과를 role="tool" 메시지로 붙여 재요청한다.
            messages.append(_assistant_tool_message(message, tool_calls))
            for call in tool_calls:
                fn = (call.get("function") or {})
                fname = fn.get("name") or ""
                args = _parse_tool_args(fn.get("arguments"))
                result = fs_tools.dispatch_tool(
                    fname, args, req.workspace_dir, self.max_file_read_bytes
                )
                notes.append(f"tool[{iteration}] {fname}({args.get('path','')}) -> {len(result)} chars")
                if req.emitter is not None:
                    req.emitter.set_status(f"도구 사용: {fname}({args.get('path', '')})")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or f"call_{iteration}",
                        "name": fname,
                        "content": result,
                    }
                )

        notes.append("tool loop limit")
        return last_text or "", notes

    def _map_exception(self, exc: Exception, started: float, debug_notes: list) -> LLMResponse:
        import requests

        duration = time.time() - started
        if isinstance(exc, (requests.ConnectionError,)):
            status = CONNECT_ERROR_MSG
        elif isinstance(exc, requests.Timeout):
            status = f"error: timeout({int(duration)}s) — LM Studio 응답 지연"
        else:
            status = f"error: {type(exc).__name__}: {exc}"
        return LLMResponse(
            status=status,
            duration_s=duration,
            raw_debug=truncate_debug("\n".join(debug_notes + [repr(exc)])),
        )


# ---------------------------------------------------------------------------


def _png_data_uri(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            encoded = base64.b64encode(fh.read()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except OSError:
        return ""


def _parse_tool_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _assistant_tool_message(message: dict, tool_calls: list) -> dict:
    """툴 호출을 요청한 assistant 메시지를 대화에 그대로 되돌려 넣는다."""
    return {
        "role": "assistant",
        "content": message.get("content") or "",
        "tool_calls": tool_calls,
    }


def _looks_like_model_error(status_code: int, body: str) -> bool:
    if status_code not in (400, 404, 422):
        return False
    lowered = (body or "").lower()
    return "model" in lowered


# ---------------------------------------------------------------------------
# 노드 드롭다운용 모델 목록
# ---------------------------------------------------------------------------

_MODEL_CACHE = {"at": 0.0, "ids": []}
_MODEL_CACHE_TTL = 10.0  # 초. INPUT_TYPES 가 자주 불려도 서버를 계속 두드리지 않게.


def list_model_ids(timeout_s: float = 1.5) -> list:
    """LM Studio 에 있는 모델 id 목록을 돌려준다 (실패하면 빈 리스트).

    ComfyUI 가 INPUT_TYPES 를 부를 때마다 호출되므로 짧은 타임아웃과 캐시를 쓴다.
    LM Studio 가 꺼져 있어도 ComfyUI 가 멈추면 안 된다.

    /api/v0/models 는 state("loaded"/"not-loaded")까지 주지만 버전에 따라
    로드된 모델만 돌려주는 이슈가 있어 /v1/models 결과와 합친다.
    """
    now = time.time()
    if now - _MODEL_CACHE["at"] < _MODEL_CACHE_TTL:
        return list(_MODEL_CACHE["ids"])

    ids = []
    try:
        import requests

        cfg = load_config().get("lmstudio", {}) or {}
        base = (cfg.get("base_url") or "http://127.0.0.1:1234").rstrip("/")
        headers = {}
        if cfg.get("api_token"):
            headers["Authorization"] = f"Bearer {cfg['api_token']}"

        for path, extract in (
            ("/api/v0/models", _ids_from_v0),
            ("/v1/models", _ids_from_v1),
        ):
            try:
                resp = requests.get(base + path, headers=headers, timeout=timeout_s)
                if resp.status_code == 200:
                    for model_id in extract(resp.json()):
                        if model_id and model_id not in ids:
                            ids.append(model_id)
            except Exception:
                continue
    except Exception:
        ids = []

    _MODEL_CACHE["at"] = now
    _MODEL_CACHE["ids"] = ids
    return list(ids)


def _ids_from_v0(payload) -> list:
    """/api/v0/models — 임베딩 모델은 텍스트 생성에 못 쓰므로 제외한다."""
    out = []
    for item in (payload or {}).get("data") or []:
        if item.get("type") == "embeddings":
            continue
        out.append(item.get("id") or "")
    return out


def _ids_from_v1(payload) -> list:
    return [item.get("id") or "" for item in (payload or {}).get("data") or []]
