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


class TestAliases(unittest.TestCase):
    """ollama / vllm / llamacpp 는 openai_compat 과 같은 구현의 별칭이다.

    별칭을 만든 이유는 발견성이다. llama.cpp 를 쓰려면 "openai_compat 이 그거다"
    를 먼저 알아야 했는데, 드롭다운 어디에도 llama.cpp 라는 글자가 없었다.
    """

    def test_all_aliases_are_in_the_dropdown(self):
        for name in backends.OPENAI_COMPAT_ALIASES:
            self.assertIn(name, backends.BACKEND_NAMES, name)

    def test_existing_names_keep_their_place(self):
        """별칭은 맨 뒤에만 붙인다. 중간에 끼우면 저장된 워크플로우가 다른
        백엔드로 열릴 수 있다."""
        self.assertEqual(
            backends.BACKEND_NAMES[:5],
            ["lmstudio", "claude", "codex", "gemini", "openai_compat"],
        )

    def test_each_alias_is_the_same_backend(self):
        for name in backends.OPENAI_COMPAT_ALIASES:
            self.assertIsInstance(
                backends.get_backend(name), oc_mod.OpenAICompatBackend, name
            )

    def test_each_alias_lands_on_its_standard_port(self):
        expected = {
            "ollama": "http://127.0.0.1:11434",
            "vllm": "http://127.0.0.1:8000",
            "llamacpp": "http://127.0.0.1:8080",
        }
        # 목록이 늘면 여기도 늘려야 한다. 조용히 빠지지 않게 개수를 먼저 본다.
        self.assertEqual(set(expected), set(backends.OPENAI_COMPAT_ALIASES))
        for name, url in expected.items():
            self.assertEqual(backends.get_backend(name).base_url, url, name)

    def test_alias_says_its_own_name(self):
        """debug 문구가 'openai_compat: ...' 이면 어느 서버 얘긴지 알 수 없다."""
        for name in backends.OPENAI_COMPAT_ALIASES:
            self.assertEqual(backends.get_backend(name).name, name)
        self.assertEqual(backends.get_backend("openai_compat").name, "openai_compat")

    def test_the_node_field_still_wins(self):
        """표준 포트가 아닌 곳에 띄웠으면 노드에서 고칠 수 있어야 한다."""
        impl = backends.get_backend("llamacpp")
        impl.apply_base_url("http://192.168.0.9:9999")
        self.assertEqual(impl.base_url, "http://192.168.0.9:9999")

    def test_an_empty_field_does_not_wipe_the_standard_port(self):
        impl = backends.get_backend("llamacpp")
        impl.apply_base_url("")
        self.assertEqual(impl.base_url, "http://127.0.0.1:8080")

    def test_alias_beats_the_generic_config_value(self):
        """드롭다운에서 llamacpp 를 고른 것 자체가 어느 서버인지 명시한 것이다.

        범용 openai_compat.base_url 이 그걸 덮으면, 고른 것과 다른 서버로 나간다.
        """
        cfg = {"openai_compat": {"base_url": "http://127.0.0.1:11434"}}
        impl = oc_mod.OpenAICompatBackend(
            config=cfg, base_url_default="http://127.0.0.1:8080"
        )
        self.assertEqual(impl.base_url, "http://127.0.0.1:8080")

    def test_plain_openai_compat_still_follows_config(self):
        """별칭을 넣었다고 기존 동작이 바뀌면 안 된다."""
        cfg = {"openai_compat": {"base_url": "http://10.0.0.5:1234"}}
        self.assertEqual(oc_mod.OpenAICompatBackend(config=cfg).base_url,
                         "http://10.0.0.5:1234")

    def test_aliases_do_not_send_the_lmstudio_ttl(self):
        """ttl 은 LM Studio 전용 필드다. 엄격한 서버는 400 을 낸다."""
        for name in backends.OPENAI_COMPAT_ALIASES:
            impl = backends.get_backend(name)
            payload = impl._build_payload(
                base.LLMRequest(name, "", "", "hi", ttl_sec=300), [], "m"
            )
            self.assertNotIn("ttl", payload, name)


    def test_the_node_runs_end_to_end_on_an_alias(self):
        """생성자만 맞고 노드 배선이 틀리면 소용없다. 실제로 한 번 돌려본다."""
        nodes_mod = importlib.import_module(f"{_PACK_NAME}.nodes")
        with MockLMStudio(script=[{"content": "hello"}]) as server:
            out = nodes_mod.LLMHubGenerate().generate(
                backend="llamacpp", prompt="hi", system_prompt="", model="m",
                file_access=False, workspace_dir="", temperature=0.7, max_tokens=64,
                timeout_sec=10, seed=0, stream_view="off",
                openai_base_url=server.base_url,
            )
        self.assertEqual(out["result"][1], "ok", out["result"][2][:300])
        self.assertEqual(out["result"][0], "hello")
        self.assertNotIn("ttl", server.requests[0])


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

    def _backend_only(self):
        """JS 의 BACKEND_ONLY 리터럴을 {위젯: [백엔드...]} 로 읽는다.

        예전에는 완성된 문자열을 assertIn 으로 비교했는데, 별칭
        (ollama/vllm/llamacpp)이 늘자 문구가 달라져 깨졌다. 목록 자체를 읽어
        비교하면 별칭이 더 늘어도 이 테스트는 그대로 산다.
        """
        import re

        path = os.path.join(_PACK_ROOT, "web", "js", "llmhub_monitor.js")
        with open(path, encoding="utf-8") as fh:
            body = fh.read().split("const BACKEND_ONLY = {", 1)[1].split("\n};", 1)[0]
        return {
            name: re.findall(r'"([^"]+)"', names)
            for name, names in re.findall(r"(\w+)\s*:\s*\[([^\]]*)\]", body)
        }

    def _family(self):
        return {"openai_compat", *backends.OPENAI_COMPAT_ALIASES}

    def test_js_shows_the_address_box_for_the_whole_family(self):
        """별칭도 주소 칸이 필요하다. 표준 포트가 아닌 곳에 띄웠으면 고쳐야 한다."""
        shown = set(self._backend_only().get("openai_base_url", []))
        self.assertEqual(shown, self._family())

    def test_js_shows_sampling_widgets_for_the_whole_family(self):
        """전부 OpenAI API 라 temperature/max_tokens/extra_body 를 쓴다."""
        mapping = self._backend_only()
        for widget in ("temperature", "max_tokens", "extra_body"):
            shown = set(mapping.get(widget, []))
            self.assertTrue(
                self._family() <= shown,
                f"{widget}: {sorted(self._family() - shown)} 에서 안 보인다",
            )

    def test_lmstudio_only_widgets_stay_lmstudio_only(self):
        """ttl / unload / 모델 드롭다운은 LM Studio 전용 기능이다.

        별칭에 잘못 열어주면 아무 효과 없는 위젯이 보이고, ttl 은 엄격한 서버가
        400 을 내는 필드다.
        """
        mapping = self._backend_only()
        for widget in ("lmstudio_ttl_sec", "lmstudio_unload_after", "lmstudio_model"):
            self.assertEqual(mapping.get(widget), ["lmstudio"], widget)


if __name__ == "__main__":
    unittest.main(verbosity=2)
