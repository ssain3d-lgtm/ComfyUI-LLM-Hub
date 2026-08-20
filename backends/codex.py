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

import json
import os
import tempfile
import time

from ..utils import cancel
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
    extra_body_ignored_note,
    frames_for_unsupported_video,
    merge_system_prompt,
    tail_lines,
    truncate_debug,
    unsupported_note,
    validate_workspace,
)

LOGIN_HINT = "error: codex login required - run codex login in a terminal, then try again"


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

            streaming = req.emitter is not None and req.emitter.enabled
            args = [exe, "exec", "-s", "read-only", "--skip-git-repo-check"]
            if streaming:
                args += ["--json"]

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
                notes.append("codex: not supported in v1 - MCP tool approval is auto-cancelled in non-interactive mode")

            node_id = getattr(req.emitter, "node_id", None)
            safe_extra, rejected = screen_extra_args(parse_extra_args(req.extra_args))
            if rejected:
                notes.append(
                    "extra_args: blocked flags that would unlock the sandbox -> "
                    + " ".join(rejected)
                    + " (set allow_unsafe_extra_args=true in config.json if you really need them)"
                )
            args += safe_extra

            # 프롬프트는 항상 stdin 으로 전달한다 ('-' = stdin 을 지시문으로 사용).
            args += ["-"]

            prompt = merge_system_prompt(req)
            notes.append(unsupported_note("codex", "temperature", "max_tokens"))
            extra_body_note = extra_body_ignored_note("codex", req)
            if extra_body_note:
                notes.append(extra_body_note)

            if streaming:
                state = {"text": ""}
                code, stdout, stderr, duration = run_cli_stream(
                    args, cwd=cwd, stdin_text=prompt, timeout_s=req.timeout_s,
                    # Stop 을 누르면 프로세스 트리를 죽인다. 이걸 안 주면 타임아웃까지
                    # (기본 300초) 붙잡혀 있어 버튼이 듣지 않는 것처럼 보인다.
                    should_stop=cancel.stopper(node_id), node_id=node_id,
                    on_line=lambda line: _on_stream_line(line, req.emitter, state),
                )
                # stdout 은 JSONL 이벤트라 그대로 넘기면 오류 문구 오탐이 난다.
                # 대신 스트리밍으로 모은 평문을 폴백 본문으로 넘긴다
                # (-o 파일이 비었을 때 "응답이 비어 있음" 으로 잘못 끝나지 않게).
                return self._parse(
                    code, state.get("text", ""), stderr, duration,
                    notes, last_message_path,
                )

            # 비스트리밍 경로에는 폴링 지점이 없다. 등록해야 Stop 이 듣는다.
            code, stdout, stderr, duration = run_cli(
                args, cwd=cwd, stdin_text=prompt, timeout_s=req.timeout_s,
                node_id=node_id,
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
                status="error: timeout - codex did not respond within the time limit",
                duration_s=duration,
                raw_debug=truncate_debug(debug + "\n" + tail_lines(stderr)),
            )

        # stdout 은 정상 종료 시 모델 답변이므로 오류 패턴 검사에서 제외한다
        # (claude_code.py 의 오분류 사례와 동일한 이유).
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
                raw_debug=truncate_debug(
                    debug + "\ncodex plan credits / usage limit reached.\n" + tail_lines(stderr)
                ),
            )

        # 최종 메시지는 -o 파일에서 읽는다 (stdout 파싱보다 안정적).
        text = _read_text(last_message_path).strip()
        if not text:
            text = (stdout or "").strip()
            if text:
                debug += "\n(the -o file was empty; using stdout)"

        if code != 0 and not text:
            return LLMResponse(
                status=f"error: codex exit code {code}",
                duration_s=duration,
                raw_debug=truncate_debug(debug + "\n" + tail_lines(stderr)),
            )

        if not text:
            return LLMResponse(
                status="error: the codex response was empty",
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


# codex --json 의 이벤트 스키마는 이 환경에서 실측하지 못했다(로그인 필요).
# 그래서 특정 형태를 단정하지 않고, 흔히 쓰이는 필드 이름들을 관대하게 훑는다.
# 아무것도 못 찾아도 최종 본문은 -o 파일에서 읽으므로 결과 자체는 정상이다.
_TEXT_KEYS = ("delta", "text", "message", "content", "last_agent_message")


def _extract_text(obj):
    """중첩 dict 에서 사람이 읽을 만한 텍스트 조각을 찾아본다."""
    if isinstance(obj, str):
        return obj
    if not isinstance(obj, dict):
        return ""
    for key in _TEXT_KEYS:
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
    for nested_key in ("msg", "item", "event", "data"):
        nested = obj.get(nested_key)
        if isinstance(nested, dict):
            found = _extract_text(nested)
            if found:
                return found
    return ""


def _on_stream_line(line: str, emitter, state) -> None:
    try:
        event = json.loads(line)
    except ValueError:
        return
    if not isinstance(event, dict):
        return

    # 실측(codex-cli 0.146.0): 바깥 type 은 "item.completed" 라 종류를 알려주지 않고,
    # 진짜 종류는 안쪽 item.type("agent_message" / "command_execution")에 있다.
    # 바깥만 보면 아래 어느 분기에도 안 걸려 본문이 통째로 버려진다.
    # 둘을 합쳐서 판정하면 예전 스키마와 새 스키마가 함께 동작한다.
    inner = event.get("item")
    kind = " ".join(
        part for part in (
            str(event.get("type") or (event.get("msg") or {}).get("type") or ""),
            str(inner.get("type") or "") if isinstance(inner, dict) else "",
        ) if part
    ).lower()

    if "tool" in kind or "command" in kind or "exec" in kind:
        emitter.set_status(f"Tool: {kind}")
        return

    piece = _extract_text(event)
    if not piece:
        return

    if "delta" in kind:
        state["text"] = state.get("text", "") + piece
        emitter.append(piece)
    elif "message" in kind or "agent" in kind:
        # 완성 메시지는 누적본을 대체한다(델타 중복 방지).
        state["text"] = piece
        emitter.reset_text(piece)


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
