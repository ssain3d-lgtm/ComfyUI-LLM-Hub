# -*- coding: utf-8 -*-
"""Codex CLI 백엔드 (DESIGN §8.3).

실측 기준 (codex exec --help):
  codex exec [-m MODEL] -s read-only --skip-git-repo-check [-i IMG]...
             -o LASTMSG_FILE [PROMPT|-]
  - PROMPT 를 생략하거나 '-' 를 주면 stdin 을 지시문으로 읽는다(실측).
    → 항상 stdin 을 쓰면 Windows 인자 길이 제한 문제 자체가 사라진다.
  - -o/--output-last-message 로 최종 메시지를 파일에 직접 받을 수 있다(실측).
    stdout 파싱보다 안정적이므로 이 경로를 기본으로 쓴다.
  - -s read-only 로 읽기 허용/쓰기 차단.

비디오: Codex 는 비디오 입력을 지원하지 않는다(-i 는 이미지 전용).
        → 프레임을 뽑아 -i 로 전달한다.
"""

from __future__ import annotations

import os
import tempfile
import time

from ..utils.proc import CliNotFoundError, cleanup_dir, make_empty_dir, parse_extra_args, resolve_cli, run_cli
from .base import (
    BaseBackend,
    LLMRequest,
    LLMResponse,
    detect_login_error,
    detect_rate_limit,
    frames_for_unsupported_video,
    merge_system_prompt,
    tail_lines,
    truncate_debug,
    unsupported_note,
    validate_workspace,
)

LOGIN_HINT = "error: codex 로그인 필요 — 터미널에서 codex login 실행 후 다시 시도하세요"


class CodexBackend(BaseBackend):
    name = "codex"

    def generate(self, req: LLMRequest) -> LLMResponse:
        started = time.time()
        notes = []
        temp_cwd = ""
        last_message_path = ""

        ws_error = validate_workspace(req)
        if ws_error:
            return LLMResponse(status=ws_error, duration_s=time.time() - started)

        try:
            exe = resolve_cli("codex")
        except CliNotFoundError as exc:
            return LLMResponse(status=f"error: {exc}", duration_s=time.time() - started)

        try:
            if req.file_access:
                cwd = req.workspace_dir
            else:
                temp_cwd = make_empty_dir()
                cwd = temp_cwd

            handle, last_message_path = tempfile.mkstemp(prefix="llmhub_codex_", suffix=".txt")
            os.close(handle)

            args = [exe, "exec", "-s", "read-only", "--skip-git-repo-check"]

            if (req.model or "").strip():
                args += ["-m", req.model.strip()]

            media = list(req.image_paths or [])
            if req.video_paths:
                frames, video_notes = frames_for_unsupported_video(req, "codex", cwd)
                media += frames
                notes.extend(video_notes)
            for path in media:
                args += ["-i", path]

            args += ["-o", last_message_path]

            if req.mcp_config:
                notes.append("codex: 비대화형 MCP 승인 이슈로 v1 미지원")

            args += parse_extra_args(req.extra_args)

            # 프롬프트는 항상 stdin 으로 전달한다 ('-' = stdin 을 지시문으로 사용).
            args += ["-"]

            prompt = merge_system_prompt(req)
            notes.append(unsupported_note("codex", "temperature", "max_tokens"))

            code, stdout, stderr, duration = run_cli(
                args, cwd=cwd, stdin_text=prompt, timeout_s=req.timeout_s
            )

            return self._parse(code, stdout, stderr, duration, notes, last_message_path)

        except Exception as exc:
            return LLMResponse(
                status=f"error: {type(exc).__name__}: {exc}",
                duration_s=time.time() - started,
                raw_debug=truncate_debug("\n".join(notes)),
            )
        finally:
            cleanup_dir(temp_cwd)
            _remove(last_message_path)

    def _parse(self, code, stdout, stderr, duration, notes, last_message_path) -> LLMResponse:
        debug = "\n".join([n for n in notes if n])

        if code == -1:
            return LLMResponse(
                status="error: timeout — codex 응답이 제한 시간 안에 오지 않음",
                duration_s=duration,
                raw_debug=truncate_debug(debug + "\n" + tail_lines(stderr)),
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
                raw_debug=truncate_debug(
                    debug + "\ncodex 플랜 크레딧/한도에 걸렸습니다.\n" + tail_lines(stderr)
                ),
            )

        # 최종 메시지는 -o 파일에서 읽는다 (stdout 파싱보다 안정적).
        text = _read_text(last_message_path).strip()
        if not text:
            text = (stdout or "").strip()
            if text:
                debug += "\n(-o 파일이 비어 stdout 을 사용)"

        if code != 0 and not text:
            return LLMResponse(
                status=f"error: codex 종료 코드 {code}",
                duration_s=duration,
                raw_debug=truncate_debug(debug + "\n" + tail_lines(stderr)),
            )

        if not text:
            return LLMResponse(
                status="error: codex 응답이 비어 있음",
                duration_s=duration,
                raw_debug=truncate_debug(debug + "\n" + tail_lines(stderr)),
            )

        if stderr.strip():
            debug += "\nstderr:\n" + tail_lines(stderr, 10)

        return LLMResponse(
            text=text,
            status="ok",
            duration_s=duration,
            raw_debug=truncate_debug(debug),
        )


def _read_text(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _remove(path: str) -> None:
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass
