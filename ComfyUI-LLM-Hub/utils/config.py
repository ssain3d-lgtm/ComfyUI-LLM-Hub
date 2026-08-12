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

_LOCK = threading.Lock()
_CACHE = None

DEFAULTS = {
    "lmstudio": {
        "base_url": "http://127.0.0.1:1234",
        "api_token": "",
        "default_model": "",
    },
    "cli_paths": {"claude": "claude", "codex": "codex", "gemini": "gemini"},
    "defaults": {
        "gemini_model": "gemini-2.5-flash",
        # Gemini CLI 의 승인 모드. "plan" = 읽기 전용(실측 §8.4).
        # 응답 형식이 계획서처럼 나오면 "default" 로 바꿀 수 있게 열어둔다.
        "gemini_approval_mode": "plan",
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


def get_cli_path(name: str) -> str:
    """config.json 의 cli_paths 에서 실행 파일 이름/절대경로를 얻는다."""
    cfg = load_config()
    return (cfg.get("cli_paths", {}) or {}).get(name) or name
