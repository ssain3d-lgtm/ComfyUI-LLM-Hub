# -*- coding: utf-8 -*-
"""시스템 프롬프트 프리셋.

자주 쓰는 시스템 프롬프트를 `system_prompts.json` 에 미리 적어두고 노드의
드롭다운에서 고른다. 매번 같은 문장을 다시 타이핑하지 않기 위한 것이다.

파일은 사용자가 손으로 고치는 것이므로 `config.json` 과 같은 규칙을 따른다.
  - git 에 올라가지 않는다(.gitignore)
  - 없으면 `system_prompts.example.json` 을 복사해 만든다
  - 그래서 `git pull` 이 사용자의 프리셋을 덮어쓰지 않는다

prompt 는 문자열이거나 문자열 목록이다. 목록이면 줄바꿈으로 잇는다 --
JSON 한 줄에 \\n 을 잔뜩 박아 넣는 것보다 손으로 고치기 훨씬 낫다.
"""

from __future__ import annotations

import json
import os
import shutil

# 드롭다운 첫 항목. 프리셋을 쓰지 않겠다는 뜻이다.
PRESET_NONE = "(none)"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESET_PATH = os.path.join(_ROOT, "system_prompts.json")
EXAMPLE_PATH = os.path.join(_ROOT, "system_prompts.example.json")

# 파일이 바뀌면 다시 읽는다. INPUT_TYPES 가 자주 불리므로 매번 디스크를 읽지
# 않되, 사용자가 파일을 고치면 브라우저 새로고침만으로 반영되게 하려는 것이다.
_CACHE = {"mtime": None, "presets": {}}


def _ensure_file() -> None:
    """없으면 예제를 복사한다. 실패해도 조용히 넘어간다(프리셋은 필수가 아니다)."""
    if os.path.exists(PRESET_PATH) or not os.path.exists(EXAMPLE_PATH):
        return
    try:
        shutil.copyfile(EXAMPLE_PATH, PRESET_PATH)
    except Exception:
        pass


def _text_of(value) -> str:
    if isinstance(value, list):
        return "\n".join(str(line) for line in value)
    return str(value or "")


def _parse(raw: dict) -> dict:
    """{이름: 본문} 으로 정규화한다. 형식이 어긋난 항목은 조용히 건너뛴다.

    프리셋 하나를 잘못 적었다고 노드가 죽거나 나머지 프리셋까지 사라지면
    안 된다 -- 손으로 고치는 파일이라 오타가 나기 쉽다.
    """
    out = {}
    entries = raw.get("presets") if isinstance(raw, dict) else None

    # 목록 형식: [{"name": ..., "prompt": ...}, ...]
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            text = _text_of(entry.get("prompt"))
            if name and text and name != PRESET_NONE:
                out[name] = text
    # 사전 형식도 받는다: {"이름": "본문"}
    elif isinstance(entries, dict):
        for name, value in entries.items():
            name = str(name).strip()
            text = _text_of(value)
            if name and text and name != PRESET_NONE:
                out[name] = text
    return out


def load_presets() -> dict:
    """{이름: 시스템 프롬프트}. 파일이 없거나 깨졌으면 빈 사전."""
    _ensure_file()
    try:
        mtime = os.path.getmtime(PRESET_PATH)
    except OSError:
        _CACHE.update(mtime=None, presets={})
        return {}

    if _CACHE["mtime"] == mtime:
        return dict(_CACHE["presets"])

    try:
        with open(PRESET_PATH, "r", encoding="utf-8") as fh:
            presets = _parse(json.load(fh))
    except Exception as exc:
        # JSON 이 깨져도 노드는 떠야 한다. 대신 이유는 남긴다 --
        # 조용히 빈 목록이 되면 "프리셋이 왜 안 보이지" 로만 보인다.
        print(f"[LLM Hub] could not read system_prompts.json: {exc!r}")
        presets = {}

    _CACHE.update(mtime=mtime, presets=presets)
    return dict(presets)


def preset_names() -> list:
    """드롭다운 항목. 항상 (none) 이 첫 번째다."""
    return [PRESET_NONE] + list(load_presets().keys())


def resolve(preset_name: str, system_prompt: str) -> tuple:
    """프리셋과 직접 입력한 시스템 프롬프트를 합친다.

    (합친 문장, 디버그 메모) 를 돌려준다.

    둘 다 있으면 프리셋이 앞, 입력이 뒤다. 프리셋을 '기본 성격', 입력을
    '이번 실행만의 덧붙임' 으로 쓸 수 있게 하려는 것이다. 프리셋이 입력을
    덮어쓰면 사용자가 방금 타이핑한 것이 조용히 사라져서 더 나쁘다.

    이름이 목록에 없으면(파일에서 지웠거나 이름을 바꿨다) 무시하고 이유를
    남긴다. 여기서 실패로 처리하면 워크플로우 전체가 안 돈다.
    """
    name = (preset_name or PRESET_NONE).strip()
    typed = (system_prompt or "").strip()

    if not name or name == PRESET_NONE:
        return typed, ""

    presets = load_presets()
    if name not in presets:
        return typed, (
            f"preset: '{name}' not found in system_prompts.json, ignored"
        )

    text = presets[name]
    if typed:
        return f"{text}\n\n{typed}", f"preset: '{name}' plus the system_prompt box, appended in that order"
    return text, f"preset: '{name}' applied"
