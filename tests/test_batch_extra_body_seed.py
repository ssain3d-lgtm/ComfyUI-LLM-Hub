# -*- coding: utf-8 -*-
"""이미지 배치 / extra_body / seed / usage 검증.

유사 노드와 비교하다 나온 네 가지 구멍을 막은 것이라, 각각 "왜 이게 없으면
문제인가" 를 테스트 이름에 남긴다.

  batch_mode : 배치 40장을 물리면 40장이 한 요청에 다 들어가고 답이 하나만
               나왔다. 데이터셋 캡션(장당 한 줄)이 아예 불가능했다.
  extra_body : HTTP 백엔드에는 탈출구가 없었다. temperature/max_tokens 말고는
               아무것도 못 넘기고, extra_args 는 debug 한 줄 남기고 버려졌다.
  seed       : ComfyUI 에서 seed 는 "돌리면 같은 결과" 라는 약속인데, 이 노드는
               그 모양의 손잡이만 달아두고 서버로 보내지 않았다.
  usage      : 100프롬프트에 $19.6 이라고 README 가 경고까지 하면서, 정작 방금
               쓴 양은 어디에도 안 보였다.

ComfyUI / LM Studio / CLI 로그인 없이 돌아간다.
"""

from __future__ import annotations

import importlib
import io
import os
import sys
import unittest
from unittest import mock

_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_PACK_ROOT))
_PACK_NAME = os.path.basename(_PACK_ROOT)

base = importlib.import_module(f"{_PACK_NAME}.backends.base")
lmstudio_mod = importlib.import_module(f"{_PACK_NAME}.backends.lmstudio")
nodes_mod = importlib.import_module(f"{_PACK_NAME}.nodes")
LLMRequest = base.LLMRequest

sys.path.insert(0, os.path.join(_PACK_ROOT, "tests"))
from mock_lmstudio import MockLMStudio  # noqa: E402


class RecordingBackend:
    """호출마다 받은 image_paths 를 기록하고, 장별로 다른 답을 돌려준다."""

    def __init__(self, fail_on=()):
        self.calls = []
        self.fail_on = set(fail_on)

    def generate(self, req):
        index = len(self.calls)
        self.calls.append(list(req.image_paths))
        if index in self.fail_on:
            return base.LLMResponse(text="", status="error: boom")
        return base.LLMResponse(text=f"caption{index}", status="ok")


def run_node(backend_impl, images=0, **kwargs):
    """노드를 한 번 돌린다. images 개수만큼 가짜 PNG 경로를 만들어 넣는다.

    save_images 를 가로채는 이유: 여기서 검증하려는 것은 PNG 저장이 아니라
    "몇 번 호출되는가" 다. numpy/PIL 없이도 돌아야 한다.
    """
    paths = [f"/fake/img_{i:02d}.png" for i in range(images)]
    args = dict(
        backend="lmstudio", prompt="describe", system_prompt="", model="",
        file_access=False, workspace_dir="", temperature=0.7, max_tokens=64,
        timeout_sec=10, seed=0, stream_view="off",
    )
    args.update(kwargs)
    if images:
        args["image"] = object()  # None 이 아니기만 하면 된다

    with mock.patch.object(nodes_mod, "get_backend", return_value=backend_impl), \
            mock.patch.object(nodes_mod.image_io, "save_images", return_value=paths):
        return nodes_mod.LLMHubGenerate().generate(**args)


class TestBatchMode(unittest.TestCase):

    def test_all_in_one_still_sends_every_image_in_one_call(self):
        """기존 동작이 바뀌면 안 된다. 기본값은 예전 그대로다."""
        spy = RecordingBackend()
        out = run_node(spy, images=3)
        self.assertEqual(len(spy.calls), 1)
        self.assertEqual(len(spy.calls[0]), 3)
        self.assertEqual(out["result"][1], "ok")
        self.assertEqual(out["result"][0], "caption0")

    def test_one_per_image_calls_once_per_image(self):
        spy = RecordingBackend()
        run_node(spy, images=3, batch_mode=nodes_mod.BATCH_PER_IMAGE)
        self.assertEqual(len(spy.calls), 3)
        for call in spy.calls:
            self.assertEqual(len(call), 1, "장별 호출인데 이미지가 여러 장 들어갔다")

    def test_results_keep_the_batch_order(self):
        """n번째 캡션이 n번째 그림에 붙어야 한다. 어긋나면 데이터셋이 망가진다."""
        spy = RecordingBackend()
        out = run_node(spy, images=3, batch_mode=nodes_mod.BATCH_PER_IMAGE)
        pieces = out["result"][0].split(nodes_mod.BATCH_SEPARATOR)
        self.assertEqual(pieces, ["caption0", "caption1", "caption2"])
        self.assertEqual(
            [call[0] for call in spy.calls],
            ["/fake/img_00.png", "/fake/img_01.png", "/fake/img_02.png"],
        )

    def test_a_failed_image_keeps_its_slot(self):
        """실패한 장을 빼버리면 그 뒤 캡션이 전부 한 칸씩 밀린다."""
        spy = RecordingBackend(fail_on=(1,))
        out = run_node(spy, images=3, batch_mode=nodes_mod.BATCH_PER_IMAGE)
        pieces = out["result"][0].split(nodes_mod.BATCH_SEPARATOR)
        self.assertEqual(pieces, ["caption0", "", "caption2"])

    def test_failures_are_not_reported_as_ok(self):
        spy = RecordingBackend(fail_on=(1,))
        out = run_node(spy, images=3, batch_mode=nodes_mod.BATCH_PER_IMAGE)
        status = out["result"][1]
        self.assertTrue(status.startswith("error:"), status)
        self.assertIn("1/3", status)

    def test_all_ok_says_how_many(self):
        out = run_node(RecordingBackend(), images=2,
                       batch_mode=nodes_mod.BATCH_PER_IMAGE)
        self.assertEqual(out["result"][1], "ok - 2 images")

    def test_one_image_is_not_split(self):
        """한 장이면 나눌 것이 없다. 구분자도 붙으면 안 된다."""
        spy = RecordingBackend()
        out = run_node(spy, images=1, batch_mode=nodes_mod.BATCH_PER_IMAGE)
        self.assertEqual(len(spy.calls), 1)
        self.assertEqual(out["result"][0], "caption0")
        self.assertEqual(out["result"][1], "ok")

    def test_video_disables_splitting(self):
        """프레임들은 한 영상의 조각이라 따로 물어보면 의미가 무너진다."""
        spy = RecordingBackend()
        with mock.patch.object(
            nodes_mod.video_io, "resolve_video", return_value=("/fake/clip.mp4", "")
        ), mock.patch.object(nodes_mod.image_io, "get_tmp_dir", return_value="/fake"):
            out = run_node(spy, images=3, batch_mode=nodes_mod.BATCH_PER_IMAGE,
                           video_path="/fake/clip.mp4")
        self.assertEqual(len(spy.calls), 1, "비디오가 있는데 장별로 쪼갰다")
        self.assertEqual(out["result"][1], "ok")

    def test_stop_between_images_pads_the_rest(self):
        """40장짜리 배치에서 Stop 이 안 들으면 멈출 방법이 없다."""
        spy = RecordingBackend()
        with mock.patch.object(
            nodes_mod.cancel, "is_stopped", side_effect=[False, True, True, True]
        ):
            out = run_node(spy, images=3, batch_mode=nodes_mod.BATCH_PER_IMAGE)
        self.assertEqual(len(spy.calls), 1)
        self.assertTrue(out["result"][1].startswith("stopped"), out["result"][1])
        # 자리는 그대로 3개다 (뒤쪽은 빈 문자열).
        self.assertEqual(
            out["result"][0].split(nodes_mod.BATCH_SEPARATOR), ["caption0", "", ""]
        )

    def test_debug_says_how_many_calls_were_made(self):
        """N배 요금이 나가는 모드다. 몇 번 불렀는지는 보여야 한다."""
        out = run_node(RecordingBackend(), images=3,
                       batch_mode=nodes_mod.BATCH_PER_IMAGE)
        self.assertIn("3 images -> 3 calls", out["result"][2])


class TestExtraBodyParsing(unittest.TestCase):

    def test_empty_is_not_an_error(self):
        parsed, error = base.parse_extra_body("   ")
        self.assertEqual(parsed, {})
        self.assertEqual(error, "")

    def test_broken_json_is_an_error(self):
        _parsed, error = base.parse_extra_body('{"top_p": }')
        self.assertTrue(error.startswith("error:"), error)

    def test_a_json_list_is_rejected(self):
        """payload 에 합칠 물건이라 반드시 객체여야 한다."""
        _parsed, error = base.parse_extra_body('[1, 2]')
        self.assertTrue(error.startswith("error:"), error)
        self.assertIn("JSON object", error)

    def test_object_is_parsed(self):
        parsed, error = base.parse_extra_body('{"top_p": 0.9}')
        self.assertEqual(parsed, {"top_p": 0.9})
        self.assertEqual(error, "")


class TestExtraBodyMerge(unittest.TestCase):

    def test_user_fields_win(self):
        payload = {"temperature": 0.7}
        notes = base.merge_extra_body(payload, {"temperature": 0.1, "top_p": 0.9})
        self.assertEqual(payload["temperature"], 0.1)
        self.assertEqual(payload["top_p"], 0.9)
        self.assertTrue(any("applied" in note for note in notes))

    def test_skeleton_keys_are_locked(self):
        """messages 를 덮어쓰게 두면 사용자가 만든 대화가 통째로 사라진다."""
        payload = {"messages": [{"role": "user", "content": "hi"}], "stream": False}
        notes = base.merge_extra_body(payload, {"messages": [], "stream": True})
        self.assertEqual(payload["messages"], [{"role": "user", "content": "hi"}])
        self.assertIs(payload["stream"], False)
        self.assertTrue(any("ignored" in note for note in notes))

    def test_tools_are_locked_only_while_the_tool_loop_runs(self):
        free = {}
        base.merge_extra_body(free, {"tools": ["x"]}, file_access=False)
        self.assertEqual(free["tools"], ["x"])

        locked = {"tools": ["real"]}
        base.merge_extra_body(locked, {"tools": ["x"]}, file_access=True)
        self.assertEqual(locked["tools"], ["real"])

    def test_nothing_to_merge_says_nothing(self):
        self.assertEqual(base.merge_extra_body({}, {}), [])


class TestExtraBodyThroughTheNode(unittest.TestCase):

    def test_broken_json_stops_before_any_request(self):
        """조용히 무시하면 extra_args 와 같은 함정을 한 번 더 파는 셈이다."""
        spy = RecordingBackend()
        out = run_node(spy, extra_body='{"top_p": }')
        self.assertEqual(len(spy.calls), 0, "잘못된 JSON 인데 요청을 보냈다")
        self.assertTrue(out["result"][1].startswith("error:"), out["result"][1])
        self.assertIn("extra_body", out["result"][1])

    def test_broken_json_stops_before_the_images_are_written(self):
        """40장짜리 배치를 다 써놓고 JSON 오타로 끝내면 그 쓰기가 전부 헛일이다."""
        saver = mock.Mock(return_value=["/fake/a.png"])
        with mock.patch.object(nodes_mod, "get_backend", return_value=RecordingBackend()), \
                mock.patch.object(nodes_mod.image_io, "save_images", saver):
            out = nodes_mod.LLMHubGenerate().generate(
                backend="lmstudio", prompt="x", system_prompt="", model="",
                file_access=False, workspace_dir="", temperature=0.7, max_tokens=64,
                timeout_sec=10, seed=0, stream_view="off",
                image=object(), extra_body='{"top_p": }',
            )
        self.assertEqual(saver.call_count, 0, "잘못된 JSON 인데 PNG 를 저장했다")
        self.assertTrue(out["result"][1].startswith("error:"))

    def test_valid_json_reaches_the_request(self):
        seen = {}

        class Spy:
            def generate(self, req):
                seen["extra_body"] = req.extra_body
                return base.LLMResponse(text="x", status="ok")

        run_node(Spy(), extra_body='{"top_p": 0.9}')
        self.assertEqual(seen["extra_body"], {"top_p": 0.9})

    def test_it_reaches_the_actual_payload(self):
        with MockLMStudio(script=[{"content": "ok"}]) as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url}}
            )
            backend.generate(LLMRequest(
                "lmstudio", "", "", "hi",
                extra_body={"top_p": 0.9, "response_format": {"type": "json_object"}},
            ))
        self.assertEqual(server.requests[0]["top_p"], 0.9)
        self.assertEqual(server.requests[0]["response_format"], {"type": "json_object"})

    def test_cli_backends_say_they_ignore_it(self):
        req = LLMRequest("claude", "", "", "hi", extra_body={"top_p": 0.9})
        note = base.extra_body_ignored_note("claude", req)
        self.assertIn("ignored", note)
        self.assertIn("extra_args", note, "대안을 알려주지 않으면 안내가 아니다")

    def test_nothing_set_means_no_note(self):
        req = LLMRequest("claude", "", "", "hi")
        self.assertEqual(base.extra_body_ignored_note("claude", req), "")

    def test_every_cli_backend_is_wired(self):
        """헬퍼만 있고 부르는 곳이 없으면 아무 소용이 없다."""
        for name in ("claude_code", "codex", "gemini"):
            path = os.path.join(_PACK_ROOT, "backends", f"{name}.py")
            with io.open(path, encoding="utf-8") as fh:
                source = fh.read()
            self.assertIn(
                "extra_body_ignored_note(", source, f"{name}.py 가 안내를 안 만든다"
            )


class TestSeed(unittest.TestCase):

    def test_zero_is_not_sent(self):
        """기본 설치의 요청 내용이 예전과 똑같아야 한다."""
        with MockLMStudio(script=[{"content": "ok"}]) as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url}}
            )
            backend.generate(LLMRequest("lmstudio", "", "", "hi", seed=0))
        self.assertNotIn("seed", server.requests[0])

    def test_a_set_seed_is_sent(self):
        with MockLMStudio(script=[{"content": "ok"}]) as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url}}
            )
            backend.generate(LLMRequest("lmstudio", "", "", "hi", seed=1234))
        self.assertEqual(server.requests[0]["seed"], 1234)

    def test_a_server_that_rejects_seed_still_answers(self):
        """시드 하나 때문에 생성 전체가 실패하면 안 된다."""
        with MockLMStudio(script=[{"content": "ok"}], reject_seed=True) as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url}}
            )
            response = backend.generate(LLMRequest("lmstudio", "", "", "hi", seed=7))
        self.assertEqual(response.status, "ok")
        self.assertEqual(response.text, "ok")
        self.assertIn("seed", server.requests[0])
        self.assertNotIn("seed", server.requests[1], "재시도에도 시드를 또 보냈다")
        self.assertIn("rejected 'seed'", response.raw_debug)

    def test_a_corrupted_seed_slot_does_not_kill_the_node(self):
        """위젯 순서가 밀린 워크플로우에서 seed 자리에 문자열이 오는 경우.

        int("(auto)") 가 던지는 ValueError 로 노드가 통째로 죽으면,
        .strip() 에서 죽던 사고를 숫자 쪽에서 되풀이하는 것이다.
        """
        seen = {}

        class Spy:
            def generate(self, req):
                seen["seed"] = req.seed
                return base.LLMResponse(text="x", status="ok")

        out = run_node(Spy(), seed="(auto)")
        self.assertEqual(out["result"][1], "ok", out["result"][2][:200])
        self.assertEqual(seen["seed"], 0)

    def test_the_node_passes_the_seed_down(self):
        seen = {}

        class Spy:
            def generate(self, req):
                seen["seed"] = req.seed
                return base.LLMResponse(text="x", status="ok")

        run_node(Spy(), seed=99)
        self.assertEqual(seen["seed"], 99)


class TestUsageLine(unittest.TestCase):

    def test_openai_style(self):
        line = base.format_usage(
            {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
        )
        self.assertEqual(line, "usage: prompt=10 completion=20 total=30")

    def test_claude_style_with_cost(self):
        line = base.format_usage(
            {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 8},
            cost_usd=0.0123,
        )
        self.assertIn("prompt=100", line)
        self.assertIn("completion=50", line)
        self.assertIn("total=150", line)  # total 을 안 주면 직접 더한다
        self.assertIn("cached=8", line)
        self.assertIn("cost=$0.0123", line)

    def test_unknown_shape_says_nothing(self):
        """없는 항목을 0 으로 적으면 '0 토큰 썼다' 는 거짓말이 된다."""
        self.assertEqual(base.format_usage({"weird": 1}), "")
        self.assertEqual(base.format_usage(None), "")
        self.assertEqual(base.format_usage("not a dict"), "")

    def test_cost_alone_is_enough(self):
        self.assertEqual(base.format_usage(None, cost_usd=1.5), "usage: cost=$1.5000")

    def test_it_shows_up_in_debug(self):
        usage = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
        with MockLMStudio(script=[{"content": "ok"}], usage=usage) as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url}}
            )
            response = backend.generate(LLMRequest("lmstudio", "", "", "hi"))
        self.assertIn("usage: prompt=7 completion=3 total=10", response.raw_debug)

    def test_a_server_without_usage_is_fine(self):
        with MockLMStudio(script=[{"content": "ok"}]) as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url}}
            )
            response = backend.generate(LLMRequest("lmstudio", "", "", "hi"))
        self.assertEqual(response.status, "ok")
        self.assertNotIn("usage:", response.raw_debug)


if __name__ == "__main__":
    unittest.main()
