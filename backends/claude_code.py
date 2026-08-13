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
from ..utils import cancel
from ..utils.proc import run_cli_stream
from ..utils.proc import CliNotFoundError, cleanup_dir, make_empty_dir, parse_extra_args, resolve_cli, screen_extra_args, run_cli
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

            streaming = req.emitter is not None and req.emitter.enabled
            if streaming:
                # 실측: stream-json + --include-partial-messages 로 토큰 단위 델타가 온다.
                # (--verbose 가 함께 필요하다)
                args = [
                    exe, "-p", "--output-format", "stream-json",
                    "--include-partial-messages", "--verbose",
                ]
            else:
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

            node_id = getattr(req.emitter, "node_id", None)
            safe_extra, rejected = screen_extra_args(parse_extra_args(req.extra_args))
            if rejected:
                notes.append(
                    "extra_args: 샌드박스를 푸는 위험 플래그를 차단했습니다 → "
                    + " ".join(rejected)
                    + " (필요하면 config.json 의 allow_unsafe_extra_args=true)"
                )
            args += safe_extra

            prompt = _build_prompt(req, staged)
            notes.append(unsupported_note("claude", "temperature", "max_tokens"))

            if streaming:
                code, stdout, stderr, duration = run_cli_stream(
                    args, cwd=cwd, stdin_text=prompt, timeout_s=req.timeout_s,
                    # Stop 을 누르면 프로세스 트리를 죽인다. 이걸 안 주면 타임아웃까지
                    # (기본 300초) 붙잡혀 있어 버튼이 듣지 않는 것처럼 보인다.
                    should_stop=cancel.stopper(node_id), node_id=node_id,
                    on_line=lambda line: _on_stream_line(line, req.emitter),
                )
                return self._parse_stream(code, stdout, stderr, duration, notes)

            # 비스트리밍 경로에는 폴링 지점이 없다. 등록해야 Stop 이 듣는다.
            code, stdout, stderr, duration = run_cli(
                args, cwd=cwd, stdin_text=prompt, timeout_s=req.timeout_s,
                node_id=node_id,
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

    def _parse_stream(self, code, stdout, stderr, duration, notes) -> LLMResponse:
        """stream-json 출력에서 최종 result 객체를 찾아 일반 파서로 넘긴다.

        주의: 이벤트 스트림 원문을 그대로 오류 판정에 넘기면 안 된다.
        claude 는 정상 호출에도 매번 {"type":"rate_limit_event", ...,
        "rateLimitType":"five_hour"} 를 흘리는데, 여기에 "rate_limit" 문자열이
        들어 있어 detect_rate_limit() 가 무조건 참이 된다(실측).
        → 최종 result 객체만 파서에 넘기고, 없으면 델타를 모아서 쓴다.
        """
        final = ""
        deltas = []
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if "result" in obj and "is_error" in obj:
                final = line  # 최종 요약 객체
                continue
            if obj.get("type") == "stream_event":
                inner = obj.get("event") or {}
                if inner.get("type") == "content_block_delta":
                    delta = inner.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        deltas.append(delta.get("text") or "")

        if final:
            return self._parse(code, final, stderr, duration, notes)

        # 최종 객체가 없다 = 비정상 종료. 모은 델타를 본문으로 쓰고
        # 오류 판정은 stderr 로만 한다(stdout 이벤트 원문은 넘기지 않는다).
        partial = "".join(deltas).strip()
        response = self._parse(code, "", stderr, duration, notes)
        if partial and not response.text:
            response.text = partial
            if response.status.startswith("error:"):
                response.raw_debug = truncate_debug(
                    response.raw_debug + "\n(스트리밍 중단 — 받은 부분까지만 반환)"
                )
        return response

    def _parse(self, code, stdout, stderr, duration, notes) -> LLMResponse:
        debug = "\n".join([n for n in notes if n])

        if code == -1:  # run_cli 가 표시한 타임아웃
            return LLMResponse(
                status=f"error: timeout — claude 응답이 제한 시간 안에 오지 않음",
                duration_s=duration,
                raw_debug=truncate_debug(debug + "\n" + tail_lines(stderr)),
            )

        # 로그인/한도 판정은 진단 채널에만 건다.
        # stdout 은 정상 종료 시 모델의 답변이므로 여기에 패턴 검사를 하면
        # "429 가 뭐야?" 같은 질문의 정답이 rate_limited 로 오분류돼 버려진다.
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


def _on_stream_line(line: str, emitter) -> None:
    """claude 의 stream-json 한 줄을 해석해 모니터링 창에 반영한다.

    실측 이벤트:
      {"type":"stream_event","event":{"type":"content_block_delta",
       "delta":{"type":"text_delta","text":"..."}}}
      {"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read",...}]}}
    """
    try:
        event = json.loads(line)
    except ValueError:
        return

    kind = event.get("type")

    if kind == "stream_event":
        inner = event.get("event") or {}
        if inner.get("type") == "content_block_delta":
            delta = inner.get("delta") or {}
            if delta.get("type") == "text_delta":
                emitter.append(delta.get("text") or "")
        return

    if kind == "assistant":
        for block in ((event.get("message") or {}).get("content") or []):
            if block.get("type") == "tool_use":
                emitter.set_status(f"Tool: {block.get('name', '?')}")
        return

    if kind == "system" and event.get("subtype") == "init":
        emitter.set_status("모델 준비 중...")


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
