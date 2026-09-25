# -*- coding: utf-8 -*-
"""모델 올리기 → 생성 → 즉시 내리기 (load / generate / unload) 검증.

막은 구멍:
  - OpenAI 호환 서버(Ollama / llama.cpp / vLLM)에는 언로드가 아예 없었다.
    ComfyUI 에서 프롬프트를 만든 LLM 이 VRAM 을 쥐고 있으면 다음 이미지 모델이 밀려난다.
  - LM Studio 언로드가 lms CLI 에만 기댔다. lms 가 PATH 에 없으면 안 되고,
    base_url 이 다른 PC 면 "이 PC 의" LM Studio 를 건드렸다.
  - 지정한 모델이 내려가 있으면 /v1/models 의 아무 모델로 갈아타 ok 를 냈다.
  - one_per_image 를 Stop 으로 끊으면 "마지막 장에서 내린다" 가 오지 않아 모델이 남았다.

실제 서버 없이 돈다. 관리 요청은 전부 이 파일의 가짜 서버로만 나간다.
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
lmstudio_mod = importlib.import_module(f"{_PACK_NAME}.backends.lmstudio")
proc_mod = importlib.import_module(f"{_PACK_NAME}.utils.proc")
cancel = importlib.import_module(f"{_PACK_NAME}.utils.cancel")
nodes_mod = importlib.import_module(f"{_PACK_NAME}.nodes")
LLMRequest = base.LLMRequest

sys.path.insert(0, os.path.join(_PACK_ROOT, "tests"))
from test_batch_extra_body_seed import run_node  # noqa: E402

CHAT = "/v1/chat/completions"


class FakeServer:
    """경로별 응답을 정해두는 가짜 서버. 받은 요청을 (메서드, 경로, 본문) 으로 기록한다.

    routes: {(메서드, 경로): (코드, 본문)} 또는 {(메서드, 경로): callable(server) -> (코드, 본문)}
    채팅 요청은 따로 정하지 않으면 요청한 model 을 그대로 실어 "OK" 로 답한다.
    """

    def __init__(self, routes=None, loaded=None):
        self.routes = dict(routes or {})
        # None 이면 모델 적재 상태를 흉내내지 않는다(아무 모델이나 답한다).
        self.loaded = loaded
        self.calls = []

    def paths(self, method=None):
        return [p for m, p, _ in self.calls if method is None or m == method]

    def body_of(self, path):
        for _m, p, body in self.calls:
            if p == path:
                return body
        return None

    def __enter__(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _send(self, code, payload):
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _handle(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                outer.calls.append((method, self.path, body))
                route = outer.routes.get((method, self.path))
                if route is not None:
                    code, payload = route(outer, body) if callable(route) else route
                    self._send(code, payload)
                    return
                if method == "POST" and self.path == CHAT:
                    model = (body or {}).get("model") or ""
                    if outer.loaded is not None and model not in outer.loaded:
                        self._send(400, {"error": {"message": f"model '{model}' is not loaded"}})
                        return
                    self._send(200, {
                        "model": model or "served-model",
                        "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                    })
                    return
                if method == "GET" and self.path == "/v1/models":
                    self._send(200, {"data": [{"id": "some-other-model"}]})
                    return
                self._send(404, {"error": "not found"})

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False

    @property
    def base_url(self):
        host, port = self._server.server_address
        return f"http://{host}:{port}"


def _server_backend(name, url):
    impl = backends.get_backend(name)
    impl.apply_base_url(url)
    return impl


def _lmstudio(url, **cfg):
    section = {"base_url": url, "ttl_sec": 0, "unload_after": False}
    section.update(cfg)
    return lmstudio_mod.LMStudioBackend(config={"lmstudio": section})


def _req(backend, model="m", unload=True):
    return LLMRequest(backend, model, "", "hi", unload_after=unload, timeout_s=5)


class NoLms:
    """lms 가 불리면 기록한다. 테스트가 사용자의 진짜 LM Studio 를 건드리면 안 된다."""

    def __init__(self):
        self.calls = []

    def __enter__(self):
        self._patches = [
            mock.patch.object(proc_mod, "resolve_cli", return_value="/usr/bin/lms"),
            mock.patch.object(
                proc_mod, "run_cli",
                side_effect=lambda args, **k: self.calls.append(args) or (0, "", "", 0.1),
            ),
        ]
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *_exc):
        for patch in self._patches:
            patch.stop()
        return False


class TestOllamaUnload(unittest.TestCase):
    def test_generate_then_keep_alive_zero(self):
        with FakeServer({("POST", "/api/generate"): (200, {"done_reason": "unload"})}) as srv:
            resp = _server_backend("ollama", srv.base_url).generate(_req("ollama", "llama3.2"))
        self.assertEqual(resp.status, "ok")
        self.assertEqual(srv.paths("POST"), [CHAT, "/api/generate"], "생성 뒤에 내려야 한다")
        self.assertEqual(srv.body_of("/api/generate"), {"model": "llama3.2", "keep_alive": 0})
        self.assertIn("unloaded from VRAM", resp.raw_debug)

    def test_off_sends_nothing_extra(self):
        with FakeServer() as srv:
            _server_backend("ollama", srv.base_url).generate(_req("ollama", unload=False))
        self.assertEqual(srv.paths("POST"), [CHAT])

    def test_config_default_is_off(self):
        """설정한 적 없는 서버에 관리 요청이 나가면 안 된다. LM Studio 의 기본값(True)을
        물려받지 않는다."""
        with FakeServer() as srv:
            _server_backend("ollama", srv.base_url).generate(_req("ollama", unload=None))
        self.assertEqual(srv.paths("POST"), [CHAT])

    def test_a_refusal_is_reported_not_hidden(self):
        with FakeServer({("POST", "/api/generate"): (404, {"error": "model 'x' not found"})}) as srv:
            resp = _server_backend("ollama", srv.base_url).generate(_req("ollama", "x"))
        self.assertEqual(resp.status, "ok", "언로드 실패가 생성 결과를 망치면 안 된다")
        self.assertIn("did not unload", resp.raw_debug)
        self.assertIn("not found", resp.raw_debug)


class TestLlamaCppUnload(unittest.TestCase):
    def test_router_unload(self):
        with FakeServer({("POST", "/models/unload"): (200, {"success": True})}) as srv:
            resp = _server_backend("llamacpp", srv.base_url).generate(_req("llamacpp", "qwen"))
        self.assertEqual(srv.body_of("/models/unload"), {"model": "qwen"})
        self.assertIn("unloaded from VRAM", resp.raw_debug)

    def test_single_model_server_says_why(self):
        with FakeServer() as srv:
            resp = _server_backend("llamacpp", srv.base_url).generate(_req("llamacpp"))
        self.assertIn("router mode", resp.raw_debug)
        self.assertNotIn("unloaded from VRAM", resp.raw_debug)

    def test_an_unloaded_model_is_loaded_again_not_swapped(self):
        """라우터의 자동 적재를 꺼둔 경우: 지정한 모델을 올리고 같은 모델로 다시 묻는다."""
        def load(server, body):
            server.loaded.add(body["model"])
            return 200, {"success": True}

        with FakeServer({("POST", "/models/load"): load}, loaded=set()) as srv:
            resp = _server_backend("llamacpp", srv.base_url).generate(
                _req("llamacpp", "qwen", unload=False))
        self.assertEqual(resp.status, "ok", resp.status)
        chats = [b["model"] for m, p, b in srv.calls if p == CHAT]
        self.assertEqual(chats, ["qwen", "qwen"], "다른 모델로 갈아타면 안 된다")
        self.assertIn("loaded into VRAM", resp.raw_debug)


class TestVllmSleep(unittest.TestCase):
    def test_sleep_after_and_wake_before_the_next_run(self):
        state = {"asleep": False}

        def sleep(server, _body):
            state["asleep"] = True
            return 200, {}

        def wake(server, _body):
            state["asleep"] = False
            return 200, {}

        routes = {
            ("POST", "/sleep?level=1"): sleep,
            ("POST", "/wake_up"): wake,
            ("GET", "/is_sleeping"): lambda s, b: (200, {"is_sleeping": state["asleep"]}),
        }
        with FakeServer(routes) as srv:
            impl = _server_backend("vllm", srv.base_url)
            first = impl.generate(_req("vllm"))
            self.assertTrue(state["asleep"], "생성 뒤 재워야 한다")
            second = impl.generate(_req("vllm", unload=False))
        self.assertIn("put to sleep", first.raw_debug)
        self.assertIn("woken up", second.raw_debug)
        self.assertFalse(state["asleep"])
        # 두 번째 생성은 깨운 "뒤에" 나가야 한다.
        posts = srv.paths("POST")
        self.assertLess(posts.index("/wake_up"), len(posts) - 1)
        self.assertEqual(posts[-1], CHAT)

    def test_without_sleep_mode_it_says_how_to_enable_it(self):
        with FakeServer() as srv:
            resp = _server_backend("vllm", srv.base_url).generate(_req("vllm"))
        self.assertEqual(resp.status, "ok")
        self.assertIn("--enable-sleep-mode", resp.raw_debug)


class TestServerKind(unittest.TestCase):
    def test_aliases_are_their_own_kind(self):
        for name in ("ollama", "vllm", "llamacpp", "ninfer"):
            self.assertEqual(backends.get_backend(name).server_kind(), name)

    def test_openai_compat_on_a_standard_local_port(self):
        impl = backends.get_backend("openai_compat")
        impl.apply_base_url("http://localhost:11434")
        self.assertEqual(impl.server_kind(), "ollama")

    def test_openai_compat_elsewhere_is_unknown(self):
        """모르는 서버에는 추측으로 관리 요청을 보내지 않는다."""
        impl = backends.get_backend("openai_compat")
        impl.apply_base_url("https://api.example.com")
        self.assertEqual(impl.server_kind(), "")
        with FakeServer() as srv:
            impl.apply_base_url(srv.base_url)
            resp = impl.generate(_req("openai_compat"))
        self.assertEqual(srv.paths("POST"), [CHAT])
        self.assertIn("no immediate unload", resp.raw_debug)


class TestLmStudioRest(unittest.TestCase):
    def test_rest_unload_needs_no_lms(self):
        routes = {("POST", lmstudio_mod.LMS_REST_UNLOAD): (200, {"instance_id": "m"})}
        with FakeServer(routes) as srv, NoLms() as lms:
            resp = _lmstudio(srv.base_url).generate(_req("lmstudio"))
        self.assertEqual(lms.calls, [], "REST 로 됐는데 lms 까지 불렀다")
        self.assertEqual(srv.body_of(lmstudio_mod.LMS_REST_UNLOAD), {"instance_id": "m"})
        self.assertIn("unloaded from VRAM", resp.raw_debug)

    def test_old_lm_studio_falls_back_to_lms(self):
        with FakeServer() as srv, NoLms() as lms:
            resp = _lmstudio(srv.base_url).generate(_req("lmstudio"))
        self.assertEqual([c[1:] for c in lms.calls], [["unload", "m"]])
        self.assertIn("unloaded from VRAM", resp.raw_debug)

    def test_a_remote_lm_studio_is_never_unloaded_through_the_local_lms(self):
        """lms 는 이 PC 의 LM Studio 를 본다. 원격 서버의 모델을 내린다며 로컬을 건드리면 안 된다."""
        with FakeServer() as srv, NoLms() as lms, \
                mock.patch.object(lmstudio_mod, "is_loopback", return_value=False):
            resp = _lmstudio(srv.base_url).generate(_req("lmstudio"))
        self.assertEqual(lms.calls, [])
        self.assertIn("unload: skipped", resp.raw_debug)

    def test_unload_happens_even_when_generation_fails(self):
        """타임아웃/오류로 끝나도 모델은 이미 VRAM 에 올라와 있다."""
        routes = {
            ("POST", CHAT): (500, {"error": {"message": "boom"}}),
            ("POST", lmstudio_mod.LMS_REST_UNLOAD): (200, {}),
        }
        with FakeServer(routes) as srv, NoLms():
            resp = _lmstudio(srv.base_url).generate(_req("lmstudio"))
        self.assertTrue(resp.status.startswith("error"))
        self.assertIn(lmstudio_mod.LMS_REST_UNLOAD, srv.paths("POST"))

    def test_a_chosen_model_is_loaded_not_replaced(self):
        """JIT 를 꺼둔 LM Studio 에서 지정 모델이 내려가 있으면, /v1/models 의 다른
        모델로 갈아타는 대신 그 모델을 올려서 다시 묻는다."""
        def load(server, body):
            server.loaded.add(body["model"])
            return 200, {"instance_id": body["model"]}

        with FakeServer({("POST", lmstudio_mod.LMS_REST_LOAD): load}, loaded=set()) as srv, \
                NoLms() as lms:
            resp = _lmstudio(srv.base_url).generate(_req("lmstudio", "vlm", unload=False))
        self.assertEqual(resp.status, "ok", resp.status)
        chats = [b["model"] for m, p, b in srv.calls if p == CHAT]
        self.assertEqual(chats, ["vlm", "vlm"])
        self.assertEqual(lms.calls, [])

    def test_a_chosen_model_that_cannot_load_is_an_error_not_a_substitute(self):
        routes = {("POST", lmstudio_mod.LMS_REST_LOAD): (400, {"error": {"message": "no such model"}})}
        with FakeServer(routes, loaded=set()) as srv, NoLms():
            resp = _lmstudio(srv.base_url).generate(_req("lmstudio", "vlm", unload=False))
        self.assertTrue(resp.status.startswith("error"), resp.status)
        self.assertNotIn("some-other-model", [b.get("model") for m, p, b in srv.calls if p == CHAT])
        self.assertIn("no such model", resp.raw_debug)


class TestBatchStopStillUnloads(unittest.TestCase):
    """one_per_image 를 Stop 으로 끊어도 모델은 내려가야 한다."""

    def test_stop_mid_batch(self):
        unloaded = []

        class Spy:
            _served_model = "served"

            def __init__(self):
                self.calls = 0

            def generate(self, req):
                self.calls += 1
                if self.calls == 2:
                    cancel.request_stop("node-7")
                return base.LLMResponse(text="x", status="ok")

            def unload_model(self, model_id):
                unloaded.append(model_id)
                return f"unload: '{model_id}' unloaded from VRAM"

        spy = Spy()
        out = run_node(spy, images=4, backend="ollama", batch_mode=nodes_mod.BATCH_PER_IMAGE,
                       unload_after_generation=True, unique_id="node-7")
        self.assertEqual(spy.calls, 2)
        self.assertEqual(unloaded, ["served"])
        self.assertIn("unloaded from VRAM", out["result"][2])

    def test_a_finished_batch_is_not_unloaded_twice(self):
        unloaded = []

        class Spy:
            def generate(self, req):
                return base.LLMResponse(text="x", status="ok")

            def unload_model(self, model_id):
                unloaded.append(model_id)
                return ""

        run_node(Spy(), images=3, backend="ollama", batch_mode=nodes_mod.BATCH_PER_IMAGE,
                 unload_after_generation=True)
        self.assertEqual(unloaded, [], "끝까지 돈 배치는 마지막 장의 요청이 내린다")


class TestWidget(unittest.TestCase):
    def test_it_is_appended_last(self):
        self.assertEqual(nodes_mod.WIDGET_ORDER[-1], "unload_after_generation")
        spec = nodes_mod.LLMHubGenerate.INPUT_TYPES()["optional"]["unload_after_generation"]
        self.assertEqual(spec[0], "BOOLEAN")
        self.assertIs(spec[1]["default"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
