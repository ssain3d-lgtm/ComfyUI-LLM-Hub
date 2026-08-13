# -*- coding: utf-8 -*-
"""추론 모델이 max_tokens 를 다 써버렸을 때의 진단 (실측 회귀).

실측(2026-08-12, qwen3.5-9b, LM Studio):
    max_tokens=256   finish_reason=length  reasoning_tokens=254  content=''
    max_tokens=2048  finish_reason=stop    reasoning_tokens=406  content='...'

즉 모델은 출력을 냈고 전부 숨겨진 reasoning 에 들어갔다. 그런데 노드는
"빈 응답 (모델이 텍스트를 내지 않음)" 이라고만 말해서 사용자가 갈 곳이 없다.
정확히 무슨 일이 벌어졌고 무엇을 올리면 되는지 말해야 한다.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_PACK_ROOT))
_PACK_NAME = os.path.basename(_PACK_ROOT)

base = importlib.import_module(f"{_PACK_NAME}.backends.base")
lmstudio_mod = importlib.import_module(f"{_PACK_NAME}.backends.lmstudio")
nodes_mod = importlib.import_module(f"{_PACK_NAME}.nodes")


class ReasoningServer:
    """예산을 reasoning 으로 다 태우고 content 는 빈 문자열로 주는 서버."""

    def __init__(self, reasoning_tokens=254, completion_tokens=256,
                 finish_reason="length", content=""):
        self.reasoning_tokens = reasoning_tokens
        self.completion_tokens = completion_tokens
        self.finish_reason = finish_reason
        self.content = content

    def __enter__(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                body = json.dumps({
                    "model": "reasoning-model",
                    "choices": [{
                        "finish_reason": outer.finish_reason,
                        "message": {"role": "assistant", "content": outer.content,
                                    "reasoning_content": "…생각…", "tool_calls": []},
                    }],
                    "usage": {
                        "completion_tokens": outer.completion_tokens,
                        "completion_tokens_details": {
                            "reasoning_tokens": outer.reasoning_tokens},
                    },
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = "http://127.0.0.1:%d" % self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_a):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _run(server, max_tokens=256):
    backend = lmstudio_mod.LMStudioBackend(
        config={"lmstudio": {"base_url": server.base_url, "unload_after": False}}
    )
    req = base.LLMRequest("lmstudio", "m", "", "안녕")
    req.max_tokens = max_tokens
    return backend.generate(req)


class TestMaxTokensRange(unittest.TestCase):
    """상한은 모델이 실제로 감당하는 값에 맞춰야 한다.

    실측 2026-08-12: LM Studio 에 올라온 채팅 모델 6종 전부 max_context_length=262144.
    옛 상한 32768 은 그 1/8 이라, 추론 토큰까지 먹는 이 노드에서는 특히 답답했다.
    """

    def setUp(self):
        self.spec = nodes_mod.LLMHubGenerate.INPUT_TYPES()["required"]["max_tokens"][1]

    def test_upper_bound_matches_the_measured_context(self):
        self.assertEqual(self.spec["max"], 262144)

    def test_default_is_generous_enough_for_reasoning_models(self):
        """실측: 추론에만 254~406 토큰을 쓴다. 기본값이 그보다 한참 커야 한다."""
        self.assertGreaterEqual(self.spec["default"], 1024)
        self.assertLessEqual(self.spec["default"], self.spec["max"])

    def test_tooltip_warns_about_reasoning_tokens(self):
        """이 위젯을 작게 잡으면 빈 응답이 난다는 걸 여기서 알려야 한다."""
        self.assertIn("Reasoning", self.spec["tooltip"])


class TestReasoningBudget(unittest.TestCase):
    def test_budget_exhausted_by_reasoning_is_named_precisely(self):
        with ReasoningServer() as server:
            response = _run(server, max_tokens=256)
        self.assertTrue(response.status.startswith("error:"), response.status)
        self.assertIn("max_tokens", response.status,
                      f"무엇을 올려야 하는지 말하지 않는다: {response.status}")
        self.assertIn("reasoning", response.status,
                      f"추론 토큰이 원인이라는 말이 없다: {response.status}")

    def test_the_numbers_are_reported(self):
        """254/256 같은 실제 숫자가 없으면 사용자가 얼마나 올릴지 모른다."""
        with ReasoningServer(reasoning_tokens=254, completion_tokens=256) as server:
            response = _run(server, max_tokens=256)
        self.assertIn("254", response.status + response.raw_debug)
        self.assertIn("256", response.status + response.raw_debug)

    def test_plain_empty_response_keeps_its_own_message(self):
        """추론과 무관하게 빈 응답이면 기존 메시지를 그대로 써야 한다."""
        with ReasoningServer(reasoning_tokens=0, completion_tokens=0,
                             finish_reason="stop") as server:
            response = _run(server, max_tokens=2048)
        self.assertIn("empty response", response.status)
        self.assertNotIn("추론", response.status)

    def test_normal_answer_is_untouched(self):
        with ReasoningServer(reasoning_tokens=406, completion_tokens=427,
                             finish_reason="stop", content="고양이") as server:
            response = _run(server, max_tokens=2048)
        self.assertEqual(response.status, "ok")
        self.assertEqual(response.text, "고양이")


if __name__ == "__main__":
    unittest.main()
