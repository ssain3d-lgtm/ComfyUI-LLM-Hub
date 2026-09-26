# -*- coding: utf-8 -*-
"""ninfer: 실행 버튼 → NInfer 자동 기동 → 생성 → (원하면) 서버 종료.

NInfer 에는 모델을 올리고 내리는 API 가 없다. 예전에는 NInfer 설정 UI 에서 시작/중지를 손으로
눌러야 했다. 이제 ninfer 백엔드가 설정 UI 의 /api/start · /api/stop 을 대신 부른다.

실제 서버 없이 돈다. 가짜 서버 하나가 설정 UI(/api/*)와 NInfer(/health, /v1/*)를 함께 흉내낸다.
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
backends = importlib.import_module(f"{_PACK_NAME}.backends")
ninfer_mod = importlib.import_module(f"{_PACK_NAME}.backends.ninfer")
nodes_mod = importlib.import_module(f"{_PACK_NAME}.nodes")
LLMRequest = base.LLMRequest

sys.path.insert(0, os.path.join(_PACK_ROOT, "tests"))
from test_batch_extra_body_seed import run_node  # noqa: E402

A = "qwen_a.ninfer"
B = "qwen_b.ninfer"
PUBLIC_ID = "qwen3.8-27b"


class FakeNInfer:
    """설정 UI + NInfer 서버. status 는 설정 UI 의 /api/state 가 돌려줄 값이다.

    start_polls: /api/start 뒤 몇 번의 /api/state 까지 "starting" 으로 답할지.
    preflight: /api/start 가 force 없이 돌려줄 사전 점검 결과(None = 통과).
    """

    def __init__(self, status="stopped", running="", preflight=None, start_polls=1,
                 fail_start=False, context=220000, understands_options=True):
        self.status = status
        self.running = running
        # 떠 있는 서버의 --max-context. understands_options=False 는 "options" 를 모르는 예전 설정 UI.
        self.context = context
        self.profile_context = 220000
        self.understands_options = understands_options
        self.preflight = preflight
        self.start_polls = start_polls
        self.fail_start = fail_start
        self.calls = []
        self._pending = 0

    def posts(self):
        return [(p, b) for m, p, b in self.calls if m == "POST"]

    def api_posts(self):
        return [(p, b) for p, b in self.posts() if p.startswith("/api/")]

    def _state(self):
        status = self.status
        if status == "starting":
            if self._pending > 0:
                self._pending -= 1
            else:
                status = self.status = "failed" if self.fail_start else "running"
        return {
            "status": status, "port": 8081,
            "run": ({"artifact": self.running, "args": ["--max-context", str(self.context)]}
                    if self.running else None),
            "progress": {"stage": "loading_weights", "load_percent": 50.0,
                         "failure_line": "CUDA out of memory" if status == "failed" else None},
            "log_tail": ["boom"],
        }

    def handle(self, method, path, body):
        self.calls.append((method, path, body))
        if path == "/api/state":
            return 200, self._state()
        if path == "/api/models":
            return 200, {"models": [{"name": A, "supported": True}, {"name": B, "supported": True}]}
        if path == "/api/start":
            if self.status in ("running", "starting"):
                return 409, {"error": "already_running"}
            if self.preflight and not (body or {}).get("force"):
                return 409, {"error": "preflight", "preflight": self.preflight}
            self.status, self.running, self._pending = "starting", body["artifact"], self.start_polls
            options = (body.get("options") or {}) if self.understands_options else {}
            self.context = options.get("max-context", self.profile_context)
            run = {"artifact": self.running}
            if options:
                run["overrides"] = options
            return 200, {"ok": True, "run": run}
        if path == "/api/stop":
            if self.status in ("stopped", "failed"):
                return 200, {"ok": True, "code": "already_stopped"}
            self.status, self.running = "stopped", ""
            return 200, {"ok": True, "code": "stopped"}
        up = self.status == "running"
        if path == "/health":
            return (200 if up else 503), {}
        if path == "/v1/models":
            return (200, {"data": [{"id": PUBLIC_ID}]}) if up else (503, {})
        if path == "/v1/chat/completions":
            if not up:
                return 503, {"error": {"message": "not ready"}}
            if body.get("model") != PUBLIC_ID:
                return 400, {"error": {"message": f"unknown model {body.get('model')}"}}
            return 200, {"model": PUBLIC_ID,
                         "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}
        return 404, {"error": "not_found"}

    def __enter__(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _do(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                code, payload = outer.handle(method, self.path, json.loads(raw) if raw else None)
                data = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._do("GET")

            def do_POST(self):
                self._do("POST")

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._patches = [
            mock.patch.object(ninfer_mod, "POLL_S", 0.01),
            mock.patch.object(ninfer_mod, "STOPPED_GRACE_S", 0.2),
            # 테스트끼리 "직전 모델" 이 새지 않게 한다.
            mock.patch.dict(ninfer_mod._LAST_ARTIFACT, {"name": ""}),
            # ComfyUI 밖에서는 VRAM 비우기가 늘 실패한다. 부른 횟수만 센다.
            mock.patch.object(ninfer_mod, "free_comfy_vram", side_effect=self._freed),
        ]
        self.freed = 0
        for patch in self._patches:
            patch.start()
        return self

    def _freed(self):
        self.freed += 1
        return True

    def __exit__(self, *_exc):
        for patch in self._patches:
            patch.stop()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False

    @property
    def url(self):
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def port(self):
        return self._server.server_address[1]

    def backend(self, **section):
        cfg = {"config_ui_url": self.url, "port": self.port}
        cfg.update(section)
        with mock.patch.dict(os.environ, {ninfer_mod.CONFIG_UI_ENV: ""}):
            impl = ninfer_mod.NInferBackend(config={"ninfer": cfg})
        impl.apply_base_url(self.url)
        return impl


def _req(model="", unload=False, context=0):
    return LLMRequest("ninfer", model, "", "hi", unload_after=unload, timeout_s=5, ninfer_context=context)


class TestAutoStart(unittest.TestCase):
    def test_factory_returns_the_managing_backend(self):
        self.assertIsInstance(backends.get_backend("ninfer"), ninfer_mod.NInferBackend)
        self.assertEqual(backends.get_backend("ninfer").base_url, "http://127.0.0.1:8081")

    def test_a_stopped_server_is_started_with_the_chosen_artifact(self):
        with FakeNInfer() as srv:
            resp = srv.backend().generate(_req(B))
        self.assertEqual(resp.status, "ok", resp.raw_debug)
        self.assertEqual(srv.api_posts(), [("/api/start", {"artifact": B})])
        # 요청에는 artifact 이름이 아니라 서버의 공개 ID 가 나가야 한다(NInfer 가 거절한다).
        chat = [b for p, b in srv.posts() if p == "/v1/chat/completions"]
        self.assertEqual(chat[0]["model"], PUBLIC_ID)
        self.assertIn(f"'{B}' is up", resp.raw_debug)

    def test_auto_with_nothing_running_starts_the_first_listed_model(self):
        with FakeNInfer() as srv:
            resp = srv.backend().generate(_req(""))
        self.assertEqual(resp.status, "ok", resp.raw_debug)
        self.assertEqual(srv.api_posts(), [("/api/start", {"artifact": A})])
        self.assertIn("no model chosen", resp.raw_debug)

    def test_default_artifact_from_config_wins_over_the_list(self):
        with FakeNInfer() as srv:
            srv.backend(default_artifact=B).generate(_req(""))
        self.assertEqual(srv.api_posts(), [("/api/start", {"artifact": B})])

    def test_the_previous_model_comes_back_after_an_unload(self):
        """직전 실행이 서버를 내렸어도, (auto) 로 다시 누르면 같은 모델이 떠야 한다."""
        with FakeNInfer() as srv:
            srv.backend().generate(_req(B, unload=True))
            srv.calls.clear()
            srv.backend().generate(_req(""))
        self.assertEqual(srv.api_posts(), [("/api/start", {"artifact": B})])

    def test_a_running_server_with_the_same_model_is_left_alone(self):
        with FakeNInfer(status="running", running=A) as srv:
            resp = srv.backend().generate(_req(A))
        self.assertEqual(resp.status, "ok")
        self.assertEqual(srv.api_posts(), [])

    def test_a_public_id_uses_whatever_is_running(self):
        with FakeNInfer(status="running", running=A) as srv:
            resp = srv.backend().generate(_req(PUBLIC_ID))
        self.assertEqual(resp.status, "ok")
        self.assertEqual(srv.api_posts(), [])

    def test_a_different_model_is_swapped_in(self):
        with FakeNInfer(status="running", running=A) as srv:
            resp = srv.backend().generate(_req(B))
        self.assertEqual(resp.status, "ok", resp.raw_debug)
        self.assertEqual(srv.api_posts(), [("/api/stop", {}), ("/api/start", {"artifact": B})])
        self.assertIn("to switch to", resp.raw_debug)

    def test_a_failed_start_is_reported_with_the_log_line(self):
        with FakeNInfer(fail_start=True) as srv:
            resp = srv.backend().generate(_req(A))
        self.assertTrue(resp.status.startswith("error: ninfer"), resp.status)
        self.assertIn("CUDA out of memory", resp.status)
        self.assertNotIn("/v1/chat/completions", [p for p, _ in srv.posts()])

    def test_a_start_that_never_finishes_times_out(self):
        with FakeNInfer(start_polls=10 ** 6) as srv:
            resp = srv.backend(start_timeout_s=1).generate(_req(A))
        self.assertIn("was not ready after 1s", resp.status)


class TestPreflight(unittest.TestCase):
    BLOCKED = {"level": "blocked", "free_bytes": 4 * 1024 ** 3, "needed_bytes": 24 * 1024 ** 3}
    WARN = {"level": "warn", "free_bytes": 25 * 1024 ** 3, "needed_bytes": 24 * 1024 ** 3}

    def test_comfy_models_are_unloaded_then_retried(self):
        srv = FakeNInfer(preflight=self.BLOCKED)

        def freed():
            srv.preflight = None  # ComfyUI 모델을 내렸더니 자리가 났다
            srv.freed += 1
            return True

        with srv:
            with mock.patch.object(ninfer_mod, "free_comfy_vram", side_effect=freed):
                resp = srv.backend().generate(_req(A))
        self.assertEqual(resp.status, "ok", resp.raw_debug)
        self.assertEqual(srv.freed, 1)
        self.assertIn("unloaded ComfyUI's cached models", resp.raw_debug)

    def test_still_blocked_is_an_error_not_a_forced_start(self):
        with FakeNInfer(preflight=self.BLOCKED) as srv:
            resp = srv.backend().generate(_req(A))
        self.assertIn("not enough free VRAM", resp.status)
        self.assertIn("free 4.0 GiB, needs 24.0 GiB", resp.status)
        self.assertFalse(any((b or {}).get("force") for _p, b in srv.api_posts()))

    def test_tight_but_fitting_is_forced_and_noted(self):
        with FakeNInfer(preflight=self.WARN) as srv:
            resp = srv.backend().generate(_req(A))
        self.assertEqual(resp.status, "ok", resp.raw_debug)
        self.assertEqual(srv.api_posts()[-1], ("/api/start", {"artifact": A, "force": True}))
        self.assertIn("VRAM is tight", resp.raw_debug)


class TestStopAfterGeneration(unittest.TestCase):
    def test_unload_stops_the_server(self):
        with FakeNInfer() as srv:
            resp = srv.backend().generate(_req(A, unload=True))
        self.assertEqual(resp.status, "ok")
        self.assertEqual(srv.api_posts(), [("/api/start", {"artifact": A}), ("/api/stop", {})])
        self.assertEqual(srv.status, "stopped")
        self.assertIn("NInfer server stopped", resp.raw_debug)

    def test_without_unload_the_server_stays_up(self):
        with FakeNInfer() as srv:
            srv.backend().generate(_req(A, unload=False))
        self.assertEqual(srv.status, "running")
        self.assertNotIn(("/api/stop", {}), srv.api_posts())

    def test_a_server_the_ui_did_not_start_is_not_stopped(self):
        with FakeNInfer(status="external") as srv:
            srv.status = "external"
            note = srv.backend().unload_model(PUBLIC_ID)
        self.assertIn("not started by the config UI", note)
        self.assertEqual(srv.api_posts(), [])


class TestUnmanaged(unittest.TestCase):
    def test_another_port_is_not_managed(self):
        """노드에 다른 주소를 적었으면 설정 UI 가 관리하는 서버가 아니다. 관리 요청을 보내지 않는다."""
        with FakeNInfer(status="running", running=A) as srv:
            impl = srv.backend(port=1)
            self.assertFalse(impl.managed())
            impl.generate(_req(PUBLIC_ID, unload=True))
        self.assertEqual(srv.api_posts(), [])

    def test_auto_start_off_is_not_managed(self):
        with FakeNInfer() as srv:
            self.assertFalse(srv.backend(auto_start=False).managed())

    def test_no_config_ui_and_no_server_says_what_to_start(self):
        impl = ninfer_mod.NInferBackend(config={"ninfer": {"config_ui_url": "http://127.0.0.1:1"}})
        with mock.patch.object(impl, "_health", return_value=False):
            resp = impl.generate(_req(A))
        self.assertIn("config UI", resp.status)
        self.assertIn("config_ui_script", resp.status)

    def test_the_suite_never_reaches_the_real_config_ui(self):
        """tests/__init__.py 가 설정 UI 주소를 죽은 포트로 돌려 둔다."""
        self.assertEqual(backends.get_backend("ninfer").config_ui_url, "http://127.0.0.1:1")


class TestContext(unittest.TestCase):
    """ninfer_context: 그 컨텍스트로 띄운다. 이번 기동에만 쓰고 프로필은 건드리지 않는다."""

    def test_a_start_carries_the_context(self):
        with FakeNInfer() as srv:
            resp = srv.backend().generate(_req(A, context=30000))
        self.assertEqual(resp.status, "ok", resp.raw_debug)
        self.assertEqual(srv.api_posts(), [("/api/start", {
            "artifact": A, "options": {"max-context": 30000, "kv-capacity": 30000}})])
        self.assertIn("context 30000 for this start", resp.raw_debug)

    def test_a_different_running_context_is_restarted(self):
        with FakeNInfer(status="running", running=A, context=220000) as srv:
            resp = srv.backend().generate(_req(A, context=30000))
        self.assertEqual(resp.status, "ok", resp.raw_debug)
        self.assertEqual([p for p, _ in srv.api_posts()], ["/api/stop", "/api/start"])
        self.assertIn("to change the context 220000 -> 30000", resp.raw_debug)
        self.assertEqual(srv.context, 30000)

    def test_auto_model_keeps_the_running_model_when_only_the_context_changes(self):
        with FakeNInfer(status="running", running=B, context=220000) as srv:
            srv.backend().generate(_req("", context=30000))
        self.assertEqual(srv.api_posts()[-1][1]["artifact"], B)

    def test_the_same_context_is_left_alone(self):
        with FakeNInfer(status="running", running=A, context=30000) as srv:
            srv.backend().generate(_req(A, context=30000))
        self.assertEqual(srv.api_posts(), [])

    def test_zero_uses_whatever_is_running(self):
        with FakeNInfer(status="running", running=A, context=30000) as srv:
            srv.backend().generate(_req(A, context=0))
        self.assertEqual(srv.api_posts(), [])

    def test_zero_starts_with_the_profile(self):
        with FakeNInfer() as srv:
            srv.backend().generate(_req(A, context=0))
        self.assertEqual(srv.api_posts(), [("/api/start", {"artifact": A})])

    def test_an_old_config_ui_that_ignores_options_is_reported(self):
        with FakeNInfer(understands_options=False) as srv:
            resp = srv.backend().generate(_req(A, context=30000))
        self.assertEqual(resp.status, "ok")
        self.assertIn("does not take a per-start context", resp.raw_debug)

    def test_an_unmanaged_address_says_the_context_was_ignored(self):
        with FakeNInfer(status="running", running=A) as srv:
            resp = srv.backend(port=1).generate(_req(PUBLIC_ID, context=30000))
        self.assertEqual(srv.api_posts(), [])
        self.assertIn("ninfer_context is ignored", resp.raw_debug)


class TestFreeComfyBeforeStart(unittest.TestCase):
    def test_comfy_models_are_unloaded_before_a_start(self):
        with FakeNInfer() as srv:
            resp = srv.backend().generate(_req(A))
        self.assertEqual(srv.freed, 1)
        self.assertIn("before starting", resp.raw_debug)

    def test_not_when_nothing_is_started(self):
        with FakeNInfer(status="running", running=A) as srv:
            srv.backend().generate(_req(A))
        self.assertEqual(srv.freed, 0)

    def test_it_can_be_turned_off(self):
        with FakeNInfer() as srv:
            srv.backend(free_comfy_vram=False).generate(_req(A))
        self.assertEqual(srv.freed, 0)


class TestReasoning(unittest.TestCase):
    """reasoning: 요청마다 thinking 을 정한다. 실측(2026-09-26)으로 확인한 필드만 보낸다."""

    def _chat(self, reasoning="", extra_body=None):
        with FakeNInfer(status="running", running=A) as srv:
            req = _req(A)
            req.reasoning = reasoning
            req.extra_body = extra_body or {}
            resp = srv.backend().generate(req)
        body = [b for p, b in srv.posts() if p == "/v1/chat/completions"][0]
        return body, resp

    def test_off_sends_reasoning_effort_none(self):
        body, resp = self._chat("off")
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertIn("reasoning=off", resp.raw_debug)

    def test_on_uses_the_template_kwarg(self):
        body, _ = self._chat("on")
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": True})
        self.assertNotIn("reasoning_effort", body)

    def test_a_level_is_passed_through(self):
        body, _ = self._chat("high")
        self.assertEqual(body["reasoning_effort"], "high")

    def test_default_sends_nothing(self):
        body, _ = self._chat("")
        self.assertNotIn("reasoning_effort", body)
        self.assertNotIn("chat_template_kwargs", body)

    def test_extra_body_wins(self):
        """둘이 어긋나면 NInfer 가 conflicting_template_option 400 을 낸다. 사용자가 적은 쪽을 따른다."""
        body, resp = self._chat("off", {"chat_template_kwargs": {"enable_thinking": True}})
        self.assertNotIn("reasoning_effort", body)
        self.assertIn("extra_body already sets it", resp.raw_debug)

    def test_on_keeps_other_template_kwargs_from_extra_body(self):
        body, _ = self._chat("on", {"chat_template_kwargs": {"preserve_thinking": False}})
        self.assertEqual(body["chat_template_kwargs"], {"preserve_thinking": False, "enable_thinking": True})


class TestNodeInputs(unittest.TestCase):
    """노드의 ninfer_context / reasoning 은 ninfer 에만 넘어간다."""

    def _run(self, backend, **kwargs):
        seen = []

        class Spy:
            def generate(self, req):
                seen.append(req)
                return base.LLMResponse(text="x", status="ok")

        run_node(Spy(), backend=backend, **kwargs)
        return seen[0]

    def test_ninfer_gets_both(self):
        req = self._run("ninfer", ninfer_context=30000, reasoning="off")
        self.assertEqual((req.ninfer_context, req.reasoning), (30000, "off"))

    def test_the_default_choice_means_nothing_sent(self):
        req = self._run("ninfer", reasoning=nodes_mod.REASONING_DEFAULT)
        self.assertEqual(req.reasoning, "")

    def test_other_backends_get_neither(self):
        req = self._run("llamacpp", ninfer_context=30000, reasoning="off")
        self.assertEqual((req.ninfer_context, req.reasoning), (0, ""))

    def test_widgets_are_appended_at_the_end(self):
        self.assertEqual(nodes_mod.WIDGET_ORDER[-2:], ["ninfer_context", "reasoning"])


if __name__ == "__main__":
    unittest.main()
