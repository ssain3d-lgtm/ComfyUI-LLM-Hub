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

from ..utils import fs_tools
from ..utils.config import load_config
from .base import (
    BaseBackend,
    LLMRequest,
    LLMResponse,
    detect_rate_limit,
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
        if req.file_access:
            payload["tools"] = fs_tools.TOOL_SCHEMAS
            payload["tool_choice"] = "auto"
        return payload

    # -- 메인 ---------------------------------------------------------------

    def generate(self, req: LLMRequest) -> LLMResponse:
        started = time.time()
        debug_notes = []

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

    def _run_loop(self, req: LLMRequest, messages: list, model: str):
        """툴 루프 (DESIGN §8.1). 반환: (text, debug_notes) 또는 (LLMResponse, notes)."""
        notes = []
        retried_with_model = False
        last_text = ""

        for iteration in range(self.max_iters):
            payload = self._build_payload(req, messages, model)
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
