# -*- coding: utf-8 -*-
"""WEB_DIRECTORY 깊이와 JS 의 상대 import 깊이가 맞는지 (실측 회귀).

ComfyUI 는 WEB_DIRECTORY 를 /extensions/<팩이름>/ 아래에 통째로 붙인다.
그래서 파일의 서빙 경로는 WEB_DIRECTORY 기준 상대경로가 그대로 붙는다:

    WEB_DIRECTORY="./web",    파일 web/js/x.js  ->  /extensions/<팩>/js/x.js
    WEB_DIRECTORY="./web/js", 파일 web/js/x.js  ->  /extensions/<팩>/x.js

전자는 한 단계 더 깊어서 관용구인 `../../scripts/app.js` 가
/extensions/scripts/app.js 로 풀린다 — 404 다. 실측(2026-08-12):

    GET /extensions/scripts/app.js  -> 404
    GET /scripts/app.js             -> 200

이러면 모듈 로드가 통째로 실패해서 registerExtension 이 아예 안 불린다.
증상은 "노드는 뜨는데 JS 기능만 전부 없음" 이고, 파이썬 로그에는 한 줄도 안 남는다.
"""

from __future__ import annotations

import os
import re
import unittest
from posixpath import normpath

_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PACK_NAME = os.path.basename(_PACK_ROOT)


def _web_directory():
    with open(os.path.join(_PACK_ROOT, "__init__.py"), "r", encoding="utf-8") as fh:
        match = re.search(r'WEB_DIRECTORY\s*=\s*"([^"]+)"', fh.read())
    assert match, "__init__.py 에 WEB_DIRECTORY 가 없다"
    return match.group(1)


def _served_js():
    """[(서빙 URL, 파일 경로)] — ComfyUI 의 /extensions 글롭과 같은 규칙."""
    root = os.path.abspath(os.path.join(_PACK_ROOT, _web_directory()))
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".js"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            out.append((f"/extensions/{_PACK_NAME}/{rel}", full))
    return out


class TestWebDirectoryDepth(unittest.TestCase):
    def test_there_is_js_to_serve(self):
        self.assertTrue(_served_js(), "WEB_DIRECTORY 아래에 .js 가 하나도 없다")

    def test_relative_imports_resolve_to_real_files(self):
        for url, path in _served_js():
            with open(path, "r", encoding="utf-8") as fh:
                source = fh.read()
            base = url.rsplit("/", 1)[0]
            for spec in re.findall(r'from\s+"(\.\.?/[^"]+)"', source):
                resolved = normpath(f"{base}/{spec}")
                self.assertFalse(
                    resolved.startswith("/extensions/"),
                    f"{url}\n  import '{spec}' -> {resolved}\n"
                    f"  ComfyUI 코어는 /scripts/... 로 서빙된다. /extensions/ 아래로 풀리면 "
                    f"404 라 모듈이 통째로 죽는다. WEB_DIRECTORY 깊이를 맞춰라.")
                self.assertTrue(
                    resolved.startswith("/scripts/") or resolved.startswith("/"),
                    f"{url}: import '{spec}' 가 루트 밖({resolved})으로 나간다")

    def test_core_app_import_lands_on_scripts_app_js(self):
        """가장 흔한 관용구를 콕 집어 고정한다."""
        for url, path in _served_js():
            with open(path, "r", encoding="utf-8") as fh:
                source = fh.read()
            if "scripts/app.js" not in source:
                continue
            spec = re.search(r'from\s+"(\.\.[^"]*scripts/app\.js)"', source).group(1)
            resolved = normpath(f'{url.rsplit("/", 1)[0]}/{spec}')
            self.assertEqual(resolved, "/scripts/app.js", f"{url}: {spec} -> {resolved}")


if __name__ == "__main__":
    unittest.main()
