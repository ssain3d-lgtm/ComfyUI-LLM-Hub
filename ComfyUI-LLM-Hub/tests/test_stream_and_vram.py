# -*- coding: utf-8 -*-
"""실시간 모니터링(스트리밍) + LM Studio VRAM 관리 검증.

ComfyUI / LM Studio / CLI 로그인 없이 돌아간다.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import unittest
from unittest import mock

_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_PACK_ROOT))
_PACK_NAME = os.path.basename(_PACK_ROOT)

base = importlib.import_module(f"{_PACK_NAME}.backends.base")
stream = importlib.import_module(f"{_PACK_NAME}.utils.stream")
claude_mod = importlib.import_module(f"{_PACK_NAME}.backends.claude_code")
gemini_mod = importlib.import_module(f"{_PACK_NAME}.backends.gemini")
lmstudio_mod = importlib.import_module(f"{_PACK_NAME}.backends.lmstudio")
nodes_mod = importlib.import_module(f"{_PACK_NAME}.nodes")
LLMRequest = base.LLMRequest

sys.path.insert(0, os.path.join(_PACK_ROOT, "tests"))
from mock_lmstudio import MockLMStudio  # noqa: E402
from test_cli_backends import FakeCli, _patch  # noqa: E402


class RecordingEmitter:
    """StreamEmitter 와 같은 인터페이스로 호출을 기록한다."""

    enabled = True

    def __init__(self):
        self.text = ""
        self.statuses = []
        self.deltas = []
        self.finished = None

    def append(self, delta):
        self.deltas.append(delta)
        self.text += delta

    def set_status(self, status):
        self.statuses.append(status)

    def reset_text(self, text=""):
        self.text = text

    def finish(self, status="", text=None):
        if text is not None:
            self.text = text
        self.finished = status


class StreamFake(FakeCli):
    """stdout 을 줄 단위로 on_line 에 흘려주는 run_cli_stream 대역."""

    def __call__(self, args, *, cwd, stdin_text=None, timeout_s=300, on_line=None):
        self.args = list(args)
        self.cwd = cwd
        self.stdin = stdin_text
        if self.write_last_message is not None and "-o" in args:
            with open(args[args.index("-o") + 1], "w", encoding="utf-8") as fh:
                fh.write(self.write_last_message)
        if on_line:
            for line in (self.stdout or "").splitlines():
                on_line(line + "\n")
        return self.code, self.stdout, self.stderr, 1.0


def _stream_patch(module, fake):
    return mock.patch.multiple(
        module, run_cli_stream=fake, resolve_cli=lambda name: f"/usr/bin/{name}"
    )


class TestEmitter(unittest.TestCase):
    def test_noop_without_comfyui(self):
        # ComfyUI 밖에서는 조용히 비활성화되어야 한다.
        emitter = stream.StreamEmitter(node_id="3", enabled=True)
        self.assertFalse(emitter.enabled)
        emitter.append("무시됨")
        emitter.finish("ok")

    def test_disabled_without_node_id(self):
        self.assertFalse(stream.StreamEmitter(node_id=None).enabled)

    def test_pushes_accumulated_text(self):
        sent = []

        class FakeServer:
            def send_sync(self, event, payload):
                sent.append((event, payload))

        with mock.patch.object(stream, "_prompt_server", return_value=FakeServer()):
            emitter = stream.StreamEmitter(node_id="7", throttle_s=0)
            emitter.append("안")
            emitter.append("녕")
            emitter.finish("ok")

        self.assertTrue(sent)
        self.assertEqual(sent[0][0], stream.EVENT_NAME)
        self.assertEqual(sent[-1][1]["text"], "안녕")  # 누적 전문을 보낸다
        self.assertTrue(sent[-1][1]["done"])
        self.assertEqual(sent[-1][1]["node"], "7")

    def test_emitter_failure_does_not_raise(self):
        class BrokenServer:
            def send_sync(self, *_a):
                raise RuntimeError("웹소켓 끊김")

        with mock.patch.object(stream, "_prompt_server", return_value=BrokenServer()):
            emitter = stream.StreamEmitter(node_id="1", throttle_s=0)
            emitter.append("x")  # 예외가 새어나오면 안 된다
            self.assertFalse(emitter.enabled)


CLAUDE_STREAM = "\n".join([
    json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-5"}),
    # 정상 호출에도 항상 흘러나오는 이벤트 — rate_limited 오탐의 원인이었다.
    json.dumps({"type": "rate_limit_event",
                "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour"}}),
    json.dumps({"type": "stream_event",
                "event": {"type": "content_block_delta",
                          "delta": {"type": "text_delta", "text": "안"}}}),
    json.dumps({"type": "stream_event",
                "event": {"type": "content_block_delta",
                          "delta": {"type": "text_delta", "text": "녕"}}}),
    json.dumps({"type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "Read"}]}}),
    json.dumps({"is_error": False, "result": "안녕", "subtype": "success"}),
])


class TestClaudeStreaming(unittest.TestCase):
    def test_deltas_reach_emitter(self):
        emitter = RecordingEmitter()
        fake = StreamFake(stdout=CLAUDE_STREAM)
        with _stream_patch(claude_mod, fake):
            resp = claude_mod.ClaudeCodeBackend(config={}).generate(
                LLMRequest("claude", "", "", "안녕", emitter=emitter)
            )
        self.assertEqual(resp.status, "ok")
        self.assertEqual(resp.text, "안녕")
        self.assertEqual(emitter.deltas, ["안", "녕"])
        self.assertIn("도구 사용: Read", emitter.statuses)

    def test_streaming_flags_used(self):
        fake = StreamFake(stdout=CLAUDE_STREAM)
        with _stream_patch(claude_mod, fake):
            claude_mod.ClaudeCodeBackend(config={}).generate(
                LLMRequest("claude", "", "", "안녕", emitter=RecordingEmitter())
            )
        self.assertEqual(fake.flag_value("--output-format"), "stream-json")
        self.assertIn("--include-partial-messages", fake.args)
        self.assertIn("--verbose", fake.args)  # stream-json 에 필요(실측)

    def test_rate_limit_event_is_not_a_false_positive(self):
        """정상 스트림의 rate_limit_event 를 rate_limited 로 오인하면 안 된다."""
        fake = StreamFake(stdout=CLAUDE_STREAM)
        with _stream_patch(claude_mod, fake):
            resp = claude_mod.ClaudeCodeBackend(config={}).generate(
                LLMRequest("claude", "", "", "안녕", emitter=RecordingEmitter())
            )
        self.assertNotEqual(resp.status, "rate_limited")
        self.assertEqual(resp.status, "ok")

    def test_partial_text_kept_when_stream_breaks(self):
        broken = "\n".join(CLAUDE_STREAM.splitlines()[:4])  # 최종 result 없음
        emitter = RecordingEmitter()
        fake = StreamFake(code=1, stdout=broken, stderr="죽음")
        with _stream_patch(claude_mod, fake):
            resp = claude_mod.ClaudeCodeBackend(config={}).generate(
                LLMRequest("claude", "", "", "안녕", emitter=emitter)
            )
        self.assertEqual(resp.text, "안녕")  # 받은 부분까지는 살린다

    def test_no_streaming_flags_when_view_off(self):
        fake = FakeCli(stdout=json.dumps({"is_error": False, "result": "안녕"}))
        with _patch(claude_mod, fake):
            claude_mod.ClaudeCodeBackend(config={}).generate(
                LLMRequest("claude", "", "", "안녕")  # emitter 없음
            )
        self.assertEqual(fake.flag_value("--output-format"), "json")


GEMINI_STREAM = "\n".join([
    json.dumps({"type": "init", "session_id": "x", "model": "gemini-2.5-flash"}),
    json.dumps({"type": "message", "role": "user", "content": "안녕"}),
    json.dumps({"type": "message", "role": "assistant", "content": "안", "delta": True}),
    json.dumps({"type": "message", "role": "assistant", "content": "녕", "delta": True}),
    json.dumps({"type": "tool_use", "tool_name": "read_file"}),
    json.dumps({"type": "result", "status": "success", "stats": {}}),
])


class TestGeminiStreaming(unittest.TestCase):
    def _backend(self):
        return gemini_mod.GeminiBackend(
            config={"defaults": {"gemini_model": "gemini-2.5-flash",
                                 "gemini_approval_mode": "plan"}}
        )

    def test_deltas_and_result(self):
        emitter = RecordingEmitter()
        fake = StreamFake(stdout=GEMINI_STREAM)
        with _stream_patch(gemini_mod, fake):
            resp = self._backend().generate(
                LLMRequest("gemini", "", "", "안녕", emitter=emitter)
            )
        self.assertEqual(fake.flag_value("-o"), "stream-json")
        self.assertEqual(emitter.deltas, ["안", "녕"])
        self.assertEqual(resp.text, "안녕")
        self.assertEqual(resp.status, "ok")
        self.assertIn("도구 사용: read_file", emitter.statuses)

    def test_user_echo_is_not_counted(self):
        """role=user 이벤트를 본문으로 착각하면 안 된다."""
        emitter = RecordingEmitter()
        fake = StreamFake(stdout=GEMINI_STREAM)
        with _stream_patch(gemini_mod, fake):
            self._backend().generate(LLMRequest("gemini", "", "", "안녕", emitter=emitter))
        self.assertEqual(emitter.text, "안녕")  # user 에코가 섞이면 길어진다

    def test_error_result_maps_to_status(self):
        payload = "\n".join([
            json.dumps({"type": "init"}),
            json.dumps({"type": "result", "status": "error",
                        "error": {"message": "429 quota exceeded"}}),
        ])
        fake = StreamFake(code=1, stdout=payload)
        with _stream_patch(gemini_mod, fake):
            resp = self._backend().generate(
                LLMRequest("gemini", "", "", "안녕", emitter=RecordingEmitter())
            )
        self.assertEqual(resp.status, "rate_limited")


class TestLMStudioVram(unittest.TestCase):
    def test_ttl_sent_in_payload(self):
        with MockLMStudio(script=[{"content": "안녕"}]) as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url}}
            )
            backend.generate(LLMRequest("lmstudio", "", "", "안녕", ttl_sec=300))
        self.assertEqual(server.requests[0]["ttl"], 300)

    def test_ttl_omitted_when_zero(self):
        with MockLMStudio(script=[{"content": "안녕"}]) as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url}}
            )
            backend.generate(LLMRequest("lmstudio", "", "", "안녕", ttl_sec=0))
        self.assertNotIn("ttl", server.requests[0])

    def test_unload_called_with_served_model(self):
        calls = []

        def fake_run(args, **_kwargs):
            calls.append(args)
            return 0, "", "", 0.1

        with MockLMStudio(script=[{"content": "안녕"}]) as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url}}
            )
            proc_mod = importlib.import_module(f"{_PACK_NAME}.utils.proc")
            with mock.patch.object(proc_mod, "run_cli", side_effect=fake_run), \
                 mock.patch.object(proc_mod, "resolve_cli", return_value="/usr/bin/lms"):
                resp = backend.generate(
                    LLMRequest("lmstudio", "", "", "안녕", unload_after=True)
                )

        self.assertTrue(calls, "lms unload 가 호출되지 않았다")
        self.assertEqual(calls[0][1], "unload")
        self.assertEqual(calls[0][2], "mock-model-a")  # 응답이 알려준 실제 모델
        self.assertIn("VRAM 에서 내렸습니다", resp.raw_debug)

    def test_no_unload_when_disabled(self):
        calls = []
        with MockLMStudio(script=[{"content": "안녕"}]) as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url}}
            )
            proc_mod = importlib.import_module(f"{_PACK_NAME}.utils.proc")
            with mock.patch.object(proc_mod, "run_cli",
                                   side_effect=lambda a, **k: calls.append(a) or (0, "", "", 0.1)):
                backend.generate(LLMRequest("lmstudio", "", "", "안녕", unload_after=False))
        self.assertEqual(calls, [])

    def test_missing_lms_is_explained_not_fatal(self):
        with MockLMStudio(script=[{"content": "안녕"}]) as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url},
                        "cli_paths": {"lms": "definitely-not-a-real-lms-xyz"}}
            )
            resp = backend.generate(
                LLMRequest("lmstudio", "", "", "안녕", unload_after=True)
            )
        self.assertEqual(resp.status, "ok")       # 생성 자체는 성공해야 한다
        self.assertIn("lms CLI", resp.raw_debug)  # 이유를 알려준다

    def test_streaming_disabled_when_tools_declared(self):
        """file_access=True 는 tool_calls 조립 문제로 스트리밍하지 않는다."""
        emitter = RecordingEmitter()
        fixtures = os.path.join(_PACK_ROOT, "tests", "fixtures")
        script = [
            {"tool_calls": [{"name": "read_file", "path": "test.txt"}]},
            {"content": "요약"},
        ]
        with MockLMStudio(script=script) as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url}}
            )
            resp = backend.generate(LLMRequest(
                "lmstudio", "", "", "요약해", workspace_dir=fixtures,
                file_access=True, emitter=emitter,
            ))
        self.assertEqual(resp.status, "ok")
        self.assertTrue(all(not r.get("stream") for r in server.requests))
        # 스트리밍은 못 해도 도구 진행 상황은 보여준다.
        self.assertTrue(any("도구 사용" in s for s in emitter.statuses))


class TestNodeWiring(unittest.TestCase):
    def test_stream_view_and_vram_inputs(self):
        spec = nodes_mod.LLMHubGenerate.INPUT_TYPES()
        self.assertEqual(spec["required"]["stream_view"][0], ["plain", "markdown", "off"])
        self.assertIn("lmstudio_model", spec["optional"])
        self.assertIn("lmstudio_ttl_sec", spec["optional"])
        self.assertIn("lmstudio_unload_after", spec["optional"])
        self.assertEqual(spec["hidden"], {"unique_id": "UNIQUE_ID"})

    def test_model_dropdown_always_has_auto(self):
        spec = nodes_mod.LLMHubGenerate.INPUT_TYPES()
        self.assertEqual(spec["optional"]["lmstudio_model"][0][0], nodes_mod.AUTO_MODEL)

    def test_dropdown_overrides_model_for_lmstudio(self):
        captured = {}

        class Spy:
            def generate(self, req):
                captured["model"] = req.model
                return base.LLMResponse(text="x", status="ok")

        with mock.patch.object(nodes_mod, "get_backend", return_value=Spy()):
            nodes_mod.LLMHubGenerate().generate(
                backend="lmstudio", prompt="hi", system_prompt="", model="타이핑한값",
                file_access=False, workspace_dir="", temperature=0.7, max_tokens=64,
                timeout_sec=10, stream_view="off", seed=0,
                lmstudio_model="qwen-7b",
            )
        self.assertEqual(captured["model"], "qwen-7b")

    def test_auto_falls_back_to_model_field(self):
        captured = {}

        class Spy:
            def generate(self, req):
                captured["model"] = req.model
                return base.LLMResponse(text="x", status="ok")

        with mock.patch.object(nodes_mod, "get_backend", return_value=Spy()):
            nodes_mod.LLMHubGenerate().generate(
                backend="lmstudio", prompt="hi", system_prompt="", model="타이핑한값",
                file_access=False, workspace_dir="", temperature=0.7, max_tokens=64,
                timeout_sec=10, stream_view="off", seed=0,
                lmstudio_model=nodes_mod.AUTO_MODEL,
            )
        self.assertEqual(captured["model"], "타이핑한값")

    def test_dropdown_does_not_affect_other_backends(self):
        captured = {}

        class Spy:
            def generate(self, req):
                captured["model"] = req.model
                return base.LLMResponse(text="x", status="ok")

        with mock.patch.object(nodes_mod, "get_backend", return_value=Spy()):
            nodes_mod.LLMHubGenerate().generate(
                backend="claude", prompt="hi", system_prompt="", model="opus",
                file_access=False, workspace_dir="", temperature=0.7, max_tokens=64,
                timeout_sec=10, stream_view="off", seed=0,
                lmstudio_model="qwen-7b",
            )
        self.assertEqual(captured["model"], "opus")

    def test_model_list_survives_dead_server(self):
        # LM Studio 가 꺼져 있어도 INPUT_TYPES 가 예외를 던지면 안 된다.
        ids = lmstudio_mod.list_model_ids(timeout_s=0.3)
        self.assertIsInstance(ids, list)


class TestReviewFixes(unittest.TestCase):
    """코드 리뷰에서 나온 결함들에 대한 회귀 방지."""

    def test_config_ttl_default_is_used(self):
        """config.json 의 lmstudio.ttl_sec 가 실제로 반영돼야 한다."""
        with MockLMStudio(script=[{"content": "안녕"}]) as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url, "ttl_sec": 77}}
            )
            backend.generate(LLMRequest("lmstudio", "", "", "안녕"))  # ttl 미지정
        self.assertEqual(server.requests[0]["ttl"], 77)

    def test_config_unload_false_is_respected(self):
        """config 에서 unload_after=false 로 꺼두면 언로드하지 않아야 한다."""
        calls = []
        with MockLMStudio(script=[{"content": "안녕"}]) as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url, "unload_after": False}}
            )
            proc_mod = importlib.import_module(f"{_PACK_NAME}.utils.proc")
            with mock.patch.object(
                proc_mod, "run_cli",
                side_effect=lambda a, **k: calls.append(a) or (0, "", "", 0.1),
            ):
                backend.generate(LLMRequest("lmstudio", "", "", "안녕"))
        self.assertEqual(calls, [])

    def test_request_overrides_config(self):
        with MockLMStudio(script=[{"content": "안녕"}]) as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url, "ttl_sec": 77}}
            )
            backend.generate(LLMRequest("lmstudio", "", "", "안녕", ttl_sec=5))
        self.assertEqual(server.requests[0]["ttl"], 5)

    def test_lmstudio_sse_streaming(self):
        """리뷰에서 지적된 미검증 경로: SSE 스트리밍."""
        emitter = RecordingEmitter()
        with MockLMStudio(script=[{"content": "안녕하세요"}], sse=True) as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url}}
            )
            resp = backend.generate(
                LLMRequest("lmstudio", "", "", "인사해", emitter=emitter)
            )
        self.assertEqual(resp.status, "ok")
        self.assertEqual(resp.text, "안녕하세요")
        self.assertGreater(len(emitter.deltas), 1, "토큰이 조각으로 오지 않았다")
        self.assertTrue(server.requests[0]["stream"])

    def test_gemini_crash_is_not_reported_ok(self):
        """텍스트가 왔더라도 result 이벤트가 없으면 ok 가 아니어야 한다."""
        partial = "\n".join([
            json.dumps({"type": "init"}),
            json.dumps({"type": "message", "role": "assistant",
                        "content": "절반만", "delta": True}),
        ])
        fake = StreamFake(code=1, stdout=partial, stderr="죽음")
        with _stream_patch(gemini_mod, fake):
            resp = gemini_mod.GeminiBackend(
                config={"defaults": {"gemini_approval_mode": "plan"}}
            ).generate(LLMRequest("gemini", "", "", "안녕", emitter=RecordingEmitter()))
        self.assertNotEqual(resp.status, "ok")
        self.assertEqual(resp.text, "절반만")  # 받은 부분은 살린다

    def test_codex_stream_keeps_body_when_output_file_empty(self):
        """-o 파일이 비어도 스트리밍으로 모은 본문을 잃지 않아야 한다."""
        codex_mod = importlib.import_module(f"{_PACK_NAME}.backends.codex")
        events = "\n".join([
            json.dumps({"type": "agent_message_delta", "delta": "안"}),
            json.dumps({"type": "agent_message_delta", "delta": "녕"}),
        ])
        fake = StreamFake(stdout=events, write_last_message="")  # -o 비어 있음
        with _stream_patch(codex_mod, fake):
            resp = codex_mod.CodexBackend().generate(
                LLMRequest("codex", "", "", "안녕", emitter=RecordingEmitter())
            )
        self.assertEqual(resp.text, "안녕")
        self.assertEqual(resp.status, "ok")

    def test_node_exception_finishes_monitor(self):
        """예외가 나도 모니터링 창이 '생성 중' 으로 멈추면 안 된다."""
        finished = {}

        class Emitter(RecordingEmitter):
            def finish(self, status="", text=None):
                finished["status"] = status

        with mock.patch.object(nodes_mod.stream, "make_emitter", return_value=Emitter()), \
             mock.patch.object(nodes_mod, "get_backend", side_effect=RuntimeError("펑")):
            result = nodes_mod.LLMHubGenerate().generate(
                backend="claude", prompt="hi", system_prompt="", model="",
                file_access=False, workspace_dir="", temperature=0.7,
                max_tokens=64, timeout_sec=10, seed=0, stream_view="plain",
            )
        self.assertEqual(len(result), 3)
        self.assertTrue(result[1].startswith("error:"))
        self.assertIn("status", finished)

    def test_new_widgets_are_appended_last(self):
        """나중에 추가한 위젯이 기존 위젯 앞에 끼면 예전 워크플로우 값이 밀린다."""
        spec = nodes_mod.LLMHubGenerate.INPUT_TYPES()
        required = list(spec["required"])
        original = ["backend", "prompt", "system_prompt", "model", "file_access",
                    "workspace_dir", "temperature", "max_tokens", "timeout_sec", "seed"]
        self.assertEqual(required[:len(original)], original)

        optional = list(spec["optional"])
        self.assertEqual(
            optional[:5], ["image", "video", "video_path", "mcp_config", "extra_args"]
        )

    def test_validate_inputs_accepts_unknown_model(self):
        """목록에 없는 모델이 저장돼 있어도 워크플로우가 실패하면 안 된다."""
        self.assertTrue(
            nodes_mod.LLMHubGenerate.VALIDATE_INPUTS(lmstudio_model="사라진모델")
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
