# -*- coding: utf-8 -*-
"""server_routes.py 의 핸들러를 실제로 실행한다.

이 파일은 115줄인데 테스트는 상수 두 개만 확인하고 있었다. register() 가
ComfyUI 밖에서 False 로 빠지기 때문에 그 아래 핸들러 본문은 **전부 미실행**
이었다 -- Stop 버튼의 서버 쪽 전부와, 사용자의 system_prompts.json 을 고치는
유일한 쓰기 경로가 그랬다.

aiohttp 도 ComfyUI 도 없이 돌린다. 가짜 PromptServer 에 라우트를 등록시켜
코루틴을 붙잡은 뒤, 가짜 request 로 직접 부른다.
"""

from __future__ import annotations

import asyncio
import importlib
import io
import json
import os
import sys
import types
import unittest

_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_PACK_ROOT))
_PACK_NAME = os.path.basename(_PACK_ROOT)

routes_mod = importlib.import_module(f"{_PACK_NAME}.server_routes")
cancel_mod = importlib.import_module(f"{_PACK_NAME}.utils.cancel")
presets_mod = importlib.import_module(f"{_PACK_NAME}.utils.presets")

sys.path.insert(0, os.path.join(_PACK_ROOT, "tests"))


class FakeResponse:
    """web.json_response / web.Response 를 대신한다."""

    def __init__(self, body=None, status=200, text=None, content_type=None):
        self.body = body
        self.status = status
        self.text = text
        self.content_type = content_type


class FakeRequest:
    """핸들러가 쓰는 것만 흉내낸다: await request.json() 과 request.query."""

    def __init__(self, payload=None, query=None, raise_on_json=False):
        self._payload = payload
        self._raise = raise_on_json
        self.query = query or {}

    async def json(self):
        if self._raise:
            raise ValueError("not json")
        return self._payload


def _register_and_collect():
    """가짜 PromptServer 로 register() 를 돌려 핸들러를 모은다.

    반환: {(메서드, 경로): 코루틴 함수}
    """
    collected = {}

    class Routes:
        def post(self, path):
            def deco(fn):
                collected[("POST", path)] = fn
                return fn
            return deco

        def get(self, path):
            def deco(fn):
                collected[("GET", path)] = fn
                return fn
            return deco

    server = types.ModuleType("server")
    server.PromptServer = type("PromptServer", (), {})
    server.PromptServer.instance = type("Instance", (), {"routes": Routes()})()

    aiohttp = types.ModuleType("aiohttp")
    web = types.SimpleNamespace(
        json_response=lambda body, status=200: FakeResponse(body=body, status=status),
        Response=lambda text=None, content_type=None, charset=None: FakeResponse(
            text=text, content_type=content_type
        ),
    )
    aiohttp.web = web

    saved = {name: sys.modules.get(name) for name in ("server", "aiohttp", "aiohttp.web")}
    sys.modules["server"] = server
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web
    try:
        ok = routes_mod.register()
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    assert ok, "register() 가 False 를 돌려줬다"
    return collected


# 프리셋 파일을 임시 경로로 돌리는 헬퍼는 test_presets 에 이미 있고 검증돼 있다.
# 여기서 다시 쓰면 캐시 구조 같은 세부를 두 곳에서 관리하게 된다(실제로 한 번
# 틀렸다). 그대로 가져다 쓴다.
from test_presets import _TempPresets  # noqa: E402

_SEED = {"presets": [{"name": "seed", "prompt": "hi"}]}


class TestRegistration(unittest.TestCase):
    def test_it_is_a_no_op_outside_comfyui(self):
        """ComfyUI 밖에서는 조용히 아무것도 안 해야 한다."""
        self.assertFalse(routes_mod.register())

    def test_all_four_routes_are_registered(self):
        collected = _register_and_collect()
        self.assertIn(("POST", routes_mod.ROUTE), collected)
        self.assertIn(("GET", routes_mod.HEALTH_ROUTE), collected)
        self.assertIn(("GET", routes_mod.PRESET_ROUTE), collected)
        self.assertIn(("POST", routes_mod.PRESET_ROUTE), collected)
        self.assertIn(("POST", routes_mod.PRESET_DELETE_ROUTE), collected)


class TestStopRoute(unittest.TestCase):
    """Stop 버튼의 서버 쪽. 여기까지가 노드별 취소의 전부다."""

    def setUp(self):
        self.handler = _register_and_collect()[("POST", routes_mod.ROUTE)]

    def test_it_sets_the_cancel_flag(self):
        cancel_mod.begin("node-7")
        self.assertFalse(cancel_mod.is_stopped("node-7"))
        response = asyncio.run(self.handler(FakeRequest({"node": "node-7"})))
        self.assertEqual(response.status, 200)
        self.assertTrue(response.body["ok"])
        self.assertTrue(cancel_mod.is_stopped("node-7"), "깃발이 안 섰다")

    def test_a_missing_node_is_a_400(self):
        response = asyncio.run(self.handler(FakeRequest({})))
        self.assertEqual(response.status, 400)
        self.assertIn("missing 'node'", response.body["error"])

    def test_broken_json_does_not_crash_the_route(self):
        """본문이 깨져 있어도 500 이 아니라 400 이어야 한다."""
        response = asyncio.run(self.handler(FakeRequest(raise_on_json=True)))
        self.assertEqual(response.status, 400)

    def test_it_only_stops_the_node_you_named(self):
        cancel_mod.begin("a")
        cancel_mod.begin("b")
        asyncio.run(self.handler(FakeRequest({"node": "a"})))
        self.assertTrue(cancel_mod.is_stopped("a"))
        self.assertFalse(cancel_mod.is_stopped("b"), "다른 노드까지 멈췄다")


class TestHealthRoute(unittest.TestCase):
    """라우트가 형식을 제대로 고르는지만 본다.

    진단 내용 자체는 test_health.py 가 본다. 여기서 진짜 collect() 를 부르면
    LM Studio 포트를 두드리게 되고, 그건 이 스위트가 하지 말아야 할 일이다.
    """

    # as_text() 가 쓰는 키를 전부 갖춰야 한다. 모양은 utils.health.collect() 를
    # 따른다 -- 손으로 베끼면 또 어긋나므로 아래 테스트가 실제 collect() 의
    # 키 집합과 대조한다.
    FAKE_REPORT = {
        "ok": True,
        "failed": [],
        "version": "0.0.0-test",
        "python": "3.x",
        "platform": "test",
        "pack_dir": "/tmp/pack",
        "checks": [{"name": "frontend JS", "ok": True, "optional": False,
                    "detail": "found"}],
    }

    def test_the_fake_report_has_the_same_shape_as_the_real_one(self):
        """가짜가 진짜와 어긋나면 이 클래스는 아무것도 안 지키게 된다."""
        # 조회를 열지 않는다. 차단된 상태에서도 collect() 는 같은 키를 돌려주고,
        # 여기서 보려는 건 값이 아니라 모양이다. (이 테스트가 처음엔 allow_probe
        # 로 감싸져 있어서, 스위트에 남은 마지막 실서버 요청 2건이 여기였다.)
        real = self._collect()
        self.assertEqual(sorted(self.FAKE_REPORT), sorted(real))
        self.assertEqual(
            sorted(self.FAKE_REPORT["checks"][0]), sorted(real["checks"][0])
        )

    def setUp(self):
        self.handler = _register_and_collect()[("GET", routes_mod.HEALTH_ROUTE)]
        self.health = importlib.import_module(f"{_PACK_NAME}.utils.health")
        self._collect = self.health.collect
        self.health.collect = lambda: dict(self.FAKE_REPORT)
        self.addCleanup(setattr, self.health, "collect", self._collect)

    def test_plain_text_by_default(self):
        """브라우저로 여는 사람은 눈으로 읽으려는 것이지 JSON 을 파싱하려는 게 아니다."""
        response = asyncio.run(self.handler(FakeRequest(query={})))
        self.assertEqual(response.content_type, "text/plain")
        self.assertTrue(response.text)

    def test_json_when_asked(self):
        response = asyncio.run(self.handler(FakeRequest(query={"json": "1"})))
        self.assertEqual(response.body, self.FAKE_REPORT)

    def test_a_broken_diagnostic_still_answers(self):
        """진단이 죽으면 진단할 방법이 없어진다."""
        self.health.collect = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        response = asyncio.run(self.handler(FakeRequest(query={})))
        self.assertEqual(response.status, 500)
        self.assertIn("diagnostics failed", response.body["error"])


class TestPresetRoutes(unittest.TestCase):
    """사용자의 system_prompts.json 을 고치는 유일한 쓰기 경로."""

    def setUp(self):
        self.handlers = _register_and_collect()

    def test_list_returns_the_presets(self):
        with _TempPresets(_SEED):
            handler = self.handlers[("GET", routes_mod.PRESET_ROUTE)]
            response = asyncio.run(handler(FakeRequest()))
        self.assertTrue(response.body["ok"])
        self.assertIn("seed", response.body["presets"])

    def test_save_round_trips(self):
        with _TempPresets(_SEED):
            save = self.handlers[("POST", routes_mod.PRESET_ROUTE)]
            response = asyncio.run(save(FakeRequest({"name": "mine", "prompt": "본문"})))
            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["presets"]["mine"], "본문")
            # 응답이 항상 전체 목록을 준다 -- 프론트엔드가 다시 조회하지 않아도 된다.
            listed = asyncio.run(
                self.handlers[("GET", routes_mod.PRESET_ROUTE)](FakeRequest())
            )
            self.assertIn("mine", listed.body["presets"])

    def test_a_bad_name_is_a_400_with_a_readable_reason(self):
        with _TempPresets(_SEED):
            save = self.handlers[("POST", routes_mod.PRESET_ROUTE)]
            response = asyncio.run(save(FakeRequest({"name": "", "prompt": "x"})))
        self.assertEqual(response.status, 400)
        self.assertTrue(response.body["error"], "이유가 비어 있으면 편집창이 할 말이 없다")

    def test_delete_removes_it(self):
        with _TempPresets(_SEED):
            save = self.handlers[("POST", routes_mod.PRESET_ROUTE)]
            asyncio.run(save(FakeRequest({"name": "gone", "prompt": "x"})))
            delete = self.handlers[("POST", routes_mod.PRESET_DELETE_ROUTE)]
            response = asyncio.run(delete(FakeRequest({"name": "gone"})))
        self.assertEqual(response.status, 200)
        self.assertNotIn("gone", response.body["presets"])

    def test_deleting_something_that_is_not_there_is_a_400(self):
        with _TempPresets(_SEED):
            delete = self.handlers[("POST", routes_mod.PRESET_DELETE_ROUTE)]
            response = asyncio.run(delete(FakeRequest({"name": "없는것"})))
        self.assertEqual(response.status, 400)

    def test_broken_json_is_a_400_not_a_500(self):
        with _TempPresets(_SEED):
            save = self.handlers[("POST", routes_mod.PRESET_ROUTE)]
            response = asyncio.run(save(FakeRequest(raise_on_json=True)))
        self.assertEqual(response.status, 400)


if __name__ == "__main__":
    unittest.main()
