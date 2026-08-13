# -*- coding: utf-8 -*-
"""백엔드 팩토리 (DESIGN §4)."""

from __future__ import annotations

from .base import BaseBackend, LLMRequest, LLMResponse

BACKEND_NAMES = ["lmstudio", "claude", "codex", "gemini", "openai_compat"]


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
    if key == "openai_compat":
        from .openai_compat import OpenAICompatBackend

        return OpenAICompatBackend()
    if key == "gemini":
        from .gemini import GeminiBackend

        return GeminiBackend()

    raise ValueError(f"Unknown backend: {name} (available: {', '.join(BACKEND_NAMES)})")


__all__ = ["get_backend", "BACKEND_NAMES", "BaseBackend", "LLMRequest", "LLMResponse"]
