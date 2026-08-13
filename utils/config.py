# -*- coding: utf-8 -*-
"""config.json 로더 (DESIGN §10).

외부 의존성 없음(표준 라이브러리만). 최초 호출 시 config.json 이 없으면
config.example.json 을 복사해서 만든다.
"""

from __future__ import annotations

import json
import os
import shutil
import threading

_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(_PACK_ROOT, "config.json")
EXAMPLE_PATH = os.path.join(_PACK_ROOT, "config.example.json")
# 토큰을 파일 하나로 따로 두는 경로. config.json 과 달리 이 파일은 내용 전체가 토큰이라
# 실수로 통째 붙여넣기 어렵고, 다른 노드팩과도 같은 관례를 쓸 수 있다.
TOKEN_PATH = os.path.join(_PACK_ROOT, "lm_studio_token.txt")
TOKEN_ENV_VAR = "LM_STUDIO_API_KEY"

_LOCK = threading.Lock()
_CACHE = None

DEFAULTS = {
    "lmstudio": {
        "base_url": "http://127.0.0.1:1234",
        "api_token": "",
        "default_model": "",
        # 유휴 TTL(초). LM Studio 가 이 시간 동안 요청이 없으면 모델을 VRAM 에서 내린다.
        # 0 이면 ttl 을 보내지 않는다(LM Studio 기본값 60분 적용).
        "ttl_sec": 300,
        # 응답 직후 `lms unload <model>` 로 즉시 VRAM 을 비울지 여부.
        # ComfyUI 는 이미지 모델이 VRAM 을 써야 하므로 기본으로 켠다.
        "unload_after": True,
    },
    # lms 는 LM Studio 의 CLI (즉시 언로드에 사용)
    # OpenAI 호환 서버 (Ollama / vLLM / llama.cpp 등).
    # base_url 만 맞추면 LM Studio 와 같은 코드 경로를 탄다.
    "openai_compat": {
        "base_url": "http://127.0.0.1:11434",
        "api_token": "",
        "default_model": "",
    },
    "cli_paths": {"claude": "claude", "codex": "codex", "gemini": "gemini", "lms": "lms"},
    "defaults": {
        # 실측(2026-08-12, gemini-cli 0.55.1): "gemini-3-flash" 는 CLI 가
        # gemini-3.5-flash 로 풀어준다. 별칭이라 최신판을 따라가므로 버전을 박은
        # 이름보다 오래 간다. 참고로 CLI 기본값은 gemini-3.1-flash-lite 라
        # 이 값을 비워두면 더 작은 모델이 붙는다.
        # 노드의 model 칸에 적으면 이 값보다 우선한다.
        "gemini_model": "gemini-3-flash",
        # Gemini CLI 의 승인 모드. "plan" = 읽기 전용(실측 §8.4).
        # 응답 형식이 계획서처럼 나오면 "default" 로 바꿀 수 있게 열어둔다.
        "gemini_approval_mode": "plan",
        # claude 시스템 프롬프트 적용 방식.
        #   "append"  = Claude Code 기본 시스템 프롬프트에 덧붙임(도구 사용 능력 유지)
        #   "replace" = 기본 프롬프트를 통째로 교체(스타일 지시를 강하게 먹이고 싶을 때)
        # 실측: append 모드에서는 기본 프롬프트가 강해 문체/언어 지시가 희석될 수 있다.
        "claude_system_prompt_mode": "append",
    },
    "tool_loop_max_iters": 8,
    "max_file_read_bytes": 262144,
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(force_reload: bool = False) -> dict:
    """config.json 을 읽어 DEFAULTS 위에 병합한 dict 를 돌려준다."""
    global _CACHE
    with _LOCK:
        if _CACHE is not None and not force_reload:
            return _CACHE

        user_cfg = {}
        try:
            if not os.path.exists(CONFIG_PATH) and os.path.exists(EXAMPLE_PATH):
                shutil.copyfile(EXAMPLE_PATH, CONFIG_PATH)
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                    user_cfg = json.load(fh) or {}
        except Exception:
            # 설정 파일이 깨져도 노드는 기본값으로 동작해야 한다 (N4).
            user_cfg = {}

        _CACHE = _deep_merge(DEFAULTS, user_cfg)
        return _CACHE


def resolve_api_token(cfg: dict = None) -> str:
    """LM Studio 의 API 토큰을 찾아 돌려준다 (없으면 빈 문자열).

    LM Studio 의 "Enable API key" 를 켜면 /v1/models 까지 401 이 된다. 그러면
    모델 목록이 빈 리스트가 되고 노드의 lmstudio_model 콤보에는 "(auto)" 하나만
    남아 — 드롭다운이 아예 안 뜬 것처럼 보인다. 그래서 토큰을 config.json 한 곳에서만
    찾으면 안 된다: 런처가 환경변수로 넣어두는 설치가 흔하다.

    찾는 순서:
      1. config.json 의 lmstudio.api_token   (명시적으로 적었으면 그게 우선)
      2. 환경변수 LM_STUDIO_API_KEY          (런처/셸에서 주입)
      3. lm_studio_token.txt                 (파일 내용 전체가 토큰)

    돌려준 값은 절대 로그·debug 출력에 실으면 안 된다 (DESIGN §10).
    """
    if cfg is None:
        cfg = load_config()
    token = ((cfg.get("lmstudio", {}) or {}).get("api_token") or "").strip()
    if token:
        return token

    token = (os.environ.get(TOKEN_ENV_VAR) or "").strip()
    if token:
        return token

    try:
        if os.path.exists(TOKEN_PATH):
            with open(TOKEN_PATH, "r", encoding="utf-8") as fh:
                # 파일 끝 줄바꿈이 그대로 헤더에 들어가면 서버가 토큰을 거부한다.
                return fh.read().strip()
    except Exception:
        pass
    return ""


def get_cli_path(name: str) -> str:
    """config.json 의 cli_paths 에서 실행 파일 이름/절대경로를 얻는다."""
    cfg = load_config()
    return (cfg.get("cli_paths", {}) or {}).get(name) or name
