# -*- coding: utf-8 -*-
"""보안 리뷰 + 코드 리뷰 지적사항 회귀 방지."""

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
proc = importlib.import_module(f"{_PACK_NAME}.utils.proc")
video_io = importlib.import_module(f"{_PACK_NAME}.utils.video_io")
claude_mod = importlib.import_module(f"{_PACK_NAME}.backends.claude_code")
codex_mod = importlib.import_module(f"{_PACK_NAME}.backends.codex")
gemini_mod = importlib.import_module(f"{_PACK_NAME}.backends.gemini")
LLMRequest = base.LLMRequest

sys.path.insert(0, os.path.join(_PACK_ROOT, "tests"))
from test_cli_backends import FakeCli, _patch  # noqa: E402

FIXTURES = os.path.join(_PACK_ROOT, "tests", "fixtures")


class TestExtraArgsSandbox(unittest.TestCase):
    """보안 HIGH: extra_args 로 읽기 전용 잠금을 풀 수 없어야 한다."""

    def setUp(self):
        # config 캐시를 비워 allow_unsafe_extra_args 기본(off)을 확실히 한다.
        cfg = importlib.import_module(f"{_PACK_NAME}.utils.config")
        cfg.load_config(force_reload=True)

    def test_claude_bash_flag_blocked(self):
        fake = FakeCli(stdout=json.dumps({"is_error": False, "result": "x"}))
        with _patch(claude_mod, fake):
            resp = claude_mod.ClaudeCodeBackend(config={}).generate(
                LLMRequest("claude", "", "", "hi",
                           extra_args="--dangerously-skip-permissions --allowedTools Bash")
            )
        self.assertNotIn("--dangerously-skip-permissions", fake.args)
        self.assertNotIn("Bash", " ".join(fake.args))
        self.assertIn("blocked flags", resp.raw_debug)

    def test_codex_sandbox_flag_blocked(self):
        fake = FakeCli(write_last_message="x")
        with _patch(codex_mod, fake):
            codex_mod.CodexBackend().generate(
                LLMRequest("codex", "", "", "hi", extra_args="-s danger-full-access")
            )
        # -s read-only 는 남고, 사용자가 넣은 -s danger-full-access 는 걸러진다
        self.assertNotIn("danger-full-access", " ".join(fake.args))

    def test_gemini_yolo_blocked(self):
        fake = FakeCli(stdout=json.dumps({"response": "x"}))
        with _patch(gemini_mod, fake):
            gemini_mod.GeminiBackend(
                config={"defaults": {"gemini_approval_mode": "plan"}}
            ).generate(LLMRequest("gemini", "", "", "hi", extra_args="--yolo"))
        self.assertNotIn("--yolo", fake.args)

    def test_benign_extra_args_pass_through(self):
        fake = FakeCli(stdout=json.dumps({"is_error": False, "result": "x"}))
        with _patch(claude_mod, fake):
            claude_mod.ClaudeCodeBackend(config={}).generate(
                LLMRequest("claude", "", "", "hi", extra_args="--effort high")
            )
        self.assertIn("--effort", fake.args)
        self.assertIn("high", fake.args)

    def test_opt_in_allows_unsafe(self):
        fake = FakeCli(stdout=json.dumps({"is_error": False, "result": "x"}))
        cfg = importlib.import_module(f"{_PACK_NAME}.utils.config")
        with mock.patch.object(cfg, "load_config",
                               return_value={"allow_unsafe_extra_args": True}):
            with _patch(claude_mod, fake):
                claude_mod.ClaudeCodeBackend(config={}).generate(
                    LLMRequest("claude", "", "", "hi", extra_args="--allowedTools Bash")
                )
        self.assertIn("Bash", " ".join(fake.args))


class TestErrorMisclassification(unittest.TestCase):
    """모델 답변에 '429' 등이 있어도 오류로 오분류하지 않아야 한다."""

    def test_claude_answer_mentioning_429(self):
        payload = json.dumps({
            "is_error": False,
            "result": "HTTP 429 는 too many requests, 즉 rate limit 초과를 뜻합니다.",
        })
        fake = FakeCli(code=0, stdout=payload)
        with _patch(claude_mod, fake):
            resp = claude_mod.ClaudeCodeBackend(config={}).generate(
                LLMRequest("claude", "", "", "429가 뭐야?")
            )
        self.assertEqual(resp.status, "ok")
        self.assertIn("429", resp.text)

    def test_claude_answer_mentioning_unauthorized(self):
        payload = json.dumps({
            "is_error": False,
            "result": "401 Unauthorized 는 인증 실패를 뜻합니다.",
        })
        fake = FakeCli(code=0, stdout=payload)
        with _patch(claude_mod, fake):
            resp = claude_mod.ClaudeCodeBackend(config={}).generate(
                LLMRequest("claude", "", "", "401이 뭐야?")
            )
        self.assertEqual(resp.status, "ok")

    def test_real_login_error_still_caught(self):
        fake = FakeCli(code=1, stderr="Please log in to continue")
        with _patch(claude_mod, fake):
            resp = claude_mod.ClaudeCodeBackend(config={}).generate(
                LLMRequest("claude", "", "", "hi")
            )
        self.assertIn("login required", resp.status)

    def test_codex_answer_mentioning_rate_limit(self):
        fake = FakeCli(code=0, write_last_message="rate limit 은 요청 한도입니다.")
        with _patch(codex_mod, fake):
            resp = codex_mod.CodexBackend().generate(
                LLMRequest("codex", "", "", "rate limit 설명해")
            )
        self.assertEqual(resp.status, "ok")


class TestProcHardening(unittest.TestCase):
    def test_posix_shlex_strips_quotes(self):
        # 리눅스/맥에서 따옴표가 argv 에 남지 않아야 한다.
        if sys.platform == "win32":
            self.skipTest("posix 전용 검증")
        out = proc.parse_extra_args('--flag "two words"')
        self.assertEqual(out, ["--flag", "two words"])

    def test_screen_extra_args_blocks_dangerous(self):
        cfg = importlib.import_module(f"{_PACK_NAME}.utils.config")
        with mock.patch.object(cfg, "load_config", return_value={}):
            safe, rejected = proc.screen_extra_args(
                ["--effort", "high", "--yolo", "-s", "danger-full-access"]
            )
        self.assertIn("--effort", safe)
        self.assertIn("high", safe)
        self.assertIn("--yolo", rejected)
        self.assertIn("-s", rejected)


class TestStageMedia(unittest.TestCase):
    def test_does_not_overwrite_user_file(self):
        import tempfile

        ws = tempfile.mkdtemp()
        # 사용자 원본 파일 (같은 이름)
        user_file = os.path.join(ws, "clip.png")
        with open(user_file, "wb") as fh:
            fh.write(b"USER ORIGINAL")
        # 다른 폴더의 동명 파일을 staging
        other = tempfile.mkdtemp()
        src = os.path.join(other, "clip.png")
        with open(src, "wb") as fh:
            fh.write(b"STAGED DIFFERENT")

        staged = base.stage_media([src], ws)
        # 사용자 원본은 그대로여야 한다
        with open(user_file, "rb") as fh:
            self.assertEqual(fh.read(), b"USER ORIGINAL")
        # staged 는 전용 하위 폴더에 들어간다
        self.assertTrue(staged[0].startswith("_llmhub_media"))


class TestVideoFrameCollection(unittest.TestCase):
    def setUp(self):
        if not os.path.isfile(os.path.join(FIXTURES, "sample_video.mp4")):
            self.skipTest("샘플 비디오 없음")
        if not video_io.find_extractor():
            self.skipTest("추출기 없음")

    def test_clears_stale_frames(self):
        import tempfile

        out = tempfile.mkdtemp()
        # 다른 영상의 스테일 프레임을 미리 심는다
        stale = os.path.join(out, "llmhub_frame_99.png")
        with open(stale, "wb") as fh:
            fh.write(b"STALE")
        video_io.extract_frames(
            os.path.join(FIXTURES, "sample_video.mp4"), 3, out
        )
        self.assertFalse(os.path.exists(stale), "스테일 프레임이 남았다")


if __name__ == "__main__":
    unittest.main(verbosity=2)
