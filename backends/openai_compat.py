# -*- coding: utf-8 -*-
"""OpenAI 호환 서버 백엔드 (Ollama / vLLM / llama.cpp / NInfer 등).

모두 /v1/chat/completions 를 그대로 제공한다. 그래서 새 구현이 아니라
LM Studio 백엔드를 그대로 물려받아 base_url 만 바꾼다 -- 검증된 경로를
재사용하는 것이 새로 짜는 것보다 안전하다.

  Ollama     http://127.0.0.1:11434
  vLLM       http://127.0.0.1:8000
  llama.cpp  http://127.0.0.1:8080
  NInfer     http://127.0.0.1:8081

LM Studio 전용이라 갈라야 하는 것은 셋뿐이다.

  ttl 필드   : LM Studio 전용이다. 모르는 필드에 엄격한 서버는 400 을 낼 수
               있으므로 여기서는 보내지 않는다. Ollama 는 keep_alive 라는
               자기 필드를 쓰는데, 확인 못 한 것을 넣지 않는다(§0-5 와 같은 이유).
  lms unload : LM Studio CLI 다. 다른 서버에는 없으므로 안내만 남긴다.
  /api/v0/models : LM Studio 전용 확장이다. 모델은 노드의 model 칸에 직접 적는다.

주의: Ollama / vLLM / llama.cpp 는 실기기 검증을 하지 못했다. LM Studio 로 검증된 코드 경로를
그대로 쓰지만, 서버마다 다른 부분(SSE 청크 모양, 오류 응답 형식)은 실측 없이
확인할 수 없다. README 에도 같은 취지를 적어둔다.

NInfer 만 실기기로 확인했다 (2026-09-19~21, ninfer-serve, Qwen3.8-27B NVFP4, RTX 5090):
  - 모르는 최상위 필드(ttl, keep_alive, llama.cpp 전용 필드)는 무시하고 200. 그래도 ttl 은 다른 별칭처럼 보내지 않는다.
  - 스트리밍 thinking 은 delta.reasoning_content, 응답에 llama.cpp 호환 timings. seed, stream_options.include_usage 수용.
  - tools + tool_choice "auto" 지원 (required / named tool choice, strict:true 는 거부).
  - 요청의 model 은 서버의 공개 ID(/v1/models)와 **같아야 한다**. server_model 드롭다운이 그 ID 를 읽어 온다.
  - 거부하는 것: top_k > 20, 음수 max_tokens, JSON 강제 출력, logprobs, 오디오 입력.
  - 언로드 API 도 유휴 언로드도 없다. 프로세스가 사는 동안 모델이 VRAM 에 상주한다 -> unload_model 참고.
  - thinking 은 기본으로 켜져 있다. 끄려면 extra_body 에 {"reasoning_effort": "none"}.
    ("/no_think" 텍스트는 효과가 없고 프롬프트만 오염시킨다. top-level enable_thinking 도 듣지 않는다 -- 실측.)
"""

from __future__ import annotations

import time

import os
from .base import LLMRequest, LLMResponse, truncate_debug
from .lmstudio import LMStudioBackend, is_loopback

DEFAULT_BASE_URL = "http://127.0.0.1:11434"  # Ollama 기본 포트

# 드롭다운 표시값. nodes.AUTO_MODEL 과 같은 글자다. 여기서 nodes 를 임포트하면
# 순환(nodes -> backends -> nodes)이 되므로 글자를 복사해 둔다 -- 테스트가
# nodes.AUTO_MODEL 로 이 함수를 불러 둘이 같음을 확인한다.
AUTO_SENTINEL = "(auto)"

# 사용자가 자주 쓸 주소. README 와 노드 tooltip 에서 함께 쓴다.
KNOWN_SERVERS = {
    "ollama": "http://127.0.0.1:11434",
    "vllm": "http://127.0.0.1:8000",
    "llamacpp": "http://127.0.0.1:8080",
    "ninfer": "http://127.0.0.1:8081",
}


class OpenAICompatBackend(LMStudioBackend):
    name = "openai_compat"

    def __init__(self, config: dict = None, base_url_default: str = ""):
        # 부모의 LM Studio 설정을 먼저 읽은 뒤 이 백엔드 것으로 덮어쓴다.
        super().__init__(config=config)
        section = (self.config.get("openai_compat", {}) or {})
        # base_url_default 는 별칭 백엔드(ollama/vllm/llamacpp/ninfer)가 넘기는 표준 포트다.
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
        """노드에서 넘어온 주소로 갈아탄다. 빈 값이면 설정값을 유지한다.

        "(auto)" 도 빈 값으로 본다. 실제 증상(2026-08-22): 예전 위젯 순서
        밀림 때 옆 드롭다운의 "(auto)" 가 openai_base_url 칸에 저장된 워크플로우가
        있었고, llamacpp 로 바꾸는 순간 requests 가
        `MissingSchema: Invalid URL '(auto)/v1/chat/completions'` 를 냈다.
        lmstudio/gemini 에서는 이 칸을 안 보니 오래 숨어 있었다.

        스킴 없는 주소("localhost:8080")는 requests 의 InvalidSchema 보다 먼저
        사람이 읽을 수 있는 말로 멈춘다. 노드는 이 ValueError 를 status 로 바꾼다.
        """
        url = (base_url or "").strip().rstrip("/")
        if not url or url.lower() == AUTO_SENTINEL:
            return
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError(
                f"openai_base_url must start with http:// or https:// (got '{url}'). "
                "Leave the box empty to use the standard port."
            )
        self.base_url = url

    def _build_payload(self, req: LLMRequest, messages: list, model: str) -> dict:
        payload = super()._build_payload(req, messages, model)
        # LM Studio 전용 필드다. 엄격한 서버는 모르는 필드에 400 을 낸다.
        payload.pop("ttl", None)
        return payload

    def unload_model(self, model_id: str) -> str:
        if self.name == "ninfer":
            # 이 노드의 unload 는 "생성 후 VRAM 비우기" 다. NInfer 에는 그 수단이 아예 없다 -- 언로드 API 도,
            # 유휴 언로드도 없고 GPU 상주가 프로세스 시작 때 고정된다. 다른 서버용 안내(`ollama stop`)를
            # 그대로 내면 틀린 안내이고, 내렸다고 하면 거짓말이다.
            return (
                "unload: NInfer keeps the model in VRAM for as long as its process lives - there is no "
                "unload API and no idle unload. To free the VRAM, stop the server (the Stop button of "
                "the NInfer config UI, or end the ninfer-serve process)."
            )
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
                    f"error: no response from the {self.name} server ({self.base_url}). "
                    "Check the address and that the server is running "
                    "(Ollama 11434 / vLLM 8000 / llama.cpp 8080 / NInfer 8081)"
                ),
                duration_s=duration,
                raw_debug=truncate_debug("\n".join(debug_notes + [repr(exc)])),
            )
        return super()._map_exception(exc, started, debug_notes)


# ---------------------------------------------------------------------------
# 모델 목록 조회 (server_model 드롭다운용)
# ---------------------------------------------------------------------------

_MODEL_CACHE = {"at": 0.0, "ids": []}
_MODEL_CACHE_TTL = 10.0  # 초. INPUT_TYPES 가 자주 불려도 서버를 계속 두드리지 않게.

# is_loopback 은 lmstudio 로 옮겼다(그쪽도 같은 판정이 필요해졌고,
# 이 모듈이 lmstudio 를 상속하므로 반대 방향 import 는 순환이 된다).


def _probe(base_url: str, timeout_s: float, headers: dict) -> list:
    """한 서버의 /v1/models 를 읽는다. 실패하면 빈 리스트."""
    import requests

    try:
        resp = requests.get(base_url + "/v1/models", headers=headers, timeout=timeout_s)
        if resp.status_code != 200:
            return []
        data = (resp.json() or {}).get("data") or []
    except Exception:
        return []
    return [m.get("id") for m in data if isinstance(m, dict) and m.get("id")]


def list_server_models(timeout_s: float = 1.5) -> list:
    """켜져 있는 OpenAI 호환 서버들의 모델 id 를 모아 돌려준다.

    **내 컴퓨터(loopback) 주소만 조회한다.** 이유가 두 가지다.

    1. INPUT_TYPES 는 /object_info 요청마다 불린다. 원격 주소를 물면 페이지를
       열 때마다 네트워크 왕복이 붙는다. 안 켜진 로컬 포트는 즉시 거절당해
       사실상 공짜지만 원격은 타임아웃까지 기다린다.
    2. config 에 유료 API 주소를 적어둔 사람은, 목록 하나 채우자고 페이지를
       열 때마다 남의 서버로 요청이 나가게 된다. 그럴 일이 아니다.

    그래서 표준 포트 3개와, config 의 base_url 이 loopback 일 때만 그것을 본다.
    원격/유료 서버를 쓰는 경우 모델 이름은 model 칸에 직접 적으면 된다.

    토큰은 config 에 적힌 주소에만 보낸다. 표준 포트 3개는 "내 컴퓨터에 떠 있는
    아무 서버" 라 누구 것인지 알 수 없고, 거기에 토큰을 뿌릴 이유가 없다
    (LM Studio 토큰을 재사용하지 않는 것과 같은 이유).

    실패해도 경고하지 않는다. 이 백엔드를 안 쓰는 사람이 대다수인데, 서버가
    안 떠 있다고 매번 로그를 남기면 그게 거짓 경보다.
    """
    # 테스트/CI 는 이 조회를 끈다. 안 그러면 스위트가 개발 PC 에 떠 있는
    # 진짜 서버를 두드리게 되고(포트 1234/11434/8000/8080), 결과가
    # 머신마다 달라진다. 예전에 테스트가 사용자 서버에 실제로 lms unload
    # 를 쐈던 사고와 같은 부류다.
    if os.environ.get("LLMHUB_SKIP_MODEL_PROBE"):
        return []

    now = time.time()
    if now - _MODEL_CACHE["at"] < _MODEL_CACHE_TTL:
        return list(_MODEL_CACHE["ids"])

    ids = []
    try:
        from ..utils.config import load_config

        section = (load_config().get("openai_compat", {}) or {})
        configured = (section.get("base_url") or "").strip().rstrip("/")
        token = (
            section.get("api_token") or os.environ.get("OPENAI_COMPAT_API_KEY", "") or ""
        ).strip()

        targets = [(url, {}) for url in KNOWN_SERVERS.values()]
        if configured and is_loopback(configured) and configured not in KNOWN_SERVERS.values():
            targets.append(
                (configured, {"Authorization": f"Bearer {token}"} if token else {})
            )

        for base, headers in targets:
            for model_id in _probe(base, timeout_s, headers):
                if model_id not in ids:
                    ids.append(model_id)
    except Exception:
        ids = []

    _MODEL_CACHE["at"] = now
    _MODEL_CACHE["ids"] = ids
    return list(ids)
