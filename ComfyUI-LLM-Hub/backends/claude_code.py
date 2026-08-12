# -*- coding: utf-8 -*-
"""Claude Code CLI 백엔드 (DESIGN §8.2).

실측 기준 (claude --help):
  claude -p --output-format json [--model M] [--append-system-prompt S]
         [--tools ""] [--allowedTools "Read,Glob,Grep"] [--mcp-config PATH]
  - 프롬프트는 stdin 파이프로 전달된다 (실측 확인).
  - --output-format json 의 결과 JSON: result / is_error / usage / total_cost_usd
  - --max-turns 플래그는 현재 CLI 에 없다 → 사용하지 않는다.

비디오: Claude 는 비디오 입력을 지원하지 않는다(Read 툴은 이미지/PDF 만).
        → 프레임을 뽑아 이미지로 전달한다.
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
    frames_for_unsupported_video,
    stage_media,
    tail_lines,
    truncate_debug,
    unsupported_note,
    validate_workspace,
    workspace_hint,
)

# file_access=True 일 때만 허용하는 읽기 전용 툴 (DESIGN §11).
READ_ONLY_TOOLS = "Read,Glob,Grep"

LOGIN_HINT = "error: claude 로그인 필요 — 터미널에서 claude 실행 후 로그인"


class ClaudeCodeBackend(BaseBackend):
    name = "claude"

    def __init__(self, config: dict = None):
        self.config = config or load_config()
        defaults = self.config.get("defaults", {}) or {}
        # "append" = 기본 시스템 프롬프트에 덧붙임, "replace" = 통째로 교체
        self.system_prompt_mode = defaults.get("claude_system_prompt_mode") or "append"

    def generate(self, req: LLMRequest) -> LLMResponse:
        started = time.time()
        notes = []
        temp_cwd = ""

        ws_error = validate_workspace(req)
        if ws_error:
            return LLMResponse(status=ws_error, duration_s=time.time() - started)

        try:
            exe = resolve_cli("claude")
        except CliNotFoundError as exc:
            return LLMResponse(status=f"error: {exc}", duration_s=time.time() - started)

        try:
            if req.file_access:
                cwd = req.workspace_dir
            else:
                temp_cwd = make_empty_dir()
                cwd = temp_cwd

            args = [exe, "-p", "--output-format", "json"]

            if (req.model or "").strip():
                args += ["--model", req.model.strip()]

            # 시스템 프롬프트는 전용 플래그가 있으므로 §8.5 병합을 쓰지 않는다.
            system_parts = []
            if (req.system_prompt or "").strip():
                system_parts.append(req.system_prompt.strip())
            hint = workspace_hint(req)
            if hint:
                system_parts.append(hint)
            if system_parts:
                flag = (
                    "--system-prompt"
                    if self.system_prompt_mode == "replace"
                    else "--append-system-prompt"
                )
                args += [flag, "\n".join(system_parts)]

            # 이미지는 cwd 안에 있어야 Read 툴이 볼 수 있다. file_access=False 라도
            # 이미지가 있으면 Read 만 열어준다(워크스페이스 노출 없음).
            media = list(req.image_paths or [])
            if req.video_paths:
                frames, video_notes = frames_for_unsupported_video(req, "claude", cwd)
                media += frames
                notes.extend(video_notes)
            staged = stage_media(media, cwd)
            if req.file_access:
                allowed = READ_ONLY_TOOLS
            elif staged:
                allowed = "Read"
            else:
                allowed = ""

            if req.mcp_config:
                if os.path.exists(req.mcp_config):
                    args += ["--mcp-config", req.mcp_config, "--strict-mcp-config"]
                    allowed = (allowed + "," if allowed else "") + "mcp__*"
                    notes.append(f"claude: mcp-config 적용 → {req.mcp_config}")
                else:
                    notes.append(f"claude: mcp_config 파일이 없어 무시 → {req.mcp_config}")

            if allowed:
                args += ["--allowedTools", allowed]
            else:
                # 실측: --tools "" 가 내장 툴 전체를 끄는 공식 방법이다.
                args += ["--tools", ""]

            args += parse_extra_args(req.extra_args)

            prompt = _build_prompt(req, staged)
            notes.append(unsupported_note("claude", "temperature", "max_tokens"))

            code, stdout, stderr, duration = run_cli(
                args, cwd=cwd, stdin_text=prompt, timeout_s=req.timeout_s
            )

            return self._parse(code, stdout, stderr, duration, notes)

        except Exception as exc:  # 모든 예외를 status 로 변환 (DESIGN §6)
            return LLMResponse(
                status=f"error: {type(exc).__name__}: {exc}",
                duration_s=time.time() - started,
                raw_debug=truncate_debug("\n".join(notes)),
            )
        finally:
            cleanup_dir(temp_cwd)

    def _parse(self, code, stdout, stderr, duration, notes) -> LLMResponse:
        debug = "\n".join([n for n in notes if n])

        if code == -1:  # run_cli 가 표시한 타임아웃
            return LLMResponse(
                status=f"error: timeout — claude 응답이 제한 시간 안에 오지 않음",
                duration_s=duration,
                raw_debug=truncate_debug(debug + "\n" + tail_lines(stderr)),
            )

        # 로그인/한도 문구는 종료 코드와 무관하게 먼저 본다.
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
                raw_debug=truncate_debug(
                    debug + "\nclaude 사용량 한도에 걸렸습니다. 한도 리셋을 기다리세요.\n" + tail_lines(stderr)
                ),
            )

        payload = _load_json(stdout)

        if payload is None:
            if code != 0:
                return LLMResponse(
                    status=f"error: claude 종료 코드 {code}",
                    duration_s=duration,
                    raw_debug=truncate_debug(debug + "\n" + tail_lines(stderr)),
                )
            text = (stdout or "").strip()
            return LLMResponse(
                text=text,
                status="ok" if text else "error: claude 응답이 비어 있음",
                duration_s=duration,
                raw_debug=truncate_debug(debug + "\n(JSON 파싱 실패, stdout 원문 사용)\n" + tail_lines(stderr)),
            )

        result = payload.get("result")
        if not isinstance(result, str):
            result = "" if result is None else str(result)

        meta = {
            key: payload.get(key)
            for key in ("subtype", "num_turns", "total_cost_usd", "usage", "permission_denials")
            if payload.get(key) is not None
        }
        debug = debug + "\n" + json.dumps(meta, ensure_ascii=False)[:1500]

        if payload.get("is_error"):
            status = "rate_limited" if detect_rate_limit(result) else f"error: claude — {result[:200]}"
            return LLMResponse(status=status, duration_s=duration, raw_debug=truncate_debug(debug))

        return LLMResponse(
            text=result.strip(),
            status="ok" if result.strip() else "error: claude 응답이 비어 있음",
            duration_s=duration,
            raw_debug=truncate_debug(debug),
        )


def _build_prompt(req: LLMRequest, staged: list) -> str:
    """미디어가 있으면 Read 툴로 먼저 확인하라는 지시를 앞에 붙인다 (DESIGN §8.2)."""
    prefix = ""
    if staged:
        names = ", ".join(staged)
        prefix = f"먼저 Read 툴로 {names} 파일을 확인한 뒤 작업하라.\n\n"
    return prefix + (req.user_prompt or "")


def _load_json(stdout: str):
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
        # 앞뒤에 잡음이 섞였을 때를 대비해 마지막 JSON 객체를 찾아본다.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except ValueError:
                return None
    return None
