# -*- coding: utf-8 -*-
"""thinking(추론) 을 본문과 분리해 모니터로 흘린다 (실측 기반).

실측 2026-08-12, LM Studio 스트리밍 한 번의 델타 구성:

    reasoning_content : 298회
    content           :   3회

델타의 99%가 thinking 인데 본문만 읽고 있었다. 그래서 모니터 창은 생성 시간
대부분을 빈 채로 앉아 있다가 마지막에 답만 툭 나온다.

절대 불변식: thinking 은 노드의 text 출력에 절대 들어가지 않는다.
그 출력은 다운스트림 프롬프트로 들어가므로 오염되면 그림이 망가진다.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_PACK_ROOT))
_PACK_NAME = os.path.basename(_PACK_ROOT)

base = importlib.import_module(f"{_PACK_NAME}.backends.base")
lmstudio_mod = importlib.import_module(f"{_PACK_NAME}.backends.lmstudio")
stream_mod = importlib.import_module(f"{_PACK_NAME}.utils.stream")
nodes_mod = importlib.import_module(f"{_PACK_NAME}.nodes")

THINKING = "Here's a thinking process: 17 x 23 ..."
ANSWER = "391"


class ThinkingServer:
    """reasoning_content 를 먼저 잔뜩 흘리고 content 를 조금 주는 서버."""

    def __enter__(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_GET(self):
                body = json.dumps({"data": [{"id": "m"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for piece in THINKING:
                    self._chunk({"reasoning_content": piece})
                for piece in ANSWER:
                    self._chunk({"content": piece})
                self.wfile.write(b"data: [DONE]\n\n")

            def _chunk(self, delta):
                payload = {"model": "m", "choices": [{"delta": delta}]}
                self.wfile.write(
                    ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8"))

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = "http://127.0.0.1:%d" % self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_a):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class Recorder:
    enabled = True

    def __init__(self):
        self.text = ""
        self.thinking = ""
        self.status = ""

    def append(self, delta):
        self.text += delta or ""

    def append_thinking(self, delta):
        self.thinking += delta or ""

    def set_status(self, status):
        self.status = status

    def reset_text(self, text=""):
        self.text = text

    def finish(self, status="", text=None):
        if text is not None:
            self.text = text


class TestThinkingChannel(unittest.TestCase):
    def _run(self):
        with ThinkingServer() as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url, "unload_after": False}}
            )
            req = base.LLMRequest("lmstudio", "m", "", "17*23")
            req.max_tokens = 2048
            req.emitter = Recorder()
            response = backend.generate(req)
            return response, req.emitter

    def test_thinking_reaches_the_monitor(self):
        _response, emitter = self._run()
        self.assertEqual(emitter.thinking, THINKING)

    def test_thinking_never_enters_the_body(self):
        _response, emitter = self._run()
        self.assertEqual(emitter.text, ANSWER)
        self.assertNotIn("thinking process", emitter.text)

    def test_thinking_never_enters_the_text_output(self):
        """가장 중요한 불변식. 이 출력은 다운스트림 프롬프트로 들어간다."""
        response, _emitter = self._run()
        self.assertEqual(response.text, ANSWER)
        self.assertNotIn("thinking process", response.text)
        self.assertEqual(response.status, "ok")

    def test_emitter_sends_thinking_on_its_own_channel(self):
        emitter = stream_mod.StreamEmitter(node_id="7", enabled=True)
        sent = []
        emitter.enabled = True
        emitter._server = mock.Mock()
        emitter._server.send_sync = lambda event, payload: sent.append(payload)
        emitter.append_thinking("생각")
        emitter.append("답")
        emitter.finish(status="ok")
        self.assertTrue(sent, "아무것도 전송되지 않았다")
        last = sent[-1]
        self.assertIn("thinking", last)
        self.assertEqual(last["thinking"], "생각")
        self.assertEqual(last["text"], "답")

    def test_frontend_switches_to_the_body_as_soon_as_it_arrives(self):
        """사고 과정이 답을 가리면 안 된다. JS 를 정적으로 고정한다."""
        path = os.path.join(_PACK_ROOT, "web", "js", "llmhub_monitor.js")
        with open(path, "r", encoding="utf-8") as fh:
            javascript = fh.read()
        handler = javascript.split("api.addEventListener(EVENT_NAME", 1)[1].split("});", 1)[0]
        self.assertIn("data.thinking", handler, "thinking 을 아예 안 읽는다")
        self.assertIn("renderThinking", handler)
        # 본문 분기가 thinking 분기보다 먼저 와야 한다.
        self.assertLess(handler.index("if (body)"), handler.index("renderThinking"),
                        "본문이 있어도 사고 과정을 먼저 그린다")

    def test_thinking_is_never_rendered_as_markdown(self):
        """마크다운으로 그리면 답처럼 보여서 어느 게 결과인지 헷갈린다."""
        path = os.path.join(_PACK_ROOT, "web", "js", "llmhub_monitor.js")
        with open(path, "r", encoding="utf-8") as fh:
            javascript = fh.read()
        body = javascript.split("renderThinking(text) {", 1)[1].split("\n    },", 1)[0]
        self.assertNotIn("renderMarkdown", body)
        self.assertIn("textContent", body)

    def test_old_emitters_without_the_method_do_not_crash(self):
        """append_thinking 이 없는 emitter 로도 백엔드가 죽으면 안 된다."""
        class Old:
            enabled = True
            def __init__(self): self.text = ""
            def append(self, d): self.text += d or ""
            def set_status(self, s): pass
            def reset_text(self, t=""): self.text = t
            def finish(self, status="", text=None): pass

        with ThinkingServer() as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url, "unload_after": False}}
            )
            req = base.LLMRequest("lmstudio", "m", "", "17*23")
            req.max_tokens = 2048
            req.emitter = Old()
            response = backend.generate(req)
        self.assertEqual(response.text, ANSWER)


if __name__ == "__main__":
    unittest.main()
