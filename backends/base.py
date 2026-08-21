# -*- coding: utf-8 -*-
"""공통 데이터 모델 / 백엔드 인터페이스 / CLI 백엔드 공용 헬퍼 (DESIGN §6, §7, §8.5)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

# raw_debug 는 앞뒤 합쳐 최대 4000자로 절단한다 (DESIGN §6).
MAX_DEBUG_CHARS = 4000

# file_access=True 일 때 시스템 프롬프트 끝에 자동 주입되는 안내 한 줄 (DESIGN §7).
WORKSPACE_HINT_TEMPLATE = (
    "The working root folder is {workspace_dir}. "
    "If you need files, list them first and read only the ones you need."
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
    # 샘플링 시드. 0 이면 보내지 않는다(= 서버 기본값 = 지금까지의 동작).
    # HTTP 백엔드만 본다. CLI 3종에는 대응하는 플래그가 없다.
    seed: int = 0
    # HTTP 백엔드의 payload 에 그대로 합칠 추가 필드 (extra_args 의 HTTP 판).
    # nodes.py 에서 JSON 문자열을 파싱해 넘긴다.
    extra_body: dict = field(default_factory=dict)
    # LM Studio 전용. None = config.json 의 lmstudio 설정을 따름.
    # ttl_sec: 유휴 TTL(초), 0 이면 ttl 을 보내지 않는다.
    # unload_after: 응답 직후 lms unload 로 VRAM 을 비울지.
    ttl_sec: object = None
    unload_after: object = None
    timeout_s: int = 300
    extra_args: str = ""
    # openai_compat 전용: 노드에서 넘긴 서버 주소(비면 config 값을 쓴다)
    base_url_override: str = ""
    # 실시간 모니터링용 StreamEmitter (없으면 스트리밍하지 않는다)
    emitter: object = None


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
        return "error: workspace_dir needed (file_access is on but the path is empty)"
    if not os.path.isdir(ws):
        return f"error: workspace_dir needed (folder does not exist: {ws})"
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


def server_error_reason(body: str, limit: int = 200) -> str:
    """HTTP 오류 본문에서 status 에 실을 한 줄을 뽑는다. 못 알아보면 빈 문자열.

    코드만 돌려주면(`HTTP 500`) 사용자는 debug 를 열기 전까지 무슨 일인지 알 수
    없다. 실측 사례: llama.cpp 라우터가 못 뜨는 모델에 대해 내는
    `{"error":{"message":"model name=... failed to load"}}` 가 그랬다 --
    화면에는 `error: LM Studio HTTP 500` 한 줄뿐이라 라우터가 죽은 줄 알았다.

    본문 모양은 서버마다 다르다. 아는 모양만 읽고, 모르면 빈 문자열을 준다 --
    원문은 어차피 debug 에 통째로 남으므로 여기서 지어낼 이유가 없다.
      {"error": {"message": "..."}}   OpenAI / llama.cpp / LM Studio
      {"error": "..."}                Ollama 등
      {"message": "..."}
    """
    text = (body or "").strip()
    if not text:
        return ""

    try:
        data = json.loads(text)
    except Exception:
        # JSON 이 아니면 프록시가 낀 HTML 일 때가 많다. 통째로 실으면 status 가
        # 태그로 뒤덮이므로 아래 한 줄 접기/자르기에만 맡긴다.
        data = None

    if isinstance(data, dict):
        error = data.get("error", data)
        if isinstance(error, dict):
            text = str(error.get("message") or error.get("detail") or "").strip()
        elif isinstance(error, str):
            text = error.strip()
        else:
            text = ""
        if not text:
            text = str(data.get("message") or "").strip()

    # status 는 노드에 한 줄로 보이는 자리다. 줄바꿈은 접고 길면 자른다.
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


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
            # cwd 안에 같은 이름의 다른 파일이 이미 있으면 덮어쓰지 않는다
            # (사용자의 원본 파일 파괴 방지). 전용 하위 폴더에 넣는다.
            media_dir = os.path.join(root, "_llmhub_media")
            os.makedirs(media_dir, exist_ok=True)
            dest = os.path.join(media_dir, os.path.basename(real))
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
    for index, path in enumerate(req.video_paths or []):
        base_dir = out_dir or os.path.dirname(path)
        # 영상마다 별도 폴더에 뽑아 서로의 프레임을 덮어쓰지 않게 한다.
        target_dir = os.path.join(base_dir, f"_llmhub_frames_{index}")
        extracted, message = video_io.extract_frames(path, req.video_max_frames, target_dir)
        if message:
            notes.append(f"{backend_name}: no native video support -> {message}")
        frames.extend(extracted)
    return frames, notes


def unsupported_note(backend: str, *names: str) -> str:
    """미지원 파라미터를 debug 에 남길 문구 (DESIGN §5-5)."""
    if not names:
        return ""
    return f"unsupported: {', '.join(names)} ({backend} CLI does not expose this parameter)"


# --- extra_body (HTTP 백엔드용 추가 payload 필드) ----------------------------

# payload 의 뼈대라 사용자가 덮어쓰면 코드가 망가지는 키.
#   messages : 이 노드가 만든 대화 자체다.
#   stream   : 스트리밍 여부는 stream_view 위젯과 툴 루프가 결정한다.
RESERVED_PAYLOAD_KEYS = ("messages", "stream")
# file_access=True 일 때만 추가로 잠근다. 툴 루프가 자기가 선언한 스키마를
# 그대로 되받는다는 전제로 돌아가므로, 여기를 바꾸면 루프가 어긋난다.
RESERVED_WHEN_TOOLS = ("tools", "tool_choice")


def extra_body_ignored_note(backend: str, req: LLMRequest) -> str:
    """CLI 백엔드용 안내. extra_body 는 HTTP payload 에 합치는 물건이라
    프로세스를 띄우는 백엔드에는 합칠 자리가 없다. 조용히 버리지 않는다."""
    if not req.extra_body:
        return ""
    return (
        f"{backend}: extra_body is ignored by this CLI backend "
        "(it has no HTTP payload — use extra_args for CLI flags)"
    )


def parse_extra_body(text: str) -> tuple:
    """extra_body 위젯의 JSON 문자열을 dict 로 바꾼다.

    반환: (dict, error_message). error_message 가 비어 있지 않으면 실패다.

    조용히 무시하지 않는 이유: extra_args 가 HTTP 백엔드에서 debug 한 줄만
    남기고 버려지던 것이 이 위젯을 만든 계기다. 사용자가 적은 것이 반영도
    안 되고 말도 안 해주면 같은 함정을 한 번 더 파는 것이다.
    """
    raw = (text or "").strip()
    if not raw:
        return {}, ""
    import json as _json

    try:
        parsed = _json.loads(raw)
    except ValueError as exc:
        return {}, f"error: extra_body is not valid JSON - {exc}"
    if not isinstance(parsed, dict):
        return {}, (
            "error: extra_body must be a JSON object "
            f'(got {type(parsed).__name__}). Example: {{"top_p": 0.9}}'
        )
    return parsed, ""


def merge_extra_body(payload: dict, extra_body: dict, file_access: bool = False) -> list:
    """extra_body 를 payload 에 합친다. 반환: debug 에 남길 안내 리스트."""
    if not extra_body:
        return []

    reserved = set(RESERVED_PAYLOAD_KEYS)
    if file_access:
        reserved.update(RESERVED_WHEN_TOOLS)

    applied, rejected = [], []
    for key, value in extra_body.items():
        if key in reserved:
            rejected.append(key)
            continue
        payload[key] = value
        applied.append(key)

    notes = []
    if applied:
        notes.append(f"extra_body: applied {', '.join(sorted(applied))}")
    if rejected:
        notes.append(
            "extra_body: ignored "
            + ", ".join(sorted(rejected))
            + " (these are built by the node itself)"
        )
    return notes


# --- usage / cost 표기 통일 -------------------------------------------------

# 백엔드마다 키 이름이 다르다. 확인된 이름만 넣는다(추측 금지, §0-5).
#   OpenAI 호환  : prompt_tokens / completion_tokens / total_tokens
#   claude CLI   : input_tokens / output_tokens (+ 캐시 관련 키)
_USAGE_ALIASES = {
    "prompt": ("prompt_tokens", "input_tokens"),
    "completion": ("completion_tokens", "output_tokens"),
    "total": ("total_tokens",),
}


def format_usage(usage=None, cost_usd=None) -> str:
    """토큰 사용량/비용을 debug 한 줄로 통일한다.

    아무것도 못 찾으면 빈 문자열을 돌려준다 -- 백엔드마다 주는 것이 달라서,
    없는 항목을 0 으로 적으면 "0 토큰을 썼다" 는 거짓말이 된다.
    """
    parts = []
    numbers = {}
    if isinstance(usage, dict):
        for label, keys in _USAGE_ALIASES.items():
            for key in keys:
                value = usage.get(key)
                if isinstance(value, (int, float)):
                    numbers[label] = int(value)
                    break
        # total 을 안 주는 백엔드가 있어서 둘 다 있으면 직접 더한다.
        if "total" not in numbers and "prompt" in numbers and "completion" in numbers:
            numbers["total"] = numbers["prompt"] + numbers["completion"]
        for label in ("prompt", "completion", "total"):
            if label in numbers:
                parts.append(f"{label}={numbers[label]}")
        # 캐시 토큰은 있을 때만 덧붙인다(claude 만 준다).
        cached = usage.get("cache_read_input_tokens")
        if isinstance(cached, (int, float)) and cached:
            parts.append(f"cached={int(cached)}")

    if isinstance(cost_usd, (int, float)):
        # 한 번 호출에 $0.0001 단위가 흔해서 소수 4자리까지 적는다.
        parts.append(f"cost=${cost_usd:.4f}")

    if not parts:
        return ""
    return "usage: " + " ".join(parts)
