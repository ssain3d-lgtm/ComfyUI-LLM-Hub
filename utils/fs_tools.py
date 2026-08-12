# -*- coding: utf-8 -*-
"""LM Studio 툴 루프용 파일 접근 도구 + 경로 검증 (DESIGN §8.1, §11).

모든 경로는 realpath 기준으로 workspace_dir 하위인지 검증한다.
`../` 탈출과 workspace 밖을 가리키는 심볼릭 링크는 거부한다.
"""

from __future__ import annotations

import json
import os

# 툴이 한 번에 돌려주는 디렉터리 엔트리 상한 (응답 폭주 방지)
MAX_DIR_ENTRIES = 500

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": (
                "작업 루트 폴더 기준 상대경로의 파일/폴더 목록을 돌려준다. "
                "루트 자체는 '.' 또는 빈 문자열로 지정한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "작업 루트 기준 상대경로. 루트는 '.'",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "작업 루트 폴더 기준 상대경로의 텍스트 파일 내용을 돌려준다. "
                "바이너리 파일은 크기만 알려준다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "작업 루트 기준 상대경로 파일",
                    }
                },
                "required": ["path"],
            },
        },
    },
]


class PathDenied(Exception):
    """workspace_dir 밖을 가리키는 경로."""


def safe_resolve(workspace_dir: str, rel_path: str) -> str:
    """rel_path 를 workspace_dir 하위의 실제 경로로 해석한다 (DESIGN §11).

    workspace 밖으로 나가면 PathDenied 를 던진다.
    """
    root = os.path.realpath(workspace_dir)
    raw = (rel_path or "").strip()

    # 절대경로가 들어와도 realpath 검증에서 걸러지므로 그대로 합쳐서 판정한다.
    if os.path.isabs(raw):
        candidate = raw
    else:
        candidate = os.path.join(root, raw)

    resolved = os.path.realpath(candidate)

    if resolved == root:
        return resolved
    if not resolved.startswith(root + os.sep):
        raise PathDenied(f"access denied: '{rel_path}' 는 작업 루트 밖입니다")
    return resolved


def _looks_binary(chunk: bytes) -> bool:
    """바이너리 추정. 한글 등 멀티바이트 UTF-8 은 텍스트로 취급해야 한다."""
    if not chunk:
        return False
    if b"\x00" in chunk:
        return True

    # UTF-8 로 해석되면 텍스트다 (한글/이모지 등 비ASCII 포함).
    try:
        chunk.decode("utf-8")
        return False
    except UnicodeDecodeError as exc:
        # 청크 끝에서 멀티바이트 문자가 잘린 경우는 정상 텍스트일 수 있다.
        if exc.start >= len(chunk) - 4:
            return False

    # UTF-8 이 아니면 제어문자 비율로 판정한다.
    text_chars = bytes(range(0x20, 0x7F)) + b"\n\r\t\f\b"
    non_text = sum(1 for byte in chunk if byte not in text_chars)
    return non_text / len(chunk) > 0.30


def list_dir(workspace_dir: str, path: str) -> str:
    """폴더 목록(이름/타입/크기)을 JSON 문자열로 돌려준다."""
    try:
        target = safe_resolve(workspace_dir, path or ".")
    except PathDenied as exc:
        return str(exc)

    if not os.path.isdir(target):
        return f"error: 폴더가 아닙니다: {path}"

    entries = []
    try:
        names = sorted(os.listdir(target))
    except OSError as exc:
        return f"error: 폴더를 읽을 수 없습니다: {exc}"

    truncated = False
    if len(names) > MAX_DIR_ENTRIES:
        names = names[:MAX_DIR_ENTRIES]
        truncated = True

    for name in names:
        full = os.path.join(target, name)
        try:
            if os.path.isdir(full):
                entries.append({"name": name, "type": "dir"})
            else:
                entries.append(
                    {"name": name, "type": "file", "size": os.path.getsize(full)}
                )
        except OSError:
            entries.append({"name": name, "type": "unknown"})

    result = {"path": path or ".", "entries": entries}
    if truncated:
        result["note"] = f"{MAX_DIR_ENTRIES}개까지만 표시됨"
    return json.dumps(result, ensure_ascii=False)


def read_file(workspace_dir: str, path: str, max_bytes: int = 262144) -> str:
    """텍스트 파일 내용을 돌려준다. 상한 초과 시 앞부분 + '...truncated'."""
    try:
        target = safe_resolve(workspace_dir, path)
    except PathDenied as exc:
        return str(exc)

    if not os.path.isfile(target):
        return f"error: 파일이 없습니다: {path}"

    try:
        size = os.path.getsize(target)
        with open(target, "rb") as fh:
            raw = fh.read(max_bytes + 1)
    except OSError as exc:
        return f"error: 파일을 읽을 수 없습니다: {exc}"

    if _looks_binary(raw[:8192]):
        return f"binary file ({size} bytes)"

    over_limit = len(raw) > max_bytes
    raw = raw[:max_bytes]
    text = raw.decode("utf-8", errors="replace")
    if over_limit:
        text += f"\n...truncated ({size} bytes 중 앞 {max_bytes} bytes)"
    return text


def dispatch_tool(name: str, arguments: dict, workspace_dir: str, max_bytes: int) -> str:
    """LM Studio 툴 루프에서 함수 이름으로 도구를 실행한다."""
    path = ""
    if isinstance(arguments, dict):
        path = arguments.get("path") or arguments.get("directory") or arguments.get("file") or ""
    if name == "list_dir":
        return list_dir(workspace_dir, path)
    if name == "read_file":
        return read_file(workspace_dir, path, max_bytes)
    return f"error: 알 수 없는 도구입니다: {name}"
