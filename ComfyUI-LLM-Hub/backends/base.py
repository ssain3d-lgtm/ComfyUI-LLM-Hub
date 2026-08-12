# -*- coding: utf-8 -*-
"""공통 데이터 모델 / 백엔드 인터페이스 / CLI 백엔드 공용 헬퍼 (DESIGN §6, §7, §8.5)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# raw_debug 는 앞뒤 합쳐 최대 4000자로 절단한다 (DESIGN §6).
MAX_DEBUG_CHARS = 4000

# file_access=True 일 때 시스템 프롬프트 끝에 자동 주입되는 안내 한 줄 (DESIGN §7).
WORKSPACE_HINT_TEMPLATE = (
    "작업 루트 폴더는 {workspace_dir} 이다. "
    "파일이 필요하면 먼저 목록을 확인한 뒤 필요한 파일만 읽어라."
)


@dataclass
class LLMRequest:
    backend: str
    model: str
    system_prompt: str
    user_prompt: str
    image_paths: list = field(default_factory=list)  # 절대경로 PNG
    video_paths: list = field(default_factory=list)  # 절대경로 비디오 파일
    video_max_frames: int = 8   # 비디오 미지원 백엔드에서 뽑을 프레임 수
    workspace_dir: str = ""
    file_access: bool = False
    mcp_config: str = ""  # JSON 파일 경로 또는 ""
    temperature: float = 0.7
    max_tokens: int = 2048
    timeout_s: int = 300
    extra_args: str = ""


@dataclass
class LLMResponse:
    text: str = ""
    status: str = "ok"  # "ok" | "error: ..." | "rate_limited"
    duration_s: float = 0.0
    raw_debug: str = ""


class BaseBackend:
    """모든 백엔드의 공통 인터페이스.

    구현체는 generate() 내부에서 모든 예외를 잡아
    status="error: ..." 형태의 LLMResponse 로 변환해야 한다 (DESIGN §6, N4).
    """

    name = "base"

    def generate(self, req: LLMRequest) -> LLMResponse:  # pragma: no cover - 인터페이스
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 공용 헬퍼
# ---------------------------------------------------------------------------


def truncate_debug(text: str, limit: int = MAX_DEBUG_CHARS) -> str:
    """debug 문자열을 앞뒤 합쳐 limit 자로 절단한다 (DESIGN §6)."""
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    return text[:head] + "\n...[truncated]...\n" + text[-tail:]


def workspace_hint(req: LLMRequest) -> str:
    """file_access=True 일 때만 워크스페이스 안내 한 줄을 돌려준다 (DESIGN §7)."""
    if not req.file_access or not req.workspace_dir:
        return ""
    return WORKSPACE_HINT_TEMPLATE.format(workspace_dir=req.workspace_dir)


def merge_system_prompt(req: LLMRequest) -> str:
    """시스템 프롬프트 전용 플래그가 없는 CLI 를 위한 병합 규칙 (DESIGN §8.5).

    시스템 프롬프트도 워크스페이스 안내도 없으면 user_prompt 를 그대로 돌려준다.
    """
    hint = workspace_hint(req)
    system = (req.system_prompt or "").strip()
    if not system and not hint:
        return req.user_prompt

    lines = ["### SYSTEM"]
    if system:
        lines.append(system)
    if hint:
        lines.append(hint)
    lines.append("")
    lines.append("### TASK")
    lines.append(req.user_prompt)
    return "\n".join(lines)


def validate_workspace(req: LLMRequest) -> str:
    """워크스페이스 유효성 검사 (DESIGN §7).

    반환값: 오류 메시지(있으면) 또는 "" (정상).
    """
    if not req.file_access:
        return ""
    ws = (req.workspace_dir or "").strip()
    if not ws:
        return "error: workspace_dir 확인 필요 (file_access=True 인데 경로가 비어 있음)"
    if not os.path.isdir(ws):
        return f"error: workspace_dir 확인 필요 (폴더가 없음: {ws})"
    return ""


# --- CLI 출력 기반 오류 분류 (DESIGN §8.2 / §8.3 / §8.4) ---------------------

_LOGIN_PATTERNS = (
    "please log in",
    "please login",
    "not logged in",
    "not authenticated",
    "authentication required",
    "authentication failed",
    "unauthorized",
    "invalid api key",
    "set an auth method",
    "run `claude login`",
    "run claude login",
    "codex login",
    "please sign in",
    "no credentials",
    "credentials not found",
    "oauth token",
)

_RATE_LIMIT_PATTERNS = (
    "usage limit",
    "rate limit",
    "rate_limit",
    "ratelimit",
    "too many requests",
    "quota exceeded",
    "quota_exceeded",
    "resource_exhausted",
    "resource exhausted",
    "429",
    "insufficient_quota",
    "out of credits",
    "credit balance",
)


def detect_login_error(*chunks: str) -> bool:
    """출력에 로그인/인증 실패 문구가 있으면 True."""
    blob = "\n".join(c for c in chunks if c).lower()
    return any(p in blob for p in _LOGIN_PATTERNS)


def detect_rate_limit(*chunks: str) -> bool:
    """출력에 사용량 한도/쿼터 문구가 있으면 True."""
    blob = "\n".join(c for c in chunks if c).lower()
    return any(p in blob for p in _RATE_LIMIT_PATTERNS)


def tail_lines(text: str, n: int = 20) -> str:
    """stderr 등의 마지막 n 줄만 남긴다 (DESIGN §8.2)."""
    if not text:
        return ""
    lines = text.rstrip().splitlines()
    return "\n".join(lines[-n:])


def stage_media(paths, cwd: str) -> list:
    """미디어 파일을 CLI 의 cwd 안으로 옮겨 상대경로로 참조할 수 있게 한다.

    CLI 의 파일 읽기 툴은 작업 폴더 밖을 못 보는 경우가 많으므로,
    cwd 밖에 있는 파일만 복사한다. 반환값은 cwd 기준 상대경로 리스트.
    """
    import shutil

    staged = []
    if not paths or not cwd:
        return staged

    root = os.path.realpath(cwd)
    for path in paths:
        try:
            real = os.path.realpath(path)
            if real == root or real.startswith(root + os.sep):
                staged.append(os.path.relpath(real, root))
                continue
            dest = os.path.join(root, os.path.basename(real))
            if os.path.realpath(dest) != real:
                shutil.copyfile(real, dest)
            staged.append(os.path.relpath(dest, root))
        except OSError:
            continue
    return staged


def frames_for_unsupported_video(req: LLMRequest, backend_name: str, out_dir: str = "") -> tuple:
    """비디오를 지원하지 않는 백엔드용: 프레임을 뽑아 이미지 경로로 돌려준다.

    반환: (프레임 PNG 경로 리스트, 안내 메시지 리스트)
    """
    from ..utils import video_io

    frames, notes = [], []
    for path in req.video_paths or []:
        target_dir = out_dir or os.path.join(os.path.dirname(path), "_llmhub_frames")
        extracted, message = video_io.extract_frames(path, req.video_max_frames, target_dir)
        if message:
            notes.append(f"{backend_name}: 비디오 미지원 → {message}")
        frames.extend(extracted)
    return frames, notes


def unsupported_note(backend: str, *names: str) -> str:
    """미지원 파라미터를 debug 에 남길 문구 (DESIGN §5-5)."""
    if not names:
        return ""
    return f"unsupported: {', '.join(names)} ({backend} CLI 는 해당 파라미터를 노출하지 않음)"
