# -*- coding: utf-8 -*-
"""백엔드 팩토리 (DESIGN §4)."""

from __future__ import annotations

from .base import BaseBackend, LLMRequest, LLMResponse

# openai_compat 과 똑같은 구현을 쓰되, 드롭다운에서 바로 고를 수 있게 한 별칭.
# 서버마다 표준 포트가 정해져 있어서 고르기만 하면 주소가 잡힌다.
#
# 별칭을 만든 이유는 발견성이다. llama.cpp 를 쓰려면 "openai_compat 이 그거다" 를
# 먼저 알아야 했는데, 드롭다운 어디에도 llama.cpp 라는 글자가 없었다.
OPENAI_COMPAT_ALIASES = ("ollama", "vllm", "llamacpp")

# 새 이름은 반드시 "맨 뒤에만" 붙인다. 저장된 워크플로우가 콤보를 어떻게 들고
# 있든(문자열이든 인덱스든) 뒤에 붙이는 것은 안전하지만, 중간에 끼우거나 순서를
# 바꾸면 예전 워크플로우가 다른 백엔드로 열릴 수 있다.
BACKEND_NAMES = [
    "lmstudio", "claude", "codex", "gemini", "openai_compat",
    "ollama", "vllm", "llamacpp",
]


def get_backend(name: str) -> BaseBackend:
    """이름으로 백엔드 인스턴스를 만든다. 모듈은 필요할 때만 import 한다."""
    key = (name or "").strip().lower()

    if key == "lmstudio":
        from .lmstudio import LMStudioBackend

        return LMStudioBackend()
    if key == "claude":
        from .claude_code import ClaudeCodeBackend

        return ClaudeCodeBackend()
    if key == "codex":
        from .codex import CodexBackend

        return CodexBackend()
    if key == "openai_compat" or key in OPENAI_COMPAT_ALIASES:
        from .openai_compat import KNOWN_SERVERS, OpenAICompatBackend

        # 별칭은 자기 표준 포트를 기본값으로 들고 시작한다.
        impl = OpenAICompatBackend(base_url_default=KNOWN_SERVERS.get(key, ""))
        if key != "openai_compat":
            # debug/오류 문구가 "llamacpp: ..." 로 나오게 한다. 사용자가 고른
            # 이름 그대로 말해주지 않으면 어느 서버 얘기인지 알 수 없다.
            impl.name = key
        return impl
    if key == "gemini":
        from .gemini import GeminiBackend

        return GeminiBackend()

    raise ValueError(f"Unknown backend: {name} (available: {', '.join(BACKEND_NAMES)})")


__all__ = [
    "get_backend", "BACKEND_NAMES", "OPENAI_COMPAT_ALIASES",
    "BaseBackend", "LLMRequest", "LLMResponse",
]
