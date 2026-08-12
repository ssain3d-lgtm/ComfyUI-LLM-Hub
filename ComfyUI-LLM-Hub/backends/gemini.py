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
"""

from __future__ import annotations

import json
import os
import time

from ..utils.config import load_config
from ..utils.proc import CliNotFoundError, cleanup_dir, make_empty_dir, parse_extra_args, resolve_cli, run_cli
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

            args = [exe, "-o", "json"]

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

            args += parse_extra_args(req.extra_args)

            staged = stage_media(req.image_paths, cwd)
            prompt = _build_prompt(req, staged)
            notes.append(unsupported_note("gemini", "temperature", "max_tokens"))

            # 프롬프트는 stdin 으로 넣는다(인자 길이 제한 회피).
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

        if detect_login_error(stderr, stdout):
            return LLMResponse(
                status=LOGIN_HINT,
                duration_s=duration,
                raw_debug=truncate_debug(debug + "\n" + tail_lines(stderr)),
            )
        if detect_rate_limit(stderr, stdout):
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
