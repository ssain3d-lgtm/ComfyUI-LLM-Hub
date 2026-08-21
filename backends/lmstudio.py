# -*- coding: utf-8 -*-
"""LM Studio 백엔드 (OpenAI 호환 HTTP) — DESIGN §8.1.

file_access=True 이면 노드가 직접 제공하는 list_dir/read_file 툴 루프를 돌린다.
네이티브 MCP(/api/v1/chat + integrations)는 v1.5 예정.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import replace

from ..utils import cancel, fs_tools
from ..utils.config import load_config, resolve_api_token
from .base import (
    BaseBackend,
    LLMRequest,
    LLMResponse,
    detect_rate_limit,
    format_usage,
    frames_for_unsupported_video,
    merge_extra_body,
    server_error_reason,
    tail_lines,
    truncate_debug,
    validate_workspace,
    workspace_hint,
)

CONNECT_ERROR_MSG = "error: no response from the LM Studio server (check it is running and on port 1234)"


class LMStudioBackend(BaseBackend):
    name = "lmstudio"

    def __init__(self, config: dict = None):
        self.config = config or load_config()
        ls = self.config.get("lmstudio", {}) or {}
        self.base_url = (ls.get("base_url") or "http://127.0.0.1:1234").rstrip("/")
        # config.json 뿐 아니라 환경변수/토큰파일도 본다. 드롭다운만 고치고 여기를
        # 두면 목록은 뜨는데 정작 생성이 401 로 죽는다 — 같은 토큰을 써야 한다.
        self.api_token = resolve_api_token(self.config)
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
        # 시드. 0 은 "안 보냄" 이라 기본 설치의 요청 내용은 예전과 똑같다.
        # OpenAI chat/completions 규격의 필드지만 서버마다 받는지는 다르므로,
        # 400 이 오면 _run_loop 가 시드를 빼고 한 번 재시도한다.
        if req.seed and int(req.seed) > 0:
            payload["seed"] = int(req.seed)
        # 사용자가 적은 것이 마지막에 이긴다(뼈대 키만 제외).
        merge_extra_body(payload, req.extra_body, req.file_access)
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
            return "unload: skipped, the target model is unknown (TTL will release it)"

        try:
            exe = resolve_cli("lms")
        except CliNotFoundError:
            return (
                "unload: the lms CLI was not found, so immediate unload is skipped. "
                "Add LM Studio's lms to PATH, or set an absolute path in config.json "
                "under cli_paths.lms. (TTL still releases the model.)"
            )

        code, _stdout, stderr, _dur = run_cli(
            [exe, "unload", model_id], cwd=None, stdin_text=None, timeout_s=60
        )
        if code == 0:
            return f"unload: '{model_id}' unloaded from VRAM"
        return f"unload: failed (exit {code}) — {tail_lines(stderr, 3)}"

    def _generate(self, req: LLMRequest) -> LLMResponse:
        started = time.time()
        debug_notes = []
        self._served_model = ""

        ws_error = validate_workspace(req)
        if ws_error:
            return LLMResponse(status=ws_error, duration_s=time.time() - started)

        if req.mcp_config:
            debug_notes.append("lmstudio: mcp_config is planned for v1.5; using the built-in tool loop")
        if req.extra_args:
            debug_notes.append(
                f"{self.name}: extra_args is ignored by this HTTP backend "
                "(use extra_body for JSON payload fields)"
            )
        # 실제 병합은 _build_payload 가 매 반복마다 한다. 여기서는 빈 dict 에
        # 같은 규칙을 한 번 돌려 안내 문구만 얻는다(중복 기록 방지).
        debug_notes.extend(merge_extra_body({}, req.extra_body, req.file_access))
        # 스트리밍/비스트리밍 어느 경로로 끝나든 한 곳에서 usage 를 적는다.
        self._last_usage = None

        try:
            import requests  # noqa: F401
        except ImportError:
            return LLMResponse(
                status="error: the requests package is missing (run install.bat, or pip install requests)",
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
        usage_line = format_usage(getattr(self, "_last_usage", None))
        if usage_line:
            debug_notes.append(usage_line)
        duration = time.time() - started

        if isinstance(text, LLMResponse):  # _run_loop 가 오류 응답을 그대로 돌려준 경우
            text.duration_s = duration
            text.raw_debug = truncate_debug("\n".join(debug_notes + [text.raw_debug]))
            return text

        return LLMResponse(
            text=(text or "").strip(),
            status="ok" if (text or "").strip() else "error: empty response (the model produced no text)",
            duration_s=duration,
            raw_debug=truncate_debug("\n".join(debug_notes)),
        )

    def _stream_chat(self, req: LLMRequest, payload: dict):
        """SSE 스트리밍으로 토큰을 받아 모니터링 창에 흘린다.

        반환: (resp, text, timed_out) — 200 이 아니면 text 는 None 이라 호출부가 폴백한다.

        세 값을 꼭 다 돌려줘야 한다. 호출부가 `resp, streamed, timed_out = ...` 로
        받으므로 오류 경로에서 두 개만 돌려주면 ValueError 로 죽는다. 그러면
        "200 이 아니면 비스트리밍 경로가 모델 폴백을 처리한다" 는 설계가 아예
        도달하지 못한다 — unload_after 기본값이 True 라 모델이 매번 내려가고,
        다음 실행이 400 을 받는 이 경로는 일상적으로 밟힌다.
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
            return resp, None, False

        # SSE 응답에는 charset 이 없는 경우가 많은데, 그러면 requests 가
        # ISO-8859-1 로 디코딩해서 한글이 깨진다(테스트로 확인).
        # → UTF-8 을 명시한다.
        resp.encoding = "utf-8"

        # stream=True 일 때 requests 의 timeout 은 "청크 사이 간격"만 재기 때문에
        # 모델이 계속 토큰을 뱉으면 timeout_s 가 전체 시간을 못 막는다.
        # → 벽시계 기준 상한을 직접 건다.
        deadline = time.time() + req.timeout_s

        text = ""
        timed_out = False
        stop = cancel.stopper(getattr(req.emitter, "node_id", None))
        for raw in resp.iter_lines(decode_unicode=True):
            if stop():
                # 여기서 끊어야 Stop 이 즉시 듣는다. 받은 데까지는 그대로 돌려주고,
                # 성공으로 위장하지 않는 판정은 호출부가 한다.
                resp.close()
                req.emitter.set_status("stopped - using what arrived so far")
                break
            if time.time() > deadline:
                resp.close()
                timed_out = True
                req.emitter.set_status(f"timeout({req.timeout_s}s) - using what arrived so far")
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
            # 마지막 청크에 usage 를 실어주는 서버가 있다. 있으면 쓰고 없으면 만다
            # (stream_options 를 보내 강제하지는 않는다 -- 실측을 못 했다, §0-5).
            if isinstance(obj.get("usage"), dict):
                self._last_usage = obj["usage"]
            for choice in obj.get("choices") or []:
                delta = choice.get("delta") or {}
                # 추론 모델은 답을 쓰기 전에 사고 과정을 먼저 흘린다. 이걸 안 받으면
                # 생성 시간 대부분 동안 모니터 창에 아무것도 안 뜬다 (실측: 델타
                # 298개가 thinking, 3개가 본문). 본문 칸에는 절대 섞지 않는다 —
                # 노드의 text 출력이 오염되면 다운스트림 프롬프트가 망가진다.
                think = delta.get("reasoning_content") or delta.get("reasoning") or ""
                if think:
                    # 예전 emitter 에는 이 메서드가 없다.
                    getattr(req.emitter, "append_thinking", lambda _piece: None)(think)
                piece = delta.get("content") or ""
                if piece:
                    text += piece
                    req.emitter.append(piece)
        return resp, text, timed_out

    def _run_loop(self, req: LLMRequest, messages: list, model: str):
        """툴 루프 (DESIGN §8.1). 반환: (text, debug_notes) 또는 (LLMResponse, notes)."""
        notes = []
        retried_with_model = False
        # 시드를 모르는 서버가 400 을 내면 여기가 켜지고 다음 요청부터 뺀다.
        drop_seed = False
        last_text = ""

        # 툴을 선언한 요청(file_access=True)은 delta 로 오는 tool_calls 조립이
        # 까다로워 스트리밍하지 않는다. 대신 도구 진행 상황을 status 로 보여준다.
        streaming = (
            req.emitter is not None and req.emitter.enabled and not req.file_access
        )

        node_id = getattr(req.emitter, "node_id", None)
        for iteration in range(self.max_iters):
            # 반복 경계마다 확인한다. 스트리밍이 꺼져 있으면(stream_view=off, 또는
            # file_access 의 툴 루프) SSE 루프의 검사를 못 거치므로 여기가 유일한
            # 중단 지점이다. 이미 날아간 요청 하나는 끝까지 기다려야 한다 --
            # requests 의 비스트리밍 응답은 도중에 끊을 방법이 없다.
            if cancel.is_stopped(node_id):
                return (
                    LLMResponse(
                        text=(last_text or "").strip(),
                        status="stopped - cancelled by user, returning what arrived so far",
                        raw_debug="lmstudio: cancelled by user (between tool-loop rounds)",
                    ),
                    notes,
                )
            payload = self._build_payload(req, messages, model)
            if drop_seed:
                payload.pop("seed", None)

            if streaming:
                resp, streamed, timed_out = self._stream_chat(req, payload)
                if streamed is not None:
                    if cancel.is_stopped(getattr(req.emitter, "node_id", None)):
                        # 받은 데까지는 돌려준다(timeout 과 같은 규칙). 다만 ok 는 아니다 —
                        # 중지된 결과가 성공으로 보이면 다운스트림이 잘린 글을 쓴다.
                        return (
                            LLMResponse(
                                text=streamed.strip(),
                                status="stopped - cancelled by user, returning what arrived so far",
                                raw_debug="lmstudio: cancelled by user",
                            ),
                            notes,
                        )
                    if timed_out:
                        # 다른 백엔드와 마찬가지로 잘린 응답은 ok 로 위장하지 않는다.
                        return (
                            LLMResponse(
                                text=streamed.strip(),
                                status=f"error: timeout({req.timeout_s}s) - returning what arrived so far",
                                raw_debug="lmstudio: timed out mid-stream",
                            ),
                            notes,
                        )
                    return streamed, notes
                # 200 이 아니면 아래가 오류/모델 폴백을 처리한다. streaming 은 끄지
                # 않는다 — 여기서 꺼버리면 폴백 뒤 재시도가 비스트리밍으로 나가고
                # 모니터 창이 빈 채로 끝난다. unload_after 기본값이 True 라 모델은
                # 매 실행 뒤 내려가고, 그래서 이 경로가 일상적으로 밟힌다.
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
                        notes.append(f"lmstudio: no model given -> using '{fallback}' from /v1/models")
                        continue
                # seed 는 OpenAI 규격 필드지만 모든 서버가 받는다는 보장은 없다
                # (실기기로 확인하지 못했다, §0-1). 400 이면 시드만 빼고 한 번 더
                # 해본다 -- 시드 하나 때문에 생성 전체가 실패하면 안 된다.
                if not drop_seed and resp.status_code == 400 and "seed" in payload:
                    drop_seed = True
                    notes.append(
                        "lmstudio: the server rejected 'seed' (HTTP 400) -> retried without it "
                        "(this server cannot reproduce results by seed)"
                    )
                    continue
                if detect_rate_limit(body):
                    status = "rate_limited"
                else:
                    # 이름은 self.name 이다. 별칭 백엔드(ollama/vllm/llamacpp)에는
                    # 사용자가 드롭다운에서 고른 이름이 그대로 들어 있다 --
                    # llamacpp 를 골랐는데 "LM Studio" 라고 하면 엉뚱한 데를 뒤진다.
                    status = f"error: {self.name} HTTP {resp.status_code}"
                    # 서버가 말해준 이유를 그대로 옮긴다. README 가 이미 그렇게
                    # 약속하고 있다("서버가 거부하면 그 서버의 문구가 status 에").
                    reason = server_error_reason(body)
                    if reason:
                        status = f"{status} - {reason}"
                return (
                    LLMResponse(status=status, raw_debug=f"HTTP {resp.status_code}\n{body}"),
                    notes,
                )

            data = resp.json()
            # 실제로 어떤 모델이 응답했는지 기록해 둔다(언로드 대상 파악용).
            if data.get("model"):
                self._served_model = data["model"]
            # 툴 루프는 여러 번 도는데, 마지막 응답의 usage 가 누적치가 아니라
            # 그 요청 한 건의 값이다. 그래도 마지막 것을 남긴다 -- 합계를 지어내는
            # 것보다 실제로 받은 숫자를 그대로 보여주는 편이 낫다.
            if isinstance(data.get("usage"), dict):
                self._last_usage = data["usage"]
            choices = data.get("choices") or []
            if not choices:
                return (
                    LLMResponse(
                        status=f"error: the {self.name} response has no choices",
                        raw_debug=json.dumps(data, ensure_ascii=False)[:2000],
                    ),
                    notes,
                )

            message = choices[0].get("message") or {}
            last_text = message.get("content") or last_text
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                content = message.get("content") or ""
                if not content.strip():
                    exhausted = self._reasoning_budget_error(
                        choices[0], data.get("usage") or {}, req
                    )
                    if exhausted is not None:
                        return exhausted, notes
                return content, notes

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
                    req.emitter.set_status(f"Tool: {fname}({args.get('path', '')})")
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

    @staticmethod
    def _reasoning_budget_error(choice: dict, usage: dict, req: LLMRequest):
        """추론 토큰이 max_tokens 를 다 먹어 본문이 비었으면 그렇게 말해준다.

        추론 모델(qwen3 계열 등)은 답을 쓰기 전에 숨겨진 reasoning 을 먼저 뱉는다.
        그 분량도 max_tokens 에 포함되므로, 예산이 작으면 reasoning 만 하다가
        잘리고 content 는 빈 문자열로 온다. 실측: max_tokens=256 일 때
        reasoning_tokens=254, finish_reason="length", content="".

        이때 "모델이 텍스트를 내지 않음" 이라고 하면 사용자는 모델이나 프롬프트를
        의심하게 된다. 실제로 필요한 건 max_tokens 를 올리는 것뿐이다.
        반환값: 오류 응답(해당하면) 또는 None.
        """
        if choice.get("finish_reason") != "length":
            return None
        details = usage.get("completion_tokens_details") or {}
        reasoning = details.get("reasoning_tokens") or 0
        if not reasoning:
            return None
        completion = usage.get("completion_tokens") or reasoning
        return LLMResponse(
            status=(
                f"error: reasoning tokens used up the whole max_tokens budget "
                f"(reasoning {reasoning} / limit {completion} tokens, 0 for the answer). "
                f"Raise max_tokens - this model thinks before it answers and that hidden "
                f"output counts against max_tokens too."
            ),
            raw_debug=(
                f"finish_reason=length reasoning_tokens={reasoning} "
                f"completion_tokens={completion} max_tokens={req.max_tokens}"
            ),
        )

    def _map_exception(self, exc: Exception, started: float, debug_notes: list) -> LLMResponse:
        import requests

        duration = time.time() - started
        if isinstance(exc, (requests.ConnectionError,)):
            status = CONNECT_ERROR_MSG
        elif isinstance(exc, requests.Timeout):
            status = f"error: timeout({int(duration)}s) - {self.name} was too slow to respond"
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

_LOGGER = logging.getLogger(__name__)
_WARNED = set()


def _warn_once(message: str):
    """같은 경고를 한 번만 낸다.

    INPUT_TYPES 는 /object_info 요청마다 불리므로 그대로 두면 콘솔이 같은 줄로
    가득 찬다. 반대로 아예 안 내면 원인을 못 찾는다 — 그 사이를 잡는다.
    """
    if message in _WARNED:
        return
    _WARNED.add(message)
    _LOGGER.warning("[LLM Hub] %s", message)


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
    problems = []
    try:
        import requests

        full_cfg = load_config()
        cfg = full_cfg.get("lmstudio", {}) or {}
        base = (cfg.get("base_url") or "http://127.0.0.1:1234").rstrip("/")
        headers = {}
        token = resolve_api_token(full_cfg)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        # /v1/models 는 모델 종류를 알려주지 않는다. v0 이 임베딩이라고 알려준 id 를
        # 여기 모아두지 않으면, 종류를 모르는 v1 결과를 합치면서 도로 살아난다.
        excluded = set()

        for path, extract in (
            ("/api/v0/models", _ids_from_v0),
            ("/v1/models", _ids_from_v1),
        ):
            try:
                resp = requests.get(base + path, headers=headers, timeout=timeout_s)
                if resp.status_code == 200:
                    payload = resp.json()
                    if path == "/api/v0/models":
                        excluded |= _embedding_ids(payload)
                    for model_id in extract(payload):
                        if model_id and model_id not in ids and model_id not in excluded:
                            ids.append(model_id)
                else:
                    # 상태 코드만 남긴다. 토큰은 어떤 경우에도 싣지 않는다 (DESIGN §10).
                    problems.append(f"{path} HTTP {resp.status_code}")
            except Exception as exc:
                problems.append(f"{path} {type(exc).__name__}")
                continue
    except Exception as exc:
        ids = []
        problems.append(type(exc).__name__)

    if not ids and problems:
        # 조용히 빈 목록을 돌려주면 사용자는 "드롭다운이 안 뜬다" 까지만 보이고
        # 원인을 알 길이 없다. 실제로 이 증상을 찾는 데 라이브 probe 가 필요했다.
        _warn_once(
            "Could not fetch the LM Studio model list (%s). The lmstudio_model "
            "dropdown will only show '(auto)'. A 401 means LM Studio has its API key "
            "enabled - put the token in the LM_STUDIO_API_KEY environment variable "
            "or in lm_studio_token.txt."
            % ", ".join(problems)
        )

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


def _embedding_ids(payload) -> set:
    """v0 응답에서 임베딩 모델 id 만 뽑는다 (v1 병합에서 되살아나지 않게)."""
    return {
        item.get("id")
        for item in (payload or {}).get("data") or []
        if item.get("type") == "embeddings" and item.get("id")
    }


def _ids_from_v1(payload) -> list:
    return [item.get("id") or "" for item in (payload or {}).get("data") or []]
