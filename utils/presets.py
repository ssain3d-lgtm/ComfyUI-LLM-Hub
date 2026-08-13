# -*- coding: utf-8 -*-
"""시스템 프롬프트 프리셋 저장소.

노드의 편집창에서 지금 쓴 시스템 프롬프트를 이름을 붙여 저장하고, 나중에
그대로 불러온다. 저장은 `system_prompts.json` 에 한다.

파일은 노드가 쓰기도 하고 사람이 손으로 고치기도 한다. 그래서 `config.json`
과 같은 규칙을 따른다.
  - git 에 올라가지 않는다(.gitignore)
  - 없으면 `system_prompts.example.json` 을 복사해 만든다
  - 그래서 `git pull` 이 사용자의 프리셋을 덮어쓰지 않는다

본문은 문자열이거나 문자열 목록이다(목록이면 줄바꿈으로 잇는다). 저장할 때는
여러 줄이면 목록으로 쓴다 -- JSON 한 줄에 \\n 을 잔뜩 박아두면 나중에 손으로
열어봤을 때 읽을 수가 없다.
"""

from __future__ import annotations

import json
import os
import shutil

# 드롭다운 첫 항목. 프리셋을 쓰지 않겠다는 뜻이다.
PRESET_NONE = "(none)"

# 이름 길이 상한. UI 에서 잘리지 않을 정도이면 충분하다.
MAX_NAME_LEN = 60

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESET_PATH = os.path.join(_ROOT, "system_prompts.json")
EXAMPLE_PATH = os.path.join(_ROOT, "system_prompts.example.json")

# 파일이 바뀌면 다시 읽는다. INPUT_TYPES 가 자주 불리므로 매번 디스크를 읽지
# 않되, 사용자가 파일을 직접 고쳐도 반영되게 하려는 것이다.
_CACHE = {"mtime": None, "presets": {}}


class PresetError(Exception):
    """저장/삭제 요청이 잘못됐을 때. 메시지는 그대로 사용자에게 보인다."""


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
    안 된다 -- 사람이 손으로도 고치는 파일이라 오타가 나기 쉽다.
    """
    out = {}
    entries = raw.get("presets") if isinstance(raw, dict) else None

    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            text = _text_of(entry.get("prompt"))
            if name and text and name != PRESET_NONE:
                out[name] = text
    elif isinstance(entries, dict):  # {"이름": "본문"} 형태도 받는다
        for name, value in entries.items():
            name = str(name).strip()
            text = _text_of(value)
            if name and text and name != PRESET_NONE:
                out[name] = text
    return out


def _read_raw() -> dict:
    """파일 전체를 읽는다. 저장할 때 우리가 모르는 키를 보존하려고 쓴다."""
    try:
        with open(PRESET_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write(raw: dict, presets: dict) -> None:
    """프리셋을 파일에 쓴다. `_comment` 같은 다른 키는 그대로 둔다.

    본문이 여러 줄이면 목록으로 쓴다. 한 줄에 \\n 을 박아두면 나중에 파일을
    직접 열었을 때 읽을 수가 없다.
    """
    out = {k: v for k, v in raw.items() if k != "presets"}
    entries = []
    for name, text in presets.items():
        lines = text.split("\n")
        entries.append({"name": name, "prompt": lines if len(lines) > 1 else text})
    out["presets"] = entries

    # 임시 파일에 먼저 쓰고 바꿔치기한다. 쓰다가 죽으면 원본이 반쯤 덮여
    # 프리셋을 통째로 잃는다.
    tmp = PRESET_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, PRESET_PATH)
    _CACHE.update(mtime=None, presets={})


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


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise PresetError("A preset name is required.")
    if name == PRESET_NONE:
        raise PresetError(f"'{PRESET_NONE}' is reserved and cannot be used as a name.")
    if len(name) > MAX_NAME_LEN:
        raise PresetError(f"The name is too long (max {MAX_NAME_LEN} characters).")
    return name


def save_preset(name: str, prompt: str) -> dict:
    """프리셋을 저장한다(같은 이름이면 덮어쓴다). 저장 후 전체 목록을 돌려준다."""
    name = _clean_name(name)
    text = (prompt or "").strip()
    if not text:
        # 빈 프리셋은 불러와도 아무 일이 안 일어난다. 저장된 줄 알고 나중에
        # 불러왔다가 프롬프트가 비는 것보다 지금 거절하는 편이 낫다.
        raise PresetError("The system prompt is empty, so there is nothing to save.")

    _ensure_file()
    raw = _read_raw()
    presets = _parse(raw)
    presets[name] = text
    _write(raw, presets)
    return dict(presets)


def delete_preset(name: str) -> dict:
    """프리셋을 지운다. 없는 이름이면 알려준다(조용히 성공시키지 않는다)."""
    name = (name or "").strip()
    _ensure_file()
    raw = _read_raw()
    presets = _parse(raw)
    if name not in presets:
        raise PresetError(f"'{name}' is not in the preset list.")
    del presets[name]
    _write(raw, presets)
    return dict(presets)
