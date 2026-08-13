# -*- coding: utf-8 -*-
"""비디오 입력 검증.

프레임 추출기(ffmpeg/cv2)가 없는 환경에서는 추출 관련 테스트를 건너뛰고,
경로 해석과 백엔드별 분기(네이티브 vs 프레임 변환)는 항상 검증한다.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_PACK_ROOT))
_PACK_NAME = os.path.basename(_PACK_ROOT)

base = importlib.import_module(f"{_PACK_NAME}.backends.base")
video_io = importlib.import_module(f"{_PACK_NAME}.utils.video_io")
claude_mod = importlib.import_module(f"{_PACK_NAME}.backends.claude_code")
codex_mod = importlib.import_module(f"{_PACK_NAME}.backends.codex")
gemini_mod = importlib.import_module(f"{_PACK_NAME}.backends.gemini")
lmstudio_mod = importlib.import_module(f"{_PACK_NAME}.backends.lmstudio")
nodes_mod = importlib.import_module(f"{_PACK_NAME}.nodes")
LLMRequest = base.LLMRequest

sys.path.insert(0, os.path.join(_PACK_ROOT, "tests"))
from mock_lmstudio import MockLMStudio  # noqa: E402
from test_cli_backends import FakeCli, _patch  # noqa: E402

FIXTURES = os.path.join(_PACK_ROOT, "tests", "fixtures")
SAMPLE_VIDEO = os.path.join(FIXTURES, "sample_video.mp4")

HAS_VIDEO_FIXTURE = os.path.isfile(SAMPLE_VIDEO)
HAS_EXTRACTOR = bool(video_io.find_extractor())


class TestVideoResolve(unittest.TestCase):
    def test_video_extension_detection(self):
        self.assertTrue(video_io.is_video("a.mp4"))
        self.assertTrue(video_io.is_video("A.MOV"))
        self.assertTrue(video_io.is_video("clip.webm"))
        self.assertFalse(video_io.is_video("a.png"))
        self.assertFalse(video_io.is_video("a.txt"))

    def test_resolve_from_path_string(self):
        if not HAS_VIDEO_FIXTURE:
            self.skipTest("샘플 비디오 없음")
        path, note = video_io.resolve_video(None, SAMPLE_VIDEO)
        self.assertEqual(path, os.path.abspath(SAMPLE_VIDEO))
        self.assertEqual(note, "")

    def test_resolve_missing_file_reports_reason(self):
        path, note = video_io.resolve_video(None, "/no/such/clip.mp4")
        self.assertEqual(path, "")
        self.assertIn("file not found", note)

    def test_resolve_nothing_is_silent(self):
        path, note = video_io.resolve_video(None, "")
        self.assertEqual(path, "")
        self.assertEqual(note, "")

    def test_resolve_from_dict(self):
        if not HAS_VIDEO_FIXTURE:
            self.skipTest("샘플 비디오 없음")
        path, _note = video_io.resolve_video({"fullpath": SAMPLE_VIDEO}, "")
        self.assertEqual(path, os.path.abspath(SAMPLE_VIDEO))

    def test_resolve_from_comfyui_video_object(self):
        """ComfyUI VIDEO 타입(save_to 를 가진 객체) 처리."""
        if not HAS_VIDEO_FIXTURE:
            self.skipTest("샘플 비디오 없음")

        class FakeComfyVideo:
            def save_to(self, dest, **_kwargs):
                import shutil

                shutil.copyfile(SAMPLE_VIDEO, dest)

        tmp = tempfile.mkdtemp()
        path, note = video_io.resolve_video(FakeComfyVideo(), "", tmp)
        self.assertTrue(os.path.isfile(path), note)
        self.assertTrue(path.endswith(".mp4"))

    def test_unsupported_object_explains(self):
        path, note = video_io.resolve_video(object(), "")
        self.assertEqual(path, "")
        self.assertIn("video_path", note)


class TestFrameExtraction(unittest.TestCase):
    def setUp(self):
        if not HAS_VIDEO_FIXTURE:
            self.skipTest("샘플 비디오 없음")
        if not HAS_EXTRACTOR:
            self.skipTest("ffmpeg/cv2 가 없어 프레임 추출을 검증할 수 없음")

    def test_extracts_requested_frame_count(self):
        out = tempfile.mkdtemp()
        frames, note = video_io.extract_frames(SAMPLE_VIDEO, max_frames=4, out_dir=out)
        self.assertEqual(len(frames), 4, note)
        for path in frames:
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")

    def test_single_frame(self):
        out = tempfile.mkdtemp()
        frames, _note = video_io.extract_frames(SAMPLE_VIDEO, max_frames=1, out_dir=out)
        self.assertEqual(len(frames), 1)

    def test_duration_probe(self):
        # 5초짜리 픽스처. ffprobe 가 없으면 ffmpeg stderr 파싱 경로를 탄다.
        duration = video_io._probe_duration(SAMPLE_VIDEO)
        self.assertGreater(duration, 4.0)
        self.assertLess(duration, 6.0)

    def test_missing_file_reports_reason(self):
        frames, note = video_io.extract_frames("/no/such/clip.mp4", 4)
        self.assertEqual(frames, [])
        self.assertIn("file not found", note)


class TestNoExtractorGuidance(unittest.TestCase):
    def test_message_tells_user_to_install_ffmpeg(self):
        if not HAS_VIDEO_FIXTURE:
            self.skipTest("샘플 비디오 없음")
        with mock.patch.object(video_io, "find_extractor", return_value=""):
            frames, note = video_io.extract_frames(SAMPLE_VIDEO, 4, tempfile.mkdtemp())
        self.assertEqual(frames, [])
        self.assertIn("ffmpeg", note)


FAKE_FRAMES = ["/tmp/llmhub_frame_00.png", "/tmp/llmhub_frame_01.png"]


def _fake_extract(_path, _max_frames=8, _out_dir=""):
    return list(FAKE_FRAMES), "video: (테스트) 2장 추출"


class TestBackendVideoRouting(unittest.TestCase):
    """비디오를 네이티브로 받는 백엔드와 프레임으로 바꾸는 백엔드를 구분한다."""

    def _req(self, backend, **kwargs):
        return LLMRequest(backend, "", "", "이 영상 설명해", video_paths=[SAMPLE_VIDEO], **kwargs)

    def test_gemini_passes_video_natively(self):
        fake = FakeCli(stdout=json.dumps({"response": "영상 설명"}))
        with _patch(gemini_mod, fake), mock.patch.object(
            video_io, "extract_frames", side_effect=AssertionError("gemini 는 프레임 추출을 하면 안 된다")
        ):
            backend = gemini_mod.GeminiBackend(
                config={"defaults": {"gemini_model": "gemini-2.5-flash", "gemini_approval_mode": "plan"}}
            )
            resp = backend.generate(self._req("gemini"))
        self.assertEqual(resp.status, "ok")
        # Windows 는 역슬래시를 낸다. 그게 맞는 동작이라 단정 쪽을 정규화한다.
        self.assertIn("@_llmhub_media/sample_video.mp4", fake.stdin.replace("\\", "/"))
        self.assertIn("natively", resp.raw_debug)

    def test_claude_converts_video_to_frames(self):
        fake = FakeCli(stdout=json.dumps({"is_error": False, "result": "영상 설명"}))
        with _patch(claude_mod, fake), mock.patch.object(
            base_video_module(), "extract_frames", side_effect=_fake_extract
        ):
            resp = claude_mod.ClaudeCodeBackend(config={}).generate(self._req("claude"))
        self.assertEqual(resp.status, "ok")
        self.assertIn("no native video support", resp.raw_debug)

    def test_codex_converts_video_to_frames(self):
        fake = FakeCli(write_last_message="영상 설명")
        with _patch(codex_mod, fake), mock.patch.object(
            base_video_module(), "extract_frames", side_effect=_fake_extract
        ):
            resp = codex_mod.CodexBackend().generate(self._req("codex"))
        self.assertEqual(resp.status, "ok")
        self.assertIn("no native video support", resp.raw_debug)

    def test_lmstudio_converts_video_to_frames(self):
        with MockLMStudio(script=[{"content": "영상 설명"}]) as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url}}
            )
            with mock.patch.object(
                base_video_module(), "extract_frames", side_effect=_fake_extract
            ):
                resp = backend.generate(self._req("lmstudio"))
        self.assertEqual(resp.status, "ok")
        self.assertIn("no native video support", resp.raw_debug)

    def test_video_max_frames_is_forwarded(self):
        seen = {}

        def capture(path, max_frames=8, out_dir=""):
            seen["max_frames"] = max_frames
            return [], "video: (테스트)"

        fake = FakeCli(stdout=json.dumps({"is_error": False, "result": "x"}))
        with _patch(claude_mod, fake), mock.patch.object(
            base_video_module(), "extract_frames", side_effect=capture
        ):
            claude_mod.ClaudeCodeBackend(config={}).generate(
                LLMRequest(
                    "claude", "", "", "설명해",
                    video_paths=[SAMPLE_VIDEO], video_max_frames=3,
                )
            )
        self.assertEqual(seen["max_frames"], 3)


def base_video_module():
    """base.frames_for_unsupported_video 가 실제로 호출하는 모듈."""
    return importlib.import_module(f"{_PACK_NAME}.utils.video_io")


class TestNodeVideoInputs(unittest.TestCase):
    def test_video_inputs_declared(self):
        spec = nodes_mod.LLMHubGenerate.INPUT_TYPES()
        self.assertIn("video", spec["optional"])
        self.assertIn("video_path", spec["optional"])
        self.assertEqual(spec["optional"]["video"][0], "VIDEO")
        self.assertIn("video_max_frames", spec["required"])

    def test_node_reports_missing_video_without_crashing(self):
        node = nodes_mod.LLMHubGenerate()
        result = node.generate(
            backend="claude", prompt="설명해", system_prompt="", model="",
            file_access=False, workspace_dir="", temperature=0.7,
            max_tokens=128, timeout_sec=10, video_max_frames=4, seed=0,
            video_path="/no/such/clip.mp4",
        )
        self.assertEqual(len(result["result"]), 3)
        self.assertIn("file not found", result["result"][2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
