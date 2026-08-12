# -*- coding: utf-8 -*-
"""생성 중단 (노드의 Stop 버튼).

중단은 두 갈래로 일어난다. 어느 쪽이든 **받은 데까지는 돌려준다** —
timeout 이 이미 그렇게 동작하므로 같은 규칙을 따른다. 다만 상태는 ok 가 아니다.

  lmstudio : SSE 루프에서 플래그를 보고 빠져나온다
  CLI 3종  : 프로세스 트리를 죽인다 (.cmd 셔틀 때문에 kill 로는 부족하다)

ComfyUI 자체 Cancel 도 같은 판정을 거치게 해서 버튼 두 개가 따로 놀지 않게 한다.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_PACK_ROOT))
_PACK_NAME = os.path.basename(_PACK_ROOT)

base = importlib.import_module(f"{_PACK_NAME}.backends.base")
cancel = importlib.import_module(f"{_PACK_NAME}.utils.cancel")
proc_mod = importlib.import_module(f"{_PACK_NAME}.utils.proc")
lmstudio_mod = importlib.import_module(f"{_PACK_NAME}.backends.lmstudio")


class TestRegistry(unittest.TestCase):
    def setUp(self):
        cancel.begin("7")
        self.addCleanup(cancel.begin, "7")

    def test_not_stopped_by_default(self):
        self.assertFalse(cancel.is_stopped("7"))

    def test_request_stop_marks_only_that_node(self):
        cancel.begin("8")
        cancel.request_stop("7")
        self.assertTrue(cancel.is_stopped("7"))
        self.assertFalse(cancel.is_stopped("8"))

    def test_begin_clears_a_previous_stop(self):
        """다음 실행이 지난 번 중지를 물려받으면 안 된다."""
        cancel.request_stop("7")
        cancel.begin("7")
        self.assertFalse(cancel.is_stopped("7"))

    def test_comfyui_interrupt_also_counts(self):
        """ComfyUI 의 Cancel 을 눌러도 멈춰야 한다. 버튼이 둘로 갈리면 안 된다."""
        with mock.patch.object(cancel, "_comfy_interrupted", return_value=True):
            self.assertTrue(cancel.is_stopped("7"))

    def test_unknown_node_is_never_stopped(self):
        self.assertFalse(cancel.is_stopped("does-not-exist"))

    def test_stopping_kills_the_registered_process(self):
        killed = []
        fake = mock.Mock()
        with mock.patch.object(cancel, "_kill", killed.append):
            cancel.register_process("7", fake)
            cancel.request_stop("7")
        self.assertEqual(killed, [fake])


class TestRunCliStreamStops(unittest.TestCase):
    def test_stops_a_long_running_process_quickly(self):
        """timeout 300초를 기다리지 않고 즉시 끊겨야 한다."""
        script = "import sys,time\nfor i in range(600):\n    print(i, flush=True)\n    time.sleep(0.05)\n"
        seen = []
        stop = threading.Event()

        def on_line(line):
            seen.append(line)
            if len(seen) >= 3:
                stop.set()

        started = time.time()
        code, stdout, stderr, duration = proc_mod.run_cli_stream(
            [sys.executable, "-c", script], cwd=os.getcwd(),
            timeout_s=300, on_line=on_line, should_stop=stop.is_set,
        )
        elapsed = time.time() - started
        self.assertLess(elapsed, 30, f"중지가 안 먹었다 ({elapsed:.1f}s)")
        self.assertGreaterEqual(len(seen), 3)
        self.assertIn("0", stdout)      # 받은 데까지는 남는다

    def test_runs_to_completion_when_not_stopped(self):
        code, stdout, _stderr, _d = proc_mod.run_cli_stream(
            [sys.executable, "-c", "print('done')"], cwd=os.getcwd(),
            timeout_s=60, should_stop=lambda: False,
        )
        self.assertEqual(code, 0)
        self.assertIn("done", stdout)


class SlowStreamServer:
    """토큰을 천천히 흘리는 서버. 중단 없이는 오래 걸린다."""

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
                try:
                    for i in range(400):
                        chunk = {"model": "m", "choices": [{"delta": {"content": "x"}}]}
                        self.wfile.write(
                            ("data: " + json.dumps(chunk) + "\n\n").encode("utf-8"))
                        self.wfile.flush()
                        time.sleep(0.02)
                except Exception:
                    pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = "http://127.0.0.1:%d" % self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_a):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class Emitter:
    enabled = True

    def __init__(self, stop_after=5):
        self.text = ""
        self.stop_after = stop_after
        self.node_id = "7"

    def append(self, delta):
        self.text += delta or ""
        if len(self.text) >= self.stop_after:
            cancel.request_stop("7")

    def append_thinking(self, delta):
        pass

    def set_status(self, status):
        pass

    def reset_text(self, text=""):
        self.text = text

    def finish(self, status="", text=None):
        pass


class TestLmStudioStops(unittest.TestCase):
    def setUp(self):
        cancel.begin("7")
        self.addCleanup(cancel.begin, "7")

    def test_stream_stops_and_returns_what_arrived(self):
        with SlowStreamServer() as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url, "unload_after": False}}
            )
            req = base.LLMRequest("lmstudio", "m", "", "안녕")
            req.max_tokens = 2048
            req.emitter = Emitter(stop_after=5)
            req.node_id = "7"
            started = time.time()
            response = backend.generate(req)
            elapsed = time.time() - started

        self.assertLess(elapsed, 20, f"중지가 안 먹었다 ({elapsed:.1f}s)")
        self.assertTrue(response.text, "받은 데까지는 돌려줘야 한다")
        self.assertNotEqual(response.status, "ok", "중지를 성공으로 위장하면 안 된다")
        self.assertIn("중지", response.status)


class TestNonStreamingLimit(unittest.TestCase):
    """스트리밍이 꺼져 있으면(stream_view=off) SSE 루프 검사를 못 거친다.

    이미 날아간 요청 하나는 끝까지 기다려야 한다 — requests 의 비스트리밍 응답은
    도중에 끊을 방법이 없다. 그래도 **반복 경계**에서는 멈춰야 한다.
    """

    def setUp(self):
        cancel.begin("9")
        self.addCleanup(cancel.begin, "9")

    def test_stops_at_the_iteration_boundary(self):
        with SlowStreamServer() as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url, "unload_after": False}}
            )
            req = base.LLMRequest("lmstudio", "m", "", "안녕")
            req.max_tokens = 2048
            req.emitter = None          # 스트리밍 꺼짐
            cancel.request_stop("9")
            with mock.patch.object(cancel, "is_stopped", lambda _n: True):
                response = backend.generate(req)
        self.assertIn("중지", response.status)


class TestRouteAndButton(unittest.TestCase):
    """ComfyUI 없이 확인할 수 있는 배선만 본다."""

    def test_route_registration_is_a_noop_outside_comfyui(self):
        """ComfyUI 밖에서 임포트해도 터지면 안 된다(테스트가 그 증거다)."""
        routes = importlib.import_module(f"{_PACK_NAME}.server_routes")
        self.assertEqual(routes.ROUTE, "/llmhub/stop")
        self.assertIn(routes.register(), (True, False))

    def _javascript(self):
        path = os.path.join(_PACK_ROOT, "web", "js", "llmhub_monitor.js")
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()

    def test_button_posts_to_the_same_route_the_server_registers(self):
        routes = importlib.import_module(f"{_PACK_NAME}.server_routes")
        self.assertIn(routes.ROUTE, self._javascript())

    def test_button_sends_the_node_id(self):
        """어느 노드를 멈출지 없으면 서버가 할 수 있는 게 없다."""
        javascript = self._javascript()
        handler = javascript.split('.llmhub-stop"', 1)[1].split("\n  const control", 1)[0]
        self.assertIn("node: String(node.id)", handler)

    def test_button_stops_click_propagation(self):
        """안 막으면 캔버스가 클릭을 노드 드래그로 삼킨다."""
        javascript = self._javascript()
        self.assertIn("stopPropagation", javascript)

    def test_button_is_hidden_when_nothing_is_running(self):
        javascript = self._javascript()
        self.assertIn("control.setRunning(false)", javascript)
        self.assertIn("this.setRunning(!done)", javascript)


if __name__ == "__main__":
    unittest.main()
