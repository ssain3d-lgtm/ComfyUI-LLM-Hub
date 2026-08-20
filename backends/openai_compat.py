# -*- coding: utf-8 -*-
"""OpenAI 호환 서버 백엔드 (Ollama / vLLM / llama.cpp 등).

셋 다 /v1/chat/completions 를 그대로 제공한다. 그래서 새 구현이 아니라
LM Studio 백엔드를 그대로 물려받아 base_url 만 바꾼다 -- 검증된 경로를
재사용하는 것이 새로 짜는 것보다 안전하다.

  Ollama     http://127.0.0.1:11434
  vLLM       http://127.0.0.1:8000
  llama.cpp  http://127.0.0.1:8080

LM Studio 전용이라 갈라야 하는 것은 셋뿐이다.

  ttl 필드   : LM Studio 전용이다. 모르는 필드에 엄격한 서버는 400 을 낼 수
               있으므로 여기서는 보내지 않는다. Ollama 는 keep_alive 라는
               자기 필드를 쓰는데, 확인 못 한 것을 넣지 않는다(§0-5 와 같은 이유).
  lms unload : LM Studio CLI 다. 다른 서버에는 없으므로 안내만 남긴다.
  /api/v0/models : LM Studio 전용 확장이다. 모델은 노드의 model 칸에 직접 적는다.

주의: 이 백엔드는 실기기 검증을 하지 못했다. LM Studio 로 검증된 코드 경로를
그대로 쓰지만, 서버마다 다른 부분(SSE 청크 모양, 오류 응답 형식)은 실측 없이
확인할 수 없다. README 에도 같은 취지를 적어둔다.
"""

from __future__ import annotations

import time

import os
from .base import LLMRequest, LLMResponse, truncate_debug
from .lmstudio import LMStudioBackend

DEFAULT_BASE_URL = "http://127.0.0.1:11434"  # Ollama 기본 포트

# 사용자가 자주 쓸 주소. README 와 노드 tooltip 에서 함께 쓴다.
KNOWN_SERVERS = {
    "ollama": "http://127.0.0.1:11434",
    "vllm": "http://127.0.0.1:8000",
    "llamacpp": "http://127.0.0.1:8080",
}


class OpenAICompatBackend(LMStudioBackend):
    name = "openai_compat"

    def __init__(self, config: dict = None, base_url_default: str = ""):
        # 부모의 LM Studio 설정을 먼저 읽은 뒤 이 백엔드 것으로 덮어쓴다.
        super().__init__(config=config)
        section = (self.config.get("openai_compat", {}) or {})
        # base_url_default 는 별칭 백엔드(ollama/vllm/llamacpp)가 넘기는 표준 포트다.
        # config 값보다 앞에 두는 이유: 드롭다운에서 "llamacpp" 를 고른 것 자체가
        # 어느 서버를 쓸지 명시한 것이라, 범용 설정값이 그걸 덮으면 놀란다.
        # 노드의 openai_base_url 을 채우면 apply_base_url 로 그쪽이 최종적으로 이긴다.
        self.base_url = (
            base_url_default or section.get("base_url") or DEFAULT_BASE_URL
        ).rstrip("/")
        # 토큰은 이 백엔드 것만 본다. LM Studio 의 LM_STUDIO_API_KEY 를 끌어다
        # 쓰면 엉뚱한 서버에 남의 토큰을 보내게 된다.
        self.api_token = (
            section.get("api_token")
            or os.environ.get("OPENAI_COMPAT_API_KEY", "")
            or ""
        ).strip()
        self.default_model = section.get("default_model") or ""
        # ttl / unload 는 이 백엔드에 없다. 부모가 읽어둔 LM Studio 값을 지운다.
        self.default_ttl_sec = 0
        self.default_unload_after = False

    def apply_base_url(self, base_url: str) -> None:
        """노드에서 넘어온 주소로 갈아탄다. 빈 값이면 설정값을 유지한다."""
        url = (base_url or "").strip().rstrip("/")
        if url:
            self.base_url = url

    def _build_payload(self, req: LLMRequest, messages: list, model: str) -> dict:
        payload = super()._build_payload(req, messages, model)
        # LM Studio 전용 필드다. 엄격한 서버는 모르는 필드에 400 을 낸다.
        payload.pop("ttl", None)
        return payload

    def unload_model(self, model_id: str) -> str:
        return (
            "unload: this backend has no immediate unload. "
            "Use `ollama stop <model>`, or shut down the vLLM / llama.cpp server."
        )

    def _map_exception(self, exc: Exception, started: float, debug_notes: list) -> LLMResponse:
        import requests

        duration = time.time() - started
        if isinstance(exc, requests.ConnectionError):
            return LLMResponse(
                status=(
                    f"error: no response from the server ({self.base_url}). "
                    "Check the address and that the server is running "
                    "(Ollama 11434 / vLLM 8000 / llama.cpp 8080)"
                ),
                duration_s=duration,
                raw_debug=truncate_debug("\n".join(debug_notes + [repr(exc)])),
            )
        return super()._map_exception(exc, started, debug_notes)
