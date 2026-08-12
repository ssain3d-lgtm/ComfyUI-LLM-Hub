# -*- coding: utf-8 -*-
"""CLI 백엔드의 커맨드 조립 / 출력 파싱 검증 (DESIGN §8.2~§8.4).

로그인이나 네트워크 없이 돌아간다. run_cli 를 가로채 실제 프로세스는 띄우지 않고,
어떤 인자와 stdin 으로 호출됐는지와 응답 파싱 결과만 확인한다.
프로세스 실행 자체는 test_offline.py 의 TestProcHelpers 가 따로 검증한다.
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
claude_mod = importlib.import_module(f"{_PACK_NAME}.backends.claude_code")
codex_mod = importlib.import_module(f"{_PACK_NAME}.backends.codex")
gemini_mod = importlib.import_module(f"{_PACK_NAME}.backends.gemini")
LLMRequest = base.LLMRequest

FIXTURES = os.path.join(_PACK_ROOT, "tests", "fixtures")


class FakeCli:
    """run_cli 를 대체해 호출 인자를 기록하고 정해진 출력을 돌려준다."""

    def __init__(self, code=0, stdout="", stderr="", write_last_message=None):
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        self.write_last_message = write_last_message
        self.args = None
        self.cwd = None
        self.stdin = None

    def __call__(self, args, *, cwd, stdin_text=None, timeout_s=300, node_id=None):
        self.args = list(args)
        self.cwd = cwd
        self.stdin = stdin_text
        # codex 의 -o 파일 경로에 최종 메시지를 써 주는 흉내
        if self.write_last_message is not None and "-o" in args:
            path = args[args.index("-o") + 1]
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.write_last_message)
        return self.code, self.stdout, self.stderr, 1.0

    def flag_value(self, flag):
        """--flag 다음 값을 돌려준다 (없으면 None)."""
        if self.args and flag in self.args:
            index = self.args.index(flag)
            if index + 1 < len(self.args):
                return self.args[index + 1]
        return None

    def all_flag_values(self, flag):
        out = []
        for i, item in enumerate(self.args or []):
            if item == flag and i + 1 < len(self.args):
                out.append(self.args[i + 1])
        return out


def _patch(module, fake):
    return mock.patch.multiple(
        module, run_cli=fake, resolve_cli=lambda name: f"/usr/bin/{name}"
    )


CLAUDE_OK = json.dumps(
    {
        "is_error": False,
        "result": "안녕",
        "num_turns": 1,
        "total_cost_usd": 0.01,
        "usage": {"input_tokens": 2, "output_tokens": 5},
        "subtype": "success",
    }
)


class TestClaudeBackend(unittest.TestCase):
    def test_base_command_and_stdin(self):
        fake = FakeCli(stdout=CLAUDE_OK)
        with _patch(claude_mod, fake):
            resp = claude_mod.ClaudeCodeBackend().generate(
                LLMRequest("claude", "", "", "안녕이라고만 답해")
            )
        self.assertEqual(resp.status, "ok")
        self.assertEqual(resp.text, "안녕")
        self.assertIn("-p", fake.args)
        self.assertEqual(fake.flag_value("--output-format"), "json")
        # 프롬프트는 argv 가 아니라 stdin 으로 간다 (DESIGN §8.2).
        self.assertEqual(fake.stdin, "안녕이라고만 답해")
        self.assertNotIn("안녕이라고만 답해", fake.args)

    def test_tools_disabled_without_file_access(self):
        fake = FakeCli(stdout=CLAUDE_OK)
        with _patch(claude_mod, fake):
            claude_mod.ClaudeCodeBackend().generate(LLMRequest("claude", "", "", "hi"))
        # 실측: --tools "" 로 내장 툴 전체를 끈다.
        self.assertEqual(fake.flag_value("--tools"), "")
        self.assertNotIn("--allowedTools", fake.args)

    def test_read_only_tools_with_file_access(self):
        fake = FakeCli(stdout=CLAUDE_OK)
        with _patch(claude_mod, fake):
            claude_mod.ClaudeCodeBackend().generate(
                LLMRequest(
                    "claude", "", "", "요약해", workspace_dir=FIXTURES, file_access=True
                )
            )
        self.assertEqual(fake.flag_value("--allowedTools"), "Read,Glob,Grep")
        self.assertEqual(fake.cwd, FIXTURES)
        # 쓰기 계열 툴은 절대 열리지 않는다 (DESIGN §11).
        for banned in ("Write", "Edit", "Bash"):
            self.assertNotIn(banned, fake.flag_value("--allowedTools"))

    def test_system_prompt_and_model_flags(self):
        fake = FakeCli(stdout=CLAUDE_OK)
        with _patch(claude_mod, fake):
            claude_mod.ClaudeCodeBackend().generate(
                LLMRequest("claude", "opus", "항상 영어로만 답해", "hi")
            )
        self.assertEqual(fake.flag_value("--model"), "opus")
        self.assertIn("영어로만", fake.flag_value("--append-system-prompt"))

    def test_workspace_hint_in_system_prompt(self):
        fake = FakeCli(stdout=CLAUDE_OK)
        with _patch(claude_mod, fake):
            claude_mod.ClaudeCodeBackend().generate(
                LLMRequest(
                    "claude", "", "", "요약", workspace_dir=FIXTURES, file_access=True
                )
            )
        self.assertIn(FIXTURES, fake.flag_value("--append-system-prompt"))

    def test_no_model_flag_when_empty(self):
        fake = FakeCli(stdout=CLAUDE_OK)
        with _patch(claude_mod, fake):
            claude_mod.ClaudeCodeBackend().generate(LLMRequest("claude", "", "", "hi"))
        self.assertNotIn("--model", fake.args)

    def test_no_max_turns_flag(self):
        # 실측: 현재 claude CLI 에 --max-turns 가 없다 → 넣지 않는다.
        fake = FakeCli(stdout=CLAUDE_OK)
        with _patch(claude_mod, fake):
            claude_mod.ClaudeCodeBackend().generate(LLMRequest("claude", "", "", "hi"))
        self.assertNotIn("--max-turns", fake.args)

    def test_mcp_config_passthrough(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write("{}")
            mcp_path = fh.name
        try:
            fake = FakeCli(stdout=CLAUDE_OK)
            with _patch(claude_mod, fake):
                claude_mod.ClaudeCodeBackend().generate(
                    LLMRequest("claude", "", "", "hi", mcp_config=mcp_path)
                )
            self.assertEqual(fake.flag_value("--mcp-config"), mcp_path)
            self.assertIn("mcp__*", fake.flag_value("--allowedTools"))
        finally:
            os.unlink(mcp_path)

    def test_missing_mcp_config_ignored(self):
        fake = FakeCli(stdout=CLAUDE_OK)
        with _patch(claude_mod, fake):
            resp = claude_mod.ClaudeCodeBackend().generate(
                LLMRequest("claude", "", "", "hi", mcp_config="/no/such/file.json")
            )
        self.assertNotIn("--mcp-config", fake.args)
        self.assertIn("파일이 없어 무시", resp.raw_debug)

    def test_image_enables_read_and_is_staged(self):
        png = os.path.join(FIXTURES, "sample.png")
        fake = FakeCli(stdout=CLAUDE_OK)
        with _patch(claude_mod, fake):
            resp = claude_mod.ClaudeCodeBackend().generate(
                LLMRequest("claude", "", "", "이 그림 설명해", image_paths=[png])
            )
        self.assertEqual(resp.status, "ok")
        # file_access=False 여도 이미지가 있으면 Read 만 열린다.
        self.assertEqual(fake.flag_value("--allowedTools"), "Read")
        self.assertIn("sample.png", fake.stdin)

    def test_is_error_maps_to_error(self):
        payload = json.dumps({"is_error": True, "result": "something broke"})
        fake = FakeCli(stdout=payload)
        with _patch(claude_mod, fake):
            resp = claude_mod.ClaudeCodeBackend().generate(LLMRequest("claude", "", "", "hi"))
        self.assertTrue(resp.status.startswith("error:"))

    def test_login_error_detection(self):
        fake = FakeCli(code=1, stderr="Please log in to continue")
        with _patch(claude_mod, fake):
            resp = claude_mod.ClaudeCodeBackend().generate(LLMRequest("claude", "", "", "hi"))
        self.assertIn("로그인 필요", resp.status)

    def test_rate_limit_detection(self):
        fake = FakeCli(code=1, stderr="You have reached your usage limit")
        with _patch(claude_mod, fake):
            resp = claude_mod.ClaudeCodeBackend().generate(LLMRequest("claude", "", "", "hi"))
        self.assertEqual(resp.status, "rate_limited")

    def test_timeout_status(self):
        fake = FakeCli(code=-1, stderr="error: timeout(1s)")
        with _patch(claude_mod, fake):
            resp = claude_mod.ClaudeCodeBackend().generate(
                LLMRequest("claude", "", "", "hi", timeout_s=1)
            )
        self.assertIn("timeout", resp.status)

    def test_non_json_stdout_fallback(self):
        fake = FakeCli(stdout="그냥 텍스트 응답")
        with _patch(claude_mod, fake):
            resp = claude_mod.ClaudeCodeBackend().generate(LLMRequest("claude", "", "", "hi"))
        self.assertEqual(resp.text, "그냥 텍스트 응답")
        self.assertEqual(resp.status, "ok")

    def test_extra_args_appended(self):
        fake = FakeCli(stdout=CLAUDE_OK)
        with _patch(claude_mod, fake):
            claude_mod.ClaudeCodeBackend().generate(
                LLMRequest("claude", "", "", "hi", extra_args="--effort high")
            )
        self.assertIn("--effort", fake.args)
        self.assertIn("high", fake.args)

    def test_workspace_error_short_circuits(self):
        resp = claude_mod.ClaudeCodeBackend().generate(
            LLMRequest("claude", "", "", "hi", file_access=True)
        )
        self.assertIn("workspace_dir 확인 필요", resp.status)


GEMINI_OK = json.dumps({"session_id": "abc", "response": "안녕", "stats": {"turns": 1}})


class TestGeminiBackend(unittest.TestCase):
    def _backend(self):
        return gemini_mod.GeminiBackend(
            config={"defaults": {"gemini_model": "gemini-2.5-flash", "gemini_approval_mode": "plan"}}
        )

    def test_base_command(self):
        fake = FakeCli(stdout=GEMINI_OK)
        with _patch(gemini_mod, fake):
            resp = self._backend().generate(LLMRequest("gemini", "", "", "안녕이라고만 답해"))
        self.assertEqual(resp.status, "ok")
        self.assertEqual(resp.text, "안녕")
        self.assertEqual(fake.flag_value("-o"), "json")
        self.assertEqual(fake.flag_value("-m"), "gemini-2.5-flash")
        self.assertEqual(fake.stdin, "안녕이라고만 답해")

    def test_plan_mode_requires_skip_trust(self):
        # 실측: --skip-trust 가 없으면 plan 이 조용히 default 로 강등된다.
        fake = FakeCli(stdout=GEMINI_OK)
        with _patch(gemini_mod, fake):
            self._backend().generate(LLMRequest("gemini", "", "", "hi"))
        self.assertEqual(fake.flag_value("--approval-mode"), "plan")
        self.assertIn("--skip-trust", fake.args)
        # 쓰기/셸까지 열리는 모드는 절대 쓰지 않는다 (DESIGN §8.4).
        self.assertNotIn("--yolo", fake.args)
        self.assertNotIn("-y", fake.args)

    def test_explicit_model_overrides_default(self):
        fake = FakeCli(stdout=GEMINI_OK)
        with _patch(gemini_mod, fake):
            self._backend().generate(LLMRequest("gemini", "gemini-2.5-pro", "", "hi"))
        self.assertEqual(fake.flag_value("-m"), "gemini-2.5-pro")

    def test_system_prompt_merged_into_stdin(self):
        fake = FakeCli(stdout=GEMINI_OK)
        with _patch(gemini_mod, fake):
            self._backend().generate(LLMRequest("gemini", "", "항상 영어로만 답해", "안녕"))
        self.assertIn("### SYSTEM", fake.stdin)
        self.assertIn("영어로만", fake.stdin)
        self.assertIn("### TASK", fake.stdin)

    def test_media_referenced_with_at_syntax(self):
        png = os.path.join(FIXTURES, "sample.png")
        fake = FakeCli(stdout=GEMINI_OK)
        with _patch(gemini_mod, fake):
            self._backend().generate(
                LLMRequest("gemini", "", "", "설명해", image_paths=[png])
            )
        self.assertIn("@_llmhub_media/sample.png", fake.stdin)

    def test_auth_error_code_41(self):
        payload = json.dumps(
            {"session_id": "x", "error": {"type": "Error", "message": "Please set an Auth method", "code": 41}}
        )
        fake = FakeCli(code=1, stdout=payload)
        with _patch(gemini_mod, fake):
            resp = self._backend().generate(LLMRequest("gemini", "", "", "hi"))
        self.assertIn("로그인 필요", resp.status)

    def test_quota_error_maps_to_rate_limited(self):
        payload = json.dumps(
            {"error": {"type": "Error", "message": "429 quota exceeded", "code": 429}}
        )
        fake = FakeCli(code=1, stdout=payload)
        with _patch(gemini_mod, fake):
            resp = self._backend().generate(LLMRequest("gemini", "", "", "hi"))
        self.assertEqual(resp.status, "rate_limited")
        self.assertIn("Flash", resp.raw_debug)

    def test_timeout_status(self):
        fake = FakeCli(code=-1, stderr="error: timeout(1s)")
        with _patch(gemini_mod, fake):
            resp = self._backend().generate(LLMRequest("gemini", "", "", "hi", timeout_s=1))
        self.assertIn("timeout", resp.status)

    def test_mcp_config_noted_not_applied(self):
        fake = FakeCli(stdout=GEMINI_OK)
        with _patch(gemini_mod, fake):
            resp = self._backend().generate(
                LLMRequest("gemini", "", "", "hi", mcp_config="x.json")
            )
        self.assertIn("v1 미적용", resp.raw_debug)


class TestCodexBackend(unittest.TestCase):
    def test_base_command_and_sandbox(self):
        fake = FakeCli(write_last_message="안녕")
        with _patch(codex_mod, fake):
            resp = codex_mod.CodexBackend().generate(
                LLMRequest("codex", "", "", "안녕이라고만 답해")
            )
        self.assertEqual(resp.status, "ok")
        self.assertEqual(resp.text, "안녕")
        self.assertIn("exec", fake.args)
        self.assertEqual(fake.flag_value("-s"), "read-only")
        self.assertIn("--skip-git-repo-check", fake.args)
        # 샌드박스를 푸는 플래그는 절대 쓰지 않는다 (DESIGN §11).
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", fake.args)

    def test_prompt_via_stdin_with_dash(self):
        # 실측: '-' 를 주면 stdin 을 지시문으로 읽는다 → 인자 길이 제한 회피.
        fake = FakeCli(write_last_message="ok")
        with _patch(codex_mod, fake):
            codex_mod.CodexBackend().generate(LLMRequest("codex", "", "", "긴 프롬프트" * 500))
        self.assertEqual(fake.args[-1], "-")
        self.assertIn("긴 프롬프트", fake.stdin)
        self.assertNotIn("긴 프롬프트" * 500, " ".join(fake.args))

    def test_last_message_file_used(self):
        fake = FakeCli(stdout="시끄러운 로그 출력", write_last_message="진짜 최종 답변")
        with _patch(codex_mod, fake):
            resp = codex_mod.CodexBackend().generate(LLMRequest("codex", "", "", "hi"))
        self.assertEqual(resp.text, "진짜 최종 답변")

    def test_stdout_fallback_when_no_last_message(self):
        fake = FakeCli(stdout="stdout 응답", write_last_message="")
        with _patch(codex_mod, fake):
            resp = codex_mod.CodexBackend().generate(LLMRequest("codex", "", "", "hi"))
        self.assertEqual(resp.text, "stdout 응답")

    def test_images_passed_with_i_flag(self):
        png = os.path.join(FIXTURES, "sample.png")
        fake = FakeCli(write_last_message="설명")
        with _patch(codex_mod, fake):
            codex_mod.CodexBackend().generate(
                LLMRequest("codex", "", "", "설명해", image_paths=[png])
            )
        self.assertIn(png, fake.all_flag_values("-i"))

    def test_system_prompt_merged(self):
        fake = FakeCli(write_last_message="ok")
        with _patch(codex_mod, fake):
            codex_mod.CodexBackend().generate(
                LLMRequest("codex", "", "항상 영어로만 답해", "안녕")
            )
        self.assertIn("### SYSTEM", fake.stdin)
        self.assertIn("### TASK", fake.stdin)

    def test_model_flag(self):
        fake = FakeCli(write_last_message="ok")
        with _patch(codex_mod, fake):
            codex_mod.CodexBackend().generate(LLMRequest("codex", "gpt-5", "", "hi"))
        self.assertEqual(fake.flag_value("-m"), "gpt-5")

    def test_mcp_config_noted_unsupported(self):
        fake = FakeCli(write_last_message="ok")
        with _patch(codex_mod, fake):
            resp = codex_mod.CodexBackend().generate(
                LLMRequest("codex", "", "", "hi", mcp_config="x.json")
            )
        self.assertIn("v1 미지원", resp.raw_debug)

    def test_login_error_detection(self):
        fake = FakeCli(code=1, stderr="Please sign in with codex login", write_last_message="")
        with _patch(codex_mod, fake):
            resp = codex_mod.CodexBackend().generate(LLMRequest("codex", "", "", "hi"))
        self.assertIn("로그인 필요", resp.status)

    def test_timeout_status(self):
        fake = FakeCli(code=-1, stderr="error: timeout(1s)", write_last_message="")
        with _patch(codex_mod, fake):
            resp = codex_mod.CodexBackend().generate(
                LLMRequest("codex", "", "", "hi", timeout_s=1)
            )
        self.assertIn("timeout", resp.status)

    def test_temp_file_cleaned_up(self):
        fake = FakeCli(write_last_message="ok")
        with _patch(codex_mod, fake):
            codex_mod.CodexBackend().generate(LLMRequest("codex", "", "", "hi"))
        path = fake.flag_value("-o")
        self.assertFalse(os.path.exists(path))


class TestNoShellTrue(unittest.TestCase):
    """DESIGN §0-4 — subprocess 에 shell=True 가 없어야 한다."""

    def test_no_shell_true_in_sources(self):
        # 주석에 적힌 "shell=True 금지" 문구가 아니라 실제 호출 인자만 본다.
        import ast

        offenders = []
        for root, _dirs, files in os.walk(_PACK_ROOT):
            if "__pycache__" in root or os.sep + ".git" in root:
                continue
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                with open(path, "r", encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=path)
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    for kw in node.keywords:
                        if (
                            kw.arg == "shell"
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value is True
                        ):
                            offenders.append(f"{path}:{node.lineno}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
