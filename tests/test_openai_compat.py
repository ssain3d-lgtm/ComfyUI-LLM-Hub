# -*- coding: utf-8 -*-
"""OpenAI 호환 백엔드 (Ollama / vLLM / llama.cpp).

실기기가 없으므로 가짜 서버로 "무엇을 보내는가" 를 검증한다. 서버마다 다른
부분(SSE 청크 모양, 오류 응답 형식)은 여기서 확인할 수 없다 — README 와
모듈 주석에 그렇게 적어뒀다.
"""

from __future__ import annotations

import importlib
import os
import sys
import unittest
from unittest import mock

_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_PACK_ROOT))
_PACK_NAME = os.path.basename(_PACK_ROOT)

base = importlib.import_module(f"{_PACK_NAME}.backends.base")
backends = importlib.import_module(f"{_PACK_NAME}.backends")
oc_mod = importlib.import_module(f"{_PACK_NAME}.backends.openai_compat")
nodes_mod = importlib.import_module(f"{_PACK_NAME}.nodes")
LLMRequest = base.LLMRequest

sys.path.insert(0, os.path.join(_PACK_ROOT, "tests"))
from mock_lmstudio import MockLMStudio  # noqa: E402

FIXTURES = os.path.join(_PACK_ROOT, "tests", "fixtures")


def _backend(server_url, **section):
    cfg = {"openai_compat": dict({"base_url": server_url}, **section)}
    return oc_mod.OpenAICompatBackend(config=cfg)


class TestRegistration(unittest.TestCase):
    def test_in_backend_list(self):
        self.assertIn("openai_compat", backends.BACKEND_NAMES)

    def test_factory_returns_it(self):
        self.assertIsInstance(
            backends.get_backend("openai_compat"), oc_mod.OpenAICompatBackend
        )

    def test_existing_backends_keep_their_order(self):
        # 저장된 워크플로우는 콤보를 인덱스로 들고 있다. 앞 넷의 순서가 바뀌면
        # 예전 워크플로우가 다른 백엔드로 열린다.
        self.assertEqual(
            backends.BACKEND_NAMES[:4], ["lmstudio", "claude", "codex", "gemini"]
        )

    def test_default_is_ollama_port(self):
        self.assertIn("11434", oc_mod.DEFAULT_BASE_URL)


class TestPayload(unittest.TestCase):
    def test_ttl_is_not_sent(self):
        """ttl 은 LM Studio 전용이다. 엄격한 서버는 모르는 필드에 400 을 낸다."""
        with MockLMStudio(script=[{"content": "안녕"}]) as server:
            b = _backend(server.base_url)
            b.generate(LLMRequest("openai_compat", "", "", "안녕", ttl_sec=300))
        self.assertNotIn("ttl", server.requests[0])

    def test_standard_fields_are_sent(self):
        with MockLMStudio(script=[{"content": "안녕"}]) as server:
            b = _backend(server.base_url)
            b.generate(LLMRequest(
                "openai_compat", "qwen3", "시스템", "안녕",
                temperature=0.3, max_tokens=512,
            ))
        payload = server.requests[0]
        self.assertEqual(payload["model"], "qwen3")
        self.assertEqual(payload["temperature"], 0.3)
        self.assertEqual(payload["max_tokens"], 512)
        self.assertEqual(payload["messages"][0]["role"], "system")

    def test_generation_works(self):
        with MockLMStudio(script=[{"content": "안녕하세요"}]) as server:
            resp = _backend(server.base_url).generate(
                LLMRequest("openai_compat", "", "", "인사해")
            )
        self.assertEqual(resp.status, "ok")
        self.assertEqual(resp.text, "안녕하세요")

    def test_file_access_tool_loop_still_works(self):
        """LM Studio 와 같은 툴 루프를 그대로 물려받는다."""
        script = [
            {"tool_calls": [{"name": "read_file", "path": "test.txt"}]},
            {"content": "고양이는 3마리"},
        ]
        with MockLMStudio(script=script) as server:
            resp = _backend(server.base_url).generate(LLMRequest(
                "openai_compat", "", "", "요약해",
                workspace_dir=FIXTURES, file_access=True,
            ))
        self.assertEqual(resp.status, "ok")
        self.assertTrue(any("LLMHUB-7391" in r for r in server.tool_results()))


class TestBaseUrl(unittest.TestCase):
    def test_node_override_wins(self):
        b = _backend("http://127.0.0.1:9999")
        b.apply_base_url("http://127.0.0.1:8000")
        self.assertEqual(b.base_url, "http://127.0.0.1:8000")

    def test_empty_override_keeps_config(self):
        b = _backend("http://127.0.0.1:9999")
        b.apply_base_url("")
        self.assertEqual(b.base_url, "http://127.0.0.1:9999")

    def test_trailing_slash_stripped(self):
        b = _backend("http://127.0.0.1:9999")
        b.apply_base_url("http://127.0.0.1:8000/")
        self.assertEqual(b.base_url, "http://127.0.0.1:8000")

    def test_connect_error_names_the_address(self):
        b = _backend("http://127.0.0.1:1")
        resp = b.generate(LLMRequest("openai_compat", "", "", "안녕", timeout_s=5))
        self.assertIn("127.0.0.1:1", resp.status)
        self.assertIn("11434", resp.status)  # 흔한 포트 안내


class TestNoLMStudioExtras(unittest.TestCase):
    def test_unload_explains_instead_of_running_lms(self):
        """lms unload 는 LM Studio CLI 다. 여기서 실행하면 안 된다."""
        calls = []
        proc_mod = importlib.import_module(f"{_PACK_NAME}.utils.proc")
        with MockLMStudio(script=[{"content": "안녕"}]) as server:
            b = _backend(server.base_url)
            with mock.patch.object(
                proc_mod, "run_cli",
                side_effect=lambda a, **k: calls.append(a) or (0, "", "", 0.1),
            ):
                resp = b.generate(
                    LLMRequest("openai_compat", "", "", "안녕", unload_after=True)
                )
        self.assertEqual(calls, [], "lms 를 실행하려 했다")
        self.assertIn("ollama stop", resp.raw_debug)

    def test_lmstudio_token_is_not_reused(self):
        """LM Studio 토큰을 남의 서버로 보내면 안 된다."""
        with mock.patch.dict(os.environ, {"LM_STUDIO_API_KEY": "lmstudio-secret"}):
            b = oc_mod.OpenAICompatBackend(
                config={"openai_compat": {"base_url": "http://x"},
                        "lmstudio": {"api_token": "lmstudio-secret"}}
            )
        self.assertEqual(b.api_token, "")
        self.assertNotIn("Authorization", b._headers())

    def test_own_token_is_used(self):
        b = _backend("http://x", api_token="my-token")
        self.assertEqual(b._headers().get("Authorization"), "Bearer my-token")

    def test_lmstudio_backend_still_sends_ttl(self):
        """회귀 방지: 새 백엔드를 넣으면서 LM Studio 를 망가뜨리지 않았는지."""
        ls_mod = importlib.import_module(f"{_PACK_NAME}.backends.lmstudio")
        with MockLMStudio(script=[{"content": "안녕"}]) as server:
            ls = ls_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url, "ttl_sec": 300}}
            )
            ls.generate(LLMRequest("lmstudio", "", "", "안녕"))
        self.assertEqual(server.requests[0]["ttl"], 300)


class TestNodeWiring(unittest.TestCase):
    def test_widget_exists_and_is_last(self):
        spec = nodes_mod.LLMHubGenerate.INPUT_TYPES()
        self.assertIn("openai_base_url", spec["optional"])
        # 새 위젯은 맨 뒤여야 예전 워크플로우의 widgets_values 가 밀리지 않는다
        names = list(spec["optional"])
        self.assertGreater(names.index("openai_base_url"), names.index("extra_args"))

    def test_node_passes_base_url(self):
        captured = {}

        class Spy:
            base_url = "http://config-value"

            def apply_base_url(self, url):
                captured["applied"] = url

            def generate(self, req):
                captured["req"] = req.base_url_override
                return base.LLMResponse(text="x", status="ok")

        with mock.patch.object(nodes_mod, "get_backend", return_value=Spy()):
            nodes_mod.LLMHubGenerate().generate(
                backend="openai_compat", prompt="hi", system_prompt="", model="",
                file_access=False, workspace_dir="", temperature=0.7, max_tokens=64,
                timeout_sec=10, stream_view="off", seed=0,
                openai_base_url="http://127.0.0.1:8000",
            )
        self.assertEqual(captured["applied"], "http://127.0.0.1:8000")
        self.assertEqual(captured["req"], "http://127.0.0.1:8000")

    def test_js_shows_widget_only_for_this_backend(self):
        path = os.path.join(_PACK_ROOT, "web", "js", "llmhub_monitor.js")
        with open(path, encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn('openai_base_url: ["openai_compat"]', js)
        # 이 백엔드도 OpenAI API 라 temperature/max_tokens 를 쓴다
        self.assertIn('temperature: ["lmstudio", "openai_compat"]', js)


if __name__ == "__main__":
    unittest.main(verbosity=2)
