# -*- coding: utf-8 -*-
"""strata: 실행 버튼 → (꺼져 있으면) 서버 기동 → (내려가 있으면) /load → 생성 → (원하면) /unload.

Strata(serve/server.py)에는 모델을 올리고 내리는 API 가 있다. 그래서 NInfer 와 달리 언로드는 엔진만
내리고 서버 프로세스는 남긴다. 서버가 아예 꺼져 있을 때만 strata.root 의 serve/server.py 를 띄운다.

실제 서버 없이 돈다. 가짜 서버가 /health, /load, /unload, /settings, /metrics, /v1/* 를 흉내내고, 프로세스 기동은 가짜 Popen 이
"두 번째 확인부터 서버가 답한다" 로 흉내낸다. 포트 8080 의 진짜 Strata 에는 아무것도 보내지 않는다.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_PACK_ROOT))
_PACK_NAME = os.path.basename(_PACK_ROOT)

base = importlib.import_module(f"{_PACK_NAME}.backends.base")
backends = importlib.import_module(f"{_PACK_NAME}.backends")
oc_mod = importlib.import_module(f"{_PACK_NAME}.backends.openai_compat")
strata_mod = importlib.import_module(f"{_PACK_NAME}.backends.strata")
nodes_mod = importlib.import_module(f"{_PACK_NAME}.nodes")
LLMRequest = base.LLMRequest

sys.path.insert(0, os.path.join(_PACK_ROOT, "tests"))
from test_batch_extra_body_seed import run_node  # noqa: E402

A, B = "qwen3.8-flash-next-iq3_xxs", "qwen3.8-flash-next-iq2_xs"
CHAT = "/v1/chat/completions"


def make_root(folder, newest="iq3_xxs"):
    """Strata 폴더 흉내: .venv 의 python, serve/server.py, strata-<모델>.json 둘. newest 가 가장 최근에 쓴 설정이다."""
    for rel in (("serve", "server.py"), (".venv", "Scripts", "python.exe"), (".venv", "bin", "python")):
        path = os.path.join(folder, *rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
    now = time.time()
    for tag, name in (("iq3_xxs", A), ("iq2_xs", B)):
        path = os.path.join(folder, f"strata-{tag}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"model_name": name, "port": 8080}, fh)
        os.utime(path, (now, now if tag == newest else now - 1000))
    # 채팅 설정 파일은 설정이 아니다 -- 목록에 나오면 안 된다.
    open(os.path.join(folder, "strata-iq3_xxs.shared-settings.json"), "w").close()


class FakeStrata:
    """Strata 서버. up=False 면 연결을 그냥 끊어 꺼진 서버처럼 군다(요청은 그래도 기록한다)."""

    def __init__(self, up=True, loaded=True, model=A, service="strata", load_ok=True, busy=False,
                 shared=None, cvec=1, settings_ok=True):
        self.up, self.loaded, self.model, self.service = up, loaded, model, service
        self.load_ok, self.busy = load_ok, busy
        self.shared = dict(shared or {})       # 공유 설정 (POST /settings 가 바꾼다)
        self.cvec = cvec                       # /metrics 의 engine.cvec: 0 = 제어 벡터 없이 뜬 서버
        self.settings_ok = settings_ok
        self.calls = []
        self.freed = 0

    def posts(self):
        return [p for m, p, _ in self.calls if m == "POST"]

    def handle(self, method, path, body, headers=None):
        self.calls.append((method, path, body))
        headers = headers or {}
        if path == "/metrics":
            return 200, {"engine": {"cvec": self.cvec}}
        if path == "/settings":
            if method == "POST":
                # 진짜 서버의 _own_page: JSON 이어야 하고, 다른 사이트의 Origin 이 붙어 있으면 403
                if not headers.get("Content-Type", "").startswith("application/json"):
                    return 415, {"error": {"message": "send application/json"}}
                if not self.settings_ok or headers.get("Origin"):
                    return 403, {"error": {"message": "settings can be changed only from Strata's own page"}}
                self.shared = dict(body["defaults"])
            return 200, {"shared": bool(self.shared), "defaults": self.shared}
        if path == "/health":
            if self.service != "strata":
                return 200, {"status": "ok"}  # llama.cpp 의 /health
            return 200, {"status": "ok", "service": "strata", "model": self.model, "loaded": self.loaded}
        if path == "/unload":
            if self.busy:
                return 409, {"status": "busy"}
            if not self.loaded:
                return 200, {"status": "not loaded"}
            self.loaded = False
            return 200, {"status": "unloaded"}
        if path == "/load":
            if not self.load_ok:
                return 503, {"error": {"type": "server_error", "message": "the GPU is in use by another program"}}
            self.loaded = True
            return 200, {"status": "loaded"}
        if path == "/v1/models":
            return 200, {"data": [{"id": self.model}]}
        if path == CHAT:
            return 200, {"model": self.model,
                         "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}
        return 404, {"error": {"message": "not found"}}

    def __enter__(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _do(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                if not outer.up:
                    outer.calls.append((method, self.path, None))
                    self.close_connection = True
                    return  # 응답 없이 끊는다 -> 클라이언트는 ConnectionError
                code, payload = outer.handle(method, self.path, json.loads(raw) if raw else None, dict(self.headers))
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
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._tmp.name, "Strata")
        os.makedirs(self.root)
        make_root(self.root)
        self._patches = [
            mock.patch.object(strata_mod, "POLL_S", 0.01),
            mock.patch.object(strata_mod, "SERVER_LOG", os.path.join(self._tmp.name, "strata-server.log")),
            # 테스트끼리 "이 노드가 띄운 프로세스" 가 새지 않게 한다.
            mock.patch.dict(strata_mod._STARTED, {"proc": None, "config": ""}),
            # ComfyUI 밖에서는 VRAM 비우기가 늘 실패한다. 부른 횟수만 센다.
            mock.patch.object(strata_mod, "free_comfy_vram", side_effect=self._freed),
        ]
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
        self._tmp.cleanup()
        return False

    @property
    def url(self):
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def port(self):
        return self._server.server_address[1]

    def backend(self, root=None, **section):
        cfg = {"port": self.port}
        cfg.update(section)
        with mock.patch.dict(os.environ, {strata_mod.ROOT_ENV: self.root if root is None else root}):
            impl = strata_mod.StrataBackend(config={"strata": cfg})
        impl.apply_base_url(self.url)
        return impl

    def popen(self, exit_after=None):
        """subprocess.Popen 대역. 띄운 설정의 model_name 으로 서버가 두 번째 확인부터 답한다."""
        srv = self
        spawned = []

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                spawned.append((cmd, kwargs))
                self.config = cmd[cmd.index("--config") + 1]
                self.returncode = None
                self.polls = 0
                self.terminated = False

            def poll(self):
                self.polls += 1
                if exit_after is not None and self.polls > exit_after:
                    self.returncode = 3
                    return 3
                if self.polls >= 2:
                    srv.up, srv.loaded = True, True
                    srv.model = strata_mod._model_name(self.config)
                return self.returncode

            def terminate(self):
                self.terminated = True
                self.returncode = 0
                srv.up = False

            def wait(self, _timeout=None):
                return self.returncode

            def kill(self):
                self.returncode = -9

        return mock.patch.object(strata_mod.subprocess, "Popen", FakePopen), spawned


def _req(model="", unload=False, reasoning="", content_mode=""):
    return LLMRequest("strata", model, "", "hi", unload_after=unload, timeout_s=5, reasoning=reasoning,
                      content_mode=content_mode)


class TestRegistration(unittest.TestCase):
    def test_factory_returns_the_managing_backend(self):
        impl = backends.get_backend("strata")
        self.assertIsInstance(impl, strata_mod.StrataBackend)
        self.assertEqual(impl.base_url, "http://127.0.0.1:8080")
        self.assertEqual(impl.server_kind(), "strata")

    def test_it_is_appended_last_and_is_an_alias(self):
        self.assertEqual(backends.BACKEND_NAMES[-1], "strata")
        self.assertIn("strata", backends.OPENAI_COMPAT_ALIASES)

    def test_the_suite_never_sees_the_dev_pcs_strata_folder(self):
        """tests/__init__.py 가 strata.root 를 빈 값으로 덮는다. 안 그러면 테스트가 진짜 Strata 를 띄울 수 있다."""
        self.assertEqual(backends.get_backend("strata").root, "")

    def test_the_shared_port_is_probed_once(self):
        """llama.cpp 와 Strata 가 8080 을 같이 쓴다. 목록을 채울 때 같은 포트를 두 번 묻지 않는다."""
        asked = []
        with mock.patch.object(oc_mod, "_probe", lambda base, t, h: asked.append(base) or []), \
                mock.patch.dict(oc_mod._MODEL_CACHE, {"at": 0.0, "ids": []}), \
                mock.patch.dict(os.environ, {"LLMHUB_SKIP_MODEL_PROBE": ""}):
            oc_mod.list_server_models()
        self.assertEqual(len(asked), len(set(asked)), asked)
        self.assertIn("http://127.0.0.1:8080", asked)


class TestUnload(unittest.TestCase):
    def test_unload_posts_to_strata_and_leaves_the_server_up(self):
        with FakeStrata() as srv:
            resp = srv.backend().generate(_req(A, unload=True))
        self.assertEqual(resp.status, "ok", resp.raw_debug)
        self.assertEqual(srv.posts(), [CHAT, "/unload"])
        self.assertFalse(srv.loaded)
        self.assertIn("VRAM and RAM freed", resp.raw_debug)
        self.assertIn("next run loads it again", resp.raw_debug)

    def test_without_unload_the_model_stays(self):
        with FakeStrata() as srv:
            srv.backend().generate(_req(A))
        self.assertEqual(srv.posts(), [CHAT])
        self.assertTrue(srv.loaded)

    def test_a_busy_server_is_reported_not_unloaded(self):
        with FakeStrata(busy=True) as srv:
            note = srv.backend().unload_model(A)
        self.assertIn("busy", note)
        self.assertTrue(srv.loaded)

    def test_an_already_unloaded_model_says_so(self):
        with FakeStrata(loaded=False) as srv:
            self.assertIn("already unloaded", srv.backend().unload_model(A))

    def test_unload_goes_to_any_address_because_the_alias_names_the_server(self):
        """openai_base_url 에 다른 주소를 적어도 strata 를 골랐으면 그 주소는 Strata 다. /unload 는 보낸다."""
        with FakeStrata() as srv:
            impl = srv.backend(port=1)
            self.assertFalse(impl.managed())
            resp = impl.generate(_req(A, unload=True))
        self.assertEqual(resp.status, "ok", resp.raw_debug)
        self.assertEqual(srv.posts(), [CHAT, "/unload"])


class TestLoad(unittest.TestCase):
    def test_an_unloaded_model_is_loaded_before_the_request(self):
        with FakeStrata(loaded=False) as srv:
            resp = srv.backend().generate(_req(A))
        self.assertEqual(resp.status, "ok", resp.raw_debug)
        self.assertEqual(srv.posts(), ["/load", CHAT])
        self.assertIn("model loaded", resp.raw_debug)
        self.assertEqual(srv.freed, 1, "올리기 전에 ComfyUI 모델을 내려야 한다")

    def test_a_loaded_model_is_left_alone(self):
        with FakeStrata() as srv:
            srv.backend().generate(_req(A))
        self.assertNotIn("/load", srv.posts())
        self.assertEqual(srv.freed, 0)

    def test_a_refused_load_is_an_error_with_the_servers_reason(self):
        with FakeStrata(loaded=False, load_ok=False) as srv:
            resp = srv.backend().generate(_req(A))
        self.assertTrue(resp.status.startswith("error: strata"), resp.status)
        self.assertIn("GPU is in use", resp.status)
        self.assertNotIn(CHAT, srv.posts())

    def test_freeing_comfy_vram_can_be_turned_off(self):
        with FakeStrata(loaded=False) as srv:
            srv.backend(free_comfy_vram=False).generate(_req(A))
        self.assertEqual(srv.freed, 0)

    def test_the_round_trip_unload_then_load(self):
        with FakeStrata() as srv:
            impl = srv.backend()
            impl.generate(_req(A, unload=True))
            srv.calls.clear()
            resp = impl.generate(_req(A))
        self.assertEqual(resp.status, "ok", resp.raw_debug)
        self.assertEqual(srv.posts(), ["/load", CHAT])


class TestStart(unittest.TestCase):
    def test_a_stopped_server_is_started_with_the_chosen_config(self):
        with FakeStrata(up=False) as srv:
            patch, spawned = srv.popen()
            with patch:
                resp = srv.backend().generate(_req(B))
        self.assertEqual(resp.status, "ok", resp.raw_debug)
        cmd, kwargs = spawned[0]
        self.assertEqual(os.path.basename(cmd[0]), "python.exe" if os.name == "nt" else "python")
        self.assertEqual(cmd[1:], [os.path.join(srv.root, "serve", "server.py"), "--engine", "strata",
                                   "--config", os.path.join(srv.root, "strata-iq2_xs.json"),
                                   "--port", str(srv.port)])
        self.assertEqual(kwargs["cwd"], srv.root)
        self.assertEqual(srv.posts(), [CHAT])
        self.assertEqual([b for m, p, b in srv.calls if p == CHAT][0]["model"], B)
        self.assertIn(f"'{B}' is up", resp.raw_debug)
        self.assertEqual(srv.freed, 1)

    def test_the_config_tag_works_as_a_model_name_too(self):
        with FakeStrata(up=False) as srv:
            patch, spawned = srv.popen()
            with patch:
                srv.backend().generate(_req("iq2_xs"))
        self.assertTrue(spawned[0][0][-3].endswith("strata-iq2_xs.json"), spawned[0][0])

    def test_auto_starts_the_most_recently_used_config(self):
        with FakeStrata(up=False) as srv:
            patch, spawned = srv.popen()
            with patch:
                resp = srv.backend().generate(_req(""))
        self.assertTrue(spawned[0][0][-3].endswith("strata-iq3_xxs.json"), spawned[0][0])
        self.assertIn("no model chosen", resp.raw_debug)
        self.assertEqual([b for m, p, b in srv.calls if p == CHAT][0]["model"], A)

    def test_config_model_wins_over_the_most_recent(self):
        with FakeStrata(up=False) as srv:
            patch, spawned = srv.popen()
            with patch:
                srv.backend(model="iq2_xs").generate(_req(""))
        self.assertTrue(spawned[0][0][-3].endswith("strata-iq2_xs.json"))

    def test_a_config_model_that_does_not_exist_is_an_error(self):
        with FakeStrata(up=False) as srv:
            patch, spawned = srv.popen()
            with patch:
                resp = srv.backend(model="nope").generate(_req(""))
        self.assertIn("strata.model 'nope'", resp.status)
        self.assertEqual(spawned, [])

    def test_without_a_root_it_says_how_to_start_strata(self):
        with FakeStrata(up=False) as srv:
            patch, spawned = srv.popen()
            with patch:
                resp = srv.backend(root="").generate(_req(A))
        self.assertIn("not running", resp.status)
        self.assertIn("strata.root", resp.status)
        self.assertEqual(spawned, [])

    def test_a_root_without_configs_is_an_error(self):
        with FakeStrata(up=False) as srv:
            empty = os.path.join(srv.root, "empty")
            os.makedirs(empty)
            patch, spawned = srv.popen()
            with patch:
                resp = srv.backend(root=empty).generate(_req(A))
        self.assertIn("no strata-<model>.json", resp.status)
        self.assertEqual(spawned, [])

    def test_a_server_that_dies_while_starting_is_reported(self):
        with FakeStrata(up=False) as srv:
            patch, _spawned = srv.popen(exit_after=0)
            with patch:
                resp = srv.backend().generate(_req(A))
        self.assertIn("exited while starting", resp.status)
        self.assertIn("exit code 3", resp.status)
        self.assertNotIn(CHAT, srv.posts())

    def test_a_start_that_never_finishes_times_out(self):
        with FakeStrata(up=False) as srv:
            patch, _spawned = srv.popen()
            with patch, mock.patch.object(srv, "handle", lambda *a: (503, {})):
                # 서버가 "떠는" 있는데 모델이 안 올라온다 -> loaded 가 영영 false.
                resp = srv.backend(start_timeout_s=1).generate(_req(A))
        self.assertIn("was not ready after 1s", resp.status)

    def test_auto_start_off_uses_only_a_running_server(self):
        with FakeStrata(up=False) as srv:
            patch, spawned = srv.popen()
            with patch:
                impl = srv.backend(auto_start=False)
                self.assertFalse(impl.managed())
                resp = impl.generate(_req(A))
        self.assertEqual(spawned, [])
        self.assertTrue(resp.status.startswith("error:"), resp.status)


class TestSwitch(unittest.TestCase):
    def test_a_server_this_session_did_not_start_is_not_switched(self):
        with FakeStrata(model=A) as srv:
            patch, spawned = srv.popen()
            with patch:
                resp = srv.backend().generate(_req(B))
        self.assertIn("not started by this ComfyUI session", resp.status)
        self.assertIn(A, resp.status)
        self.assertEqual(spawned, [])
        self.assertNotIn(CHAT, srv.posts())

    def test_a_server_this_session_started_is_restarted_with_the_other_config(self):
        with FakeStrata(up=False) as srv:
            patch, spawned = srv.popen()
            with patch:
                impl = srv.backend()
                impl.generate(_req(A))
                first = strata_mod._STARTED["proc"]
                srv.calls.clear()
                resp = impl.generate(_req(B))
        self.assertEqual(resp.status, "ok", resp.raw_debug)
        self.assertTrue(first.terminated)
        self.assertEqual(srv.posts(), ["/unload", CHAT])
        self.assertTrue(spawned[1][0][-3].endswith("strata-iq2_xs.json"))
        self.assertIn("to switch to", resp.raw_debug)
        self.assertEqual(srv.model, B)

    def test_the_running_model_is_used_as_it_is(self):
        with FakeStrata(model=A) as srv:
            patch, spawned = srv.popen()
            with patch:
                resp = srv.backend().generate(_req(A))
        self.assertEqual(resp.status, "ok")
        self.assertEqual(spawned, [])

    def test_a_name_that_is_no_config_is_passed_through(self):
        """Strata 는 아무 model 이름이나 받는다. 설정 이름이 아니면 바꿀 것도 없다."""
        with FakeStrata(model=A) as srv:
            resp = srv.backend().generate(_req("strata"))
        self.assertEqual(resp.status, "ok", resp.status)
        self.assertEqual([b for m, p, b in srv.calls if p == CHAT][0]["model"], "strata")


class TestNotStrata(unittest.TestCase):
    def test_something_else_on_the_port_is_an_error_not_a_guess(self):
        """llama.cpp 도 8080 에 /health 를 낸다. service 가 strata 가 아니면 그렇다고 말한다."""
        with FakeStrata(service="llamacpp") as srv:
            resp = srv.backend().generate(_req(A))
        self.assertIn("something else answers", resp.status)
        self.assertIn("llama.cpp", resp.status)
        self.assertNotIn(CHAT, srv.posts())


class TestReasoning(unittest.TestCase):
    def _chat(self, reasoning="", extra_body=None):
        with FakeStrata() as srv:
            req = _req(A, reasoning=reasoning)
            req.extra_body = extra_body or {}
            resp = srv.backend().generate(req)
        body = [b for m, p, b in srv.calls if p == CHAT][0]
        return body, resp

    def test_off_sends_reasoning_effort_none(self):
        body, resp = self._chat("off")
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertIn("strata: reasoning=off", resp.raw_debug)

    def test_a_level_is_passed_through(self):
        body, _ = self._chat("medium")
        self.assertEqual(body["reasoning_effort"], "medium")

    def test_default_sends_nothing(self):
        body, resp = self._chat("")
        self.assertNotIn("reasoning_effort", body)
        self.assertNotIn("reasoning=", resp.raw_debug)

    def test_extra_body_wins(self):
        body, resp = self._chat("off", {"reasoning_effort": "high"})
        self.assertEqual(body["reasoning_effort"], "high")
        self.assertIn("extra_body already sets it", resp.raw_debug)

    def test_other_aliases_never_send_reasoning_fields(self):
        """nodes.py 가 ninfer/strata 에만 req.reasoning 을 채운다. 다른 별칭은 빈 값이라 아무것도 안 나간다."""
        for name in ("ollama", "vllm", "llamacpp"):
            payload = backends.get_backend(name)._build_payload(LLMRequest(name, "", "", "hi"), [], "m")
            self.assertNotIn("reasoning_effort", payload, name)
            self.assertNotIn("chat_template_kwargs", payload, name)


class TestContentMode(unittest.TestCase):
    """content_mode 의 NSFW / SFW 는 NInfer 설정 UI 의 "거부 성향 완화 (실험)" 스위치와 같은 길이다:
    Strata 의 공유 설정 experimental_speed_projection 을 POST /settings 로 바꾼다. 재시작 없이, 모든 클라이언트에."""

    def _run(self, mode, **server):
        with FakeStrata(**server) as srv:
            resp = srv.backend().generate(_req(A, content_mode=mode))
        return srv, resp

    def test_nsfw_turns_the_shared_switch_on_before_generating(self):
        srv, resp = self._run("NSFW", shared={"temperature": 0.5})
        # POST /settings 는 defaults 전체를 바꾸므로 다른 공유 값은 그대로 다시 보낸다
        self.assertEqual(srv.shared, {"temperature": 0.5, "experimental_speed_projection": True})
        self.assertLess(srv.posts().index("/settings"), srv.posts().index(CHAT))
        self.assertEqual(resp.status, "ok")
        self.assertIn("strata: content_mode=NSFW", resp.raw_debug)

    def test_sfw_turns_it_off(self):
        srv, resp = self._run("SFW", shared={"experimental_speed_projection": True})
        self.assertIs(srv.shared["experimental_speed_projection"], False)
        self.assertIn("strata: content_mode=SFW", resp.raw_debug)

    def test_default_leaves_the_switch_alone(self):
        srv, resp = self._run("", shared={"experimental_speed_projection": True})
        self.assertNotIn("/settings", [p for _m, p, _b in srv.calls])
        self.assertNotIn("content_mode", resp.raw_debug)

    def test_a_switch_already_in_that_position_is_not_posted_again(self):
        srv, resp = self._run("NSFW", shared={"experimental_speed_projection": True})
        self.assertNotIn("/settings", srv.posts())
        self.assertIn("strata: content_mode=NSFW", resp.raw_debug)

    def test_without_the_vector_it_is_saved_for_the_next_start(self):
        """벡터 없이 뜬 서버: 설정 UI 의 스위치처럼 저장은 되고, 다음에 벡터를 불러올 때 그 값으로 시작한다."""
        srv, resp = self._run("NSFW", cvec=0)
        self.assertIs(srv.shared["experimental_speed_projection"], True)
        self.assertEqual(resp.status, "ok")
        self.assertIn("next start", resp.raw_debug)
        self.assertIn("config UI", resp.raw_debug)

    def test_a_refused_switch_is_an_error_with_the_servers_reason(self):
        srv, resp = self._run("NSFW", settings_ok=False)
        self.assertTrue(resp.status.startswith("error: strata - could not switch to NSFW"), resp.status)
        self.assertIn("own page", resp.status)
        self.assertNotIn(CHAT, [p for _m, p, _b in srv.calls])

    def test_the_switch_goes_to_an_unmanaged_address_too(self):
        """언로드처럼: 드롭다운에서 strata 를 골랐다는 것이 그 주소가 Strata 라는 뜻이다."""
        with FakeStrata() as srv:
            resp = srv.backend(auto_start=False).generate(_req(A, content_mode="NSFW"))
        self.assertIs(srv.shared["experimental_speed_projection"], True)
        self.assertEqual(resp.status, "ok")


class TestDropdown(unittest.TestCase):
    def test_configs_are_listed_newest_first_while_the_server_is_off(self):
        with tempfile.TemporaryDirectory() as folder:
            make_root(folder, newest="iq2_xs")
            with mock.patch.dict(os.environ, {strata_mod.ROOT_ENV: folder, "LLMHUB_SKIP_MODEL_PROBE": ""}):
                self.assertEqual(strata_mod.list_strata_models(), [B, A])

    def test_the_probe_switch_also_covers_this_list(self):
        with tempfile.TemporaryDirectory() as folder:
            make_root(folder)
            with mock.patch.dict(os.environ, {strata_mod.ROOT_ENV: folder, "LLMHUB_SKIP_MODEL_PROBE": "1"}):
                self.assertEqual(strata_mod.list_strata_models(), [])

    def test_no_root_lists_nothing(self):
        with mock.patch.dict(os.environ, {strata_mod.ROOT_ENV: "", "LLMHUB_SKIP_MODEL_PROBE": ""}):
            self.assertEqual(strata_mod.list_strata_models(), [])

    def test_a_config_without_a_model_name_uses_the_servers_default(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "strata-q2_0.json"), "w") as fh:
                json.dump({"port": 8080}, fh)
            with mock.patch.dict(os.environ, {strata_mod.ROOT_ENV: folder, "LLMHUB_SKIP_MODEL_PROBE": ""}):
                self.assertEqual(strata_mod.list_strata_models(), [strata_mod.DEFAULT_MODEL_NAME])


class TestNodeInputs(unittest.TestCase):
    """노드의 reasoning 은 ninfer 와 strata 에, content_mode 는 strata 에만 넘어간다. ninfer_context 는 여전히 ninfer 에만."""

    def _run(self, backend, **kwargs):
        seen = []

        class Spy:
            def generate(self, req):
                seen.append(req)
                return base.LLMResponse(text="x", status="ok")

        run_node(Spy(), backend=backend, **kwargs)
        return seen[0]

    def test_strata_gets_reasoning_but_not_the_ninfer_context(self):
        req = self._run("strata", ninfer_context=30000, reasoning="off")
        self.assertEqual((req.ninfer_context, req.reasoning), (0, "off"))

    def test_other_aliases_get_neither(self):
        req = self._run("llamacpp", ninfer_context=30000, reasoning="off")
        self.assertEqual((req.ninfer_context, req.reasoning), (0, ""))

    def test_server_model_reaches_the_request(self):
        req = self._run("strata", server_model=B)
        self.assertEqual(req.model, B)

    def test_content_mode_reaches_strata_only(self):
        self.assertEqual(self._run("strata", content_mode="NSFW").content_mode, "NSFW")
        self.assertEqual(self._run("ninfer", content_mode="NSFW").content_mode, "")

    def test_the_default_choice_and_junk_mean_nothing(self):
        self.assertEqual(self._run("strata", content_mode=nodes_mod.CONTENT_MODE_DEFAULT).content_mode, "")
        self.assertEqual(self._run("strata", content_mode=True).content_mode, "")

    def test_content_mode_is_the_last_widget(self):
        """저장된 워크플로우의 widgets_values 자리가 그대로다: 새 위젯은 맨 뒤에만 붙인다."""
        self.assertEqual(nodes_mod.WIDGET_ORDER[-3:], ["ninfer_context", "reasoning", "content_mode"])


if __name__ == "__main__":
    unittest.main()
