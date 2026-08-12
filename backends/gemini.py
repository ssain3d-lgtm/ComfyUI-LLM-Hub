# -*- coding: utf-8 -*-
"""Gemini CLI 백엔드 (DESIGN §8.4).

실측 기준 (gemini --help):
  gemini -p "PROMPT" [-m MODEL] -o json [--approval-mode plan] [--skip-trust]
  - -p 는 "stdin 입력이 있으면 그 뒤에 덧붙는다" → stdin+(-p) 조합이 가능하다.
  - --approval-mode plan = 읽기 전용 모드. --yolo 는 쓰기/셸까지 풀리므로 쓰지 않는다.
  - 실측: 폴더가 신뢰되지 않으면 승인 모드가 조용히 default 로 강등된다
    ("Approval mode overridden to default because the current folder is not trusted").
    --skip-trust 를 함께 줘야 plan 모드가 유지된다.
  - -o json 출력 형태: {"session_id": ..., "response": ...} / 오류 시 {"error": {...}}

비디오: 4개 백엔드 중 유일하게 네이티브 지원. CLI 가 video/* 파일을 inlineData 로
        모델에 넘긴다(read_file/read_many_files 경로). 프레임 추출 없이 파일을 그대로 참조한다.
"""

from __future__ import annotations

import json
import os
import time

from ..utils.config import load_config
from ..utils.proc import (
    CliNotFoundError, cleanup_dir, make_empty_dir, parse_extra_args,
    resolve_cli, run_cli, run_cli_stream, screen_extra_args,
)
from .base import (
    BaseBackend,
    LLMRequest,
    LLMResponse,
    detect_login_error,
    detect_rate_limit,
    merge_system_prompt,
    stage_media,
    tail_lines,
    truncate_debug,
    unsupported_note,
    validate_workspace,
)

LOGIN_HINT = (
    "error: gemini 로그인 필요 — 터미널에서 gemini 를 실행해 구글 계정으로 로그인하세요"
)
RATE_LIMIT_HINT = "gemini 쿼터 초과 — Flash 모델로 바꾸거나 한도 리셋을 기다리세요"


class GeminiBackend(BaseBackend):
    name = "gemini"

    def __init__(self, config: dict = None):
        self.config = config or load_config()
        defaults = self.config.get("defaults", {}) or {}
        self.default_model = defaults.get("gemini_model") or "gemini-2.5-flash"
        self.approval_mode = defaults.get("gemini_approval_mode") or "plan"

    def generate(self, req: LLMRequest) -> LLMResponse:
        started = time.time()
        notes = []
        temp_cwd = ""

        ws_error = validate_workspace(req)
        if ws_error:
            return LLMResponse(status=ws_error, duration_s=time.time() - started)

        try:
            exe = resolve_cli("gemini")
        except CliNotFoundError as exc:
            return LLMResponse(status=f"error: {exc}", duration_s=time.time() - started)

        try:
            if req.file_access:
                cwd = req.workspace_dir
            else:
                temp_cwd = make_empty_dir()
                cwd = temp_cwd

            streaming = req.emitter is not None and req.emitter.enabled
            args = [exe, "-o", "stream-json" if streaming else "json"]

            model = (req.model or "").strip() or self.default_model
            if model:
                args += ["-m", model]

            # 읽기 전용 승인 모드. --skip-trust 가 없으면 조용히 default 로 강등된다(실측).
            if self.approval_mode:
                args += ["--approval-mode", self.approval_mode, "--skip-trust"]

            if req.mcp_config:
                notes.append(
                    "gemini: mcp_config 는 전역 settings.json 사이드이펙트 때문에 v1 미적용"
                )

            safe_extra, rejected = screen_extra_args(parse_extra_args(req.extra_args))
            if rejected:
                notes.append(
                    "extra_args: 샌드박스를 푸는 위험 플래그를 차단했습니다 → "
                    + " ".join(rejected)
                    + " (필요하면 config.json 의 allow_unsafe_extra_args=true)"
                )
            args += safe_extra

            # Gemini 는 비디오를 네이티브로 읽는다 → 파일 그대로 넘긴다.
            media = list(req.image_paths or []) + list(req.video_paths or [])
            staged = stage_media(media, cwd)
            if req.video_paths:
                notes.append(f"gemini: 비디오 {len(req.video_paths)}개를 네이티브로 전달")
            prompt = _build_prompt(req, staged)
            notes.append(unsupported_note("gemini", "temperature", "max_tokens"))

            # 프롬프트는 stdin 으로 넣는다(인자 길이 제한 회피).
            if streaming:
                state = {"text": ""}
                code, stdout, stderr, duration = run_cli_stream(
                    args, cwd=cwd, stdin_text=prompt, timeout_s=req.timeout_s,
                    on_line=lambda line: _on_stream_line(line, req.emitter, state),
                )
                return self._parse_stream(code, stdout, stderr, duration, notes, state)

            code, stdout, stderr, duration = run_cli(
                args, cwd=cwd, stdin_text=prompt, timeout_s=req.timeout_s
            )
            return self._parse(code, stdout, stderr, duration, notes)

        except Exception as exc:
            return LLMResponse(
                status=f"error: {type(exc).__name__}: {exc}",
                duration_s=time.time() - started,
                raw_debug=truncate_debug("\n".join(notes)),
            )
        finally:
            cleanup_dir(temp_cwd)

    def _parse_stream(self, code, stdout, stderr, duration, notes, state) -> LLMResponse:
        """stream-json 출력에서 최종 result 이벤트를 찾아 판정한다.

        이벤트 원문을 그대로 오류 판정에 넘기지 않는다(오탐 방지).
        """
        final = None
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("type") == "result":
                final = obj

        text = (state.get("text") or "").strip()

        if final is not None and final.get("status") == "error":
            message = str((final.get("error") or {}).get("message") or "")
            if detect_login_error(message):
                return LLMResponse(status=LOGIN_HINT, duration_s=duration,
                                   raw_debug=truncate_debug("\n".join(notes) + "\n" + message))
            if detect_rate_limit(message):
                return LLMResponse(status="rate_limited", duration_s=duration,
                                   raw_debug=truncate_debug("\n".join(notes) + "\n" + message
                                                            + "\n" + RATE_LIMIT_HINT))
            return LLMResponse(status=f"error: gemini — {message[:200]}", duration_s=duration,
                               raw_debug=truncate_debug("\n".join(notes) + "\n" + message))

        if code == -1:
            return LLMResponse(status="error: timeout — gemini 응답이 제한 시간 안에 오지 않음",
                               duration_s=duration,
                               raw_debug=truncate_debug("\n".join(notes) + "\n" + tail_lines(stderr)))

        if not text:
            return self._parse(code, "", stderr, duration, notes)

        debug = "\n".join(n for n in notes if n)

        # 텍스트가 좀 왔더라도 result 이벤트가 없거나 종료 코드가 0 이 아니면
        # 정상 완료가 아니다. 잘린 답을 성공으로 위장하면 안 된다.
        if final is None or code != 0:
            return LLMResponse(
                text=text,
                status=f"error: gemini 비정상 종료(exit {code}) — 받은 부분까지만 반환",
                duration_s=duration,
                raw_debug=truncate_debug(debug + "\n" + tail_lines(stderr)),
            )

        return LLMResponse(text=text, status="ok", duration_s=duration,
                           raw_debug=truncate_debug(debug))

    def _parse(self, code, stdout, stderr, duration, notes) -> LLMResponse:
        debug = "\n".join([n for n in notes if n])

        if code == -1:
            return LLMResponse(
                status="error: timeout — gemini 응답이 제한 시간 안에 오지 않음",
                duration_s=duration,
                raw_debug=truncate_debug(debug + "\n" + tail_lines(stderr)),
            )

        payload = _load_json(stdout)

        # JSON 안의 error 객체를 우선 본다 (실측: {"error": {"type","message","code"}}).
        if payload and isinstance(payload.get("error"), dict):
            message = str(payload["error"].get("message") or "")
            detail = debug + "\n" + message
            if detect_login_error(message) or payload["error"].get("code") == 41:
                return LLMResponse(status=LOGIN_HINT, duration_s=duration, raw_debug=truncate_debug(detail))
            if detect_rate_limit(message):
                return LLMResponse(
                    status="rate_limited",
                    duration_s=duration,
                    raw_debug=truncate_debug(detail + "\n" + RATE_LIMIT_HINT),
                )
            return LLMResponse(
                status=f"error: gemini — {message[:200]}",
                duration_s=duration,
                raw_debug=truncate_debug(detail),
            )

        # stdout 은 정상 종료 시 모델 답변(또는 JSON)이므로 오류 패턴 검사에서 제외.
        diagnostic = stderr if code == 0 else (stderr + "\n" + stdout)
        if detect_login_error(diagnostic):
            return LLMResponse(
                status=LOGIN_HINT,
                duration_s=duration,
                raw_debug=truncate_debug(debug + "\n" + tail_lines(stderr)),
            )
        if detect_rate_limit(diagnostic):
            return LLMResponse(
                status="rate_limited",
                duration_s=duration,
                raw_debug=truncate_debug(debug + "\n" + RATE_LIMIT_HINT + "\n" + tail_lines(stderr)),
            )

        if payload is not None:
            text = payload.get("response")
            if not isinstance(text, str):
                text = "" if text is None else str(text)
            stats = payload.get("stats")
            if stats is not None:
                debug += "\n" + json.dumps(stats, ensure_ascii=False)[:1200]
            return LLMResponse(
                text=text.strip(),
                status="ok" if text.strip() else "error: gemini 응답이 비어 있음",
                duration_s=duration,
                raw_debug=truncate_debug(debug),
            )

        if code != 0:
            return LLMResponse(
                status=f"error: gemini 종료 코드 {code}",
                duration_s=duration,
                raw_debug=truncate_debug(debug + "\n" + tail_lines(stderr)),
            )

        text = (stdout or "").strip()
        return LLMResponse(
            text=text,
            status="ok" if text else "error: gemini 응답이 비어 있음",
            duration_s=duration,
            raw_debug=truncate_debug(debug + "\n(JSON 파싱 실패, stdout 원문 사용)\n" + tail_lines(stderr)),
        )


def _on_stream_line(line: str, emitter, state) -> None:
    """gemini stream-json 한 줄 처리.

    CLI 번들 소스에서 확인한 이벤트:
      {"type":"init","session_id":...,"model":...}
      {"type":"message","role":"assistant","content":"...","delta":true}
      {"type":"tool_use","tool_name":...} / {"type":"tool_result","status":...}
      {"type":"error","severity":...,"message":...}
      {"type":"result","status":"success"|"error","stats":{...}}
    """
    try:
        event = json.loads(line)
    except ValueError:
        return

    kind = event.get("type")

    if kind == "message" and event.get("role") == "assistant":
        content = event.get("content")
        if not isinstance(content, str):
            return
        if event.get("delta"):
            state["text"] = state.get("text", "") + content
            emitter.append(content)
        else:
            state["text"] = content
            emitter.reset_text(content)
        return

    if kind == "tool_use":
        emitter.set_status(f"도구 사용: {event.get('tool_name', '?')}")
        return
    if kind == "init":
        emitter.set_status(f"모델 준비: {event.get('model', '')}")
        return
    if kind == "error" and event.get("severity") == "error":
        emitter.set_status(f"오류: {str(event.get('message', ''))[:80]}")


def _build_prompt(req: LLMRequest, staged: list) -> str:
    """§8.5 병합 + 미디어 파일은 @상대경로 로 참조한다 (Gemini CLI 문법).

    staged 는 cwd 기준 상대경로라 워크스페이스 밖 참조가 되지 않는다.
    """
    merged = merge_system_prompt(req)
    if not staged:
        return merged

    refs = " ".join(f"@{name}" for name in staged)
    return f"{refs}\n\n{merged}"


def _load_json(stdout: str):
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except ValueError:
                return None
    return None
