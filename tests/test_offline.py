# -*- coding: utf-8 -*-
"""로그인/서버 없이 돌아가는 오프라인 검증 (unittest).

CLI 로그인이나 LM Studio 없이도 확인 가능한 부분만 다룬다:
  - 경로 탈출 차단 등 보안 규칙 (DESIGN §11)
  - LM Studio 툴 루프 (가짜 서버 사용, DESIGN §8.1)
  - 시스템 프롬프트 병합 / 워크스페이스 검증 (DESIGN §7, §8.5)
  - CLI 백엔드의 커맨드 조립 및 출력 파싱 (DESIGN §8.2~§8.4)
  - 노드가 절대 예외를 던지지 않는지 (DESIGN N4)

실행: python -m unittest discover -s tests -v   (패키지 폴더에서)
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import unittest

# 패키지 이름에 하이픈이 있어 일반 import 문을 쓸 수 없다 → importlib 로 불러온다.
# (ComfyUI 도 커스텀 노드를 importlib 로 로드하므로 런타임에는 문제되지 않는다.)
_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_PACK_ROOT))
_PACK_NAME = os.path.basename(_PACK_ROOT)

_pack = importlib.import_module(_PACK_NAME)
base = importlib.import_module(f"{_PACK_NAME}.backends.base")
fs_tools = importlib.import_module(f"{_PACK_NAME}.utils.fs_tools")
proc = importlib.import_module(f"{_PACK_NAME}.utils.proc")
lmstudio_mod = importlib.import_module(f"{_PACK_NAME}.backends.lmstudio")
nodes_mod = importlib.import_module(f"{_PACK_NAME}.nodes")
LLMRequest = base.LLMRequest

FIXTURES = os.path.join(_PACK_ROOT, "tests", "fixtures")

sys.path.insert(0, os.path.join(_PACK_ROOT, "tests"))
from mock_lmstudio import MockLMStudio  # noqa: E402


class TestPathSecurity(unittest.TestCase):
    """DESIGN §11 — 경로 탈출 차단."""

    def test_read_inside_workspace(self):
        out = fs_tools.read_file(FIXTURES, "test.txt")
        self.assertIn("LLMHUB-7391", out)

    def test_escape_with_dotdot_denied(self):
        out = fs_tools.read_file(FIXTURES, "../test_offline.py")
        self.assertIn("access denied", out)

    def test_deep_escape_denied(self):
        out = fs_tools.read_file(FIXTURES, "../../../../etc/passwd")
        self.assertIn("access denied", out)

    def test_absolute_path_outside_denied(self):
        out = fs_tools.read_file(FIXTURES, os.path.abspath(__file__))
        self.assertIn("access denied", out)

    def test_list_dir_escape_denied(self):
        out = fs_tools.list_dir(FIXTURES, "..")
        self.assertIn("access denied", out)

    def test_symlink_outside_denied(self):
        link = os.path.join(FIXTURES, "_tmp_escape_link")
        target = os.path.dirname(_PACK_ROOT)
        if os.path.islink(link):
            os.unlink(link)
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("이 환경에서는 심볼릭 링크를 만들 수 없음")
        try:
            out = fs_tools.list_dir(FIXTURES, "_tmp_escape_link")
            self.assertIn("access denied", out)
        finally:
            if os.path.islink(link):
                os.unlink(link)

    def test_list_dir_root(self):
        out = fs_tools.list_dir(FIXTURES, ".")
        parsed = json.loads(out)
        names = [e["name"] for e in parsed["entries"]]
        self.assertIn("test.txt", names)

    def test_read_limit_truncates(self):
        out = fs_tools.read_file(FIXTURES, "test.txt", max_bytes=10)
        self.assertIn("truncated", out)

    def test_binary_detection(self):
        path = os.path.join(FIXTURES, "_tmp_binary.bin")
        with open(path, "wb") as fh:
            fh.write(b"\x00\x01\x02\x03" * 64)
        try:
            out = fs_tools.read_file(FIXTURES, "_tmp_binary.bin")
            self.assertTrue(out.startswith("binary file"))
        finally:
            os.remove(path)


class TestPromptRules(unittest.TestCase):
    """DESIGN §7, §8.5."""

    def test_merge_without_system(self):
        req = LLMRequest("codex", "", "", "안녕")
        self.assertEqual(base.merge_system_prompt(req), "안녕")

    def test_merge_with_system(self):
        req = LLMRequest("codex", "", "영어로만 답해", "안녕")
        merged = base.merge_system_prompt(req)
        self.assertIn("### SYSTEM", merged)
        self.assertIn("영어로만 답해", merged)
        self.assertIn("### TASK", merged)
        self.assertTrue(merged.rstrip().endswith("안녕"))

    def test_workspace_hint_injected(self):
        req = LLMRequest(
            "codex", "", "", "요약해", workspace_dir=FIXTURES, file_access=True
        )
        self.assertIn(FIXTURES, base.merge_system_prompt(req))

    def test_hint_absent_without_file_access(self):
        req = LLMRequest("codex", "", "", "요약해", workspace_dir=FIXTURES)
        self.assertEqual(base.workspace_hint(req), "")

    def test_workspace_validation(self):
        missing = LLMRequest("codex", "", "", "x", file_access=True)
        self.assertIn("workspace_dir 확인 필요", base.validate_workspace(missing))

        bad = LLMRequest(
            "codex", "", "", "x", workspace_dir="/definitely/not/here", file_access=True
        )
        self.assertIn("workspace_dir 확인 필요", base.validate_workspace(bad))

        good = LLMRequest("codex", "", "", "x", workspace_dir=FIXTURES, file_access=True)
        self.assertEqual(base.validate_workspace(good), "")

    def test_debug_truncation(self):
        out = base.truncate_debug("x" * 10000)
        self.assertLessEqual(len(out), base.MAX_DEBUG_CHARS + 40)
        self.assertIn("truncated", out)

    def test_error_detection(self):
        self.assertTrue(base.detect_login_error("Please log in to continue"))
        self.assertTrue(base.detect_login_error("Please set an Auth method in settings"))
        self.assertFalse(base.detect_login_error("everything is fine"))
        self.assertTrue(base.detect_rate_limit("You have hit your usage limit"))
        self.assertTrue(base.detect_rate_limit("HTTP 429 returned"))
        self.assertFalse(base.detect_rate_limit("all good"))


class TestLMStudioBackend(unittest.TestCase):
    """DESIGN §8.1 — 가짜 LM Studio 서버로 검증."""

    def _make(self, server, **overrides):
        cfg = {
            "lmstudio": {"base_url": server.base_url, "api_token": "", "default_model": ""},
            "tool_loop_max_iters": 8,
            "max_file_read_bytes": 262144,
        }
        cfg.update(overrides)
        return lmstudio_mod.LMStudioBackend(config=cfg)

    def test_plain_text_generation(self):
        with MockLMStudio(script=[{"content": "안녕"}]) as server:
            backend = self._make(server)
            resp = backend.generate(LLMRequest("lmstudio", "", "", "안녕이라고만 답해"))
            self.assertEqual(resp.status, "ok")
            self.assertEqual(resp.text, "안녕")
            payload = server.requests[0]
            self.assertEqual(payload["messages"][-1]["content"], "안녕이라고만 답해")
            self.assertNotIn("tools", payload)  # file_access=False → 툴 미포함

    def test_system_prompt_sent(self):
        with MockLMStudio(script=[{"content": "hi"}]) as server:
            backend = self._make(server)
            backend.generate(LLMRequest("lmstudio", "", "항상 영어로만 답해", "안녕"))
            messages = server.requests[0]["messages"]
            self.assertEqual(messages[0]["role"], "system")
            self.assertIn("영어로만", messages[0]["content"])

    def test_tool_loop_reads_file(self):
        script = [
            {"tool_calls": [{"name": "list_dir", "path": "."}]},
            {"tool_calls": [{"name": "read_file", "path": "test.txt"}]},
            {"content": "고양이는 3마리이고 비밀 코드는 LLMHUB-7391 이다."},
        ]
        with MockLMStudio(script=script) as server:
            backend = self._make(server)
            resp = backend.generate(
                LLMRequest(
                    "lmstudio", "", "", "test.txt 를 요약해",
                    workspace_dir=FIXTURES, file_access=True,
                )
            )
            self.assertEqual(resp.status, "ok")
            self.assertIn("LLMHUB-7391", resp.text)
            self.assertIn("tools", server.requests[0])
            results = server.tool_results()
            self.assertTrue(any("test.txt" in r for r in results))
            self.assertTrue(any("LLMHUB-7391" in r for r in results))

    def test_tool_loop_blocks_escape(self):
        script = [
            {"tool_calls": [{"name": "read_file", "path": "../../../etc/passwd"}]},
            {"content": "읽을 수 없었다"},
        ]
        with MockLMStudio(script=script) as server:
            backend = self._make(server)
            resp = backend.generate(
                LLMRequest(
                    "lmstudio", "", "", "비밀번호 파일 읽어",
                    workspace_dir=FIXTURES, file_access=True,
                )
            )
            self.assertEqual(resp.status, "ok")
            self.assertTrue(any("access denied" in r for r in server.tool_results()))

    def test_tool_loop_limit(self):
        script = [{"tool_calls": [{"name": "list_dir", "path": "."}]}] * 20
        with MockLMStudio(script=script) as server:
            backend = self._make(server, tool_loop_max_iters=3)
            resp = backend.generate(
                LLMRequest(
                    "lmstudio", "", "", "계속 뒤져",
                    workspace_dir=FIXTURES, file_access=True,
                )
            )
            self.assertIn("tool loop limit", resp.raw_debug)
            self.assertEqual(len(server.requests), 3)

    def test_model_fallback_via_v1_models(self):
        with MockLMStudio(script=[{"content": "ok"}], fail_without_model=True) as server:
            backend = self._make(server)
            resp = backend.generate(LLMRequest("lmstudio", "", "", "안녕"))
            self.assertEqual(resp.status, "ok")
            self.assertIn("mock-model-a", resp.raw_debug)
            self.assertEqual(server.requests[-1]["model"], "mock-model-a")

    def test_connection_error_message(self):
        cfg = {"lmstudio": {"base_url": "http://127.0.0.1:1"}}
        backend = lmstudio_mod.LMStudioBackend(config=cfg)
        resp = backend.generate(LLMRequest("lmstudio", "", "", "안녕", timeout_s=5))
        self.assertIn("LM Studio 서버 응답 없음", resp.status)

    def test_workspace_error_short_circuits(self):
        backend = lmstudio_mod.LMStudioBackend(config={})
        resp = backend.generate(
            LLMRequest("lmstudio", "", "", "안녕", file_access=True)
        )
        self.assertIn("workspace_dir 확인 필요", resp.status)

    def test_mcp_config_note(self):
        with MockLMStudio(script=[{"content": "ok"}]) as server:
            backend = self._make(server)
            resp = backend.generate(
                LLMRequest("lmstudio", "", "", "안녕", mcp_config="x.json")
            )
            self.assertIn("v1.5", resp.raw_debug)


class TestProcHelpers(unittest.TestCase):
    """DESIGN §9."""

    def test_extra_args_parsing(self):
        self.assertEqual(proc.parse_extra_args(""), [])
        self.assertEqual(proc.parse_extra_args("--foo bar"), ["--foo", "bar"])
        # Windows 에서만 역슬래시 경로가 보존된다(posix=False). 리눅스/맥은 posix=True.
        if sys.platform == "win32":
            self.assertIn("C:\\tmp", proc.parse_extra_args("--dir C:\\tmp"))

    def test_missing_cli_raises(self):
        with self.assertRaises(proc.CliNotFoundError):
            proc.resolve_cli("definitely-not-a-real-cli-xyz")

    def test_empty_dir_lifecycle(self):
        path = proc.make_empty_dir()
        try:
            self.assertTrue(os.path.isdir(path))
            self.assertEqual(os.listdir(path), [])
        finally:
            proc.cleanup_dir(path)
        self.assertFalse(os.path.exists(path))

    def test_timeout_kills_process(self):
        args = [sys.executable, "-c", "import time; time.sleep(30)"]
        code, _out, err, _dur = proc.run_cli(args, cwd=None, stdin_text=None, timeout_s=2)
        self.assertEqual(code, -1)
        self.assertIn("timeout(2s)", err)

    def test_run_cli_roundtrip(self):
        args = [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"]
        code, out, _err, _dur = proc.run_cli(
            args, cwd=None, stdin_text="hello", timeout_s=30
        )
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "HELLO")


class TestNodeContract(unittest.TestCase):
    """DESIGN §5, N4 — 노드는 항상 3개 출력을 돌려준다."""

    def test_input_types_shape(self):
        spec = nodes_mod.LLMHubGenerate.INPUT_TYPES()
        required = spec["required"]
        self.assertEqual(required["backend"][0], ["lmstudio", "claude", "codex", "gemini"])
        for key in (
            "prompt", "system_prompt", "model", "file_access", "workspace_dir",
            "temperature", "max_tokens", "timeout_sec", "seed",
        ):
            self.assertIn(key, required)
        for key in ("image", "mcp_config", "extra_args"):
            self.assertIn(key, spec["optional"])
        self.assertTrue(required["seed"][1]["control_after_generate"])

    def test_no_is_changed(self):
        # DESIGN §5-4: IS_CHANGED 는 일부러 구현하지 않는다.
        self.assertFalse(hasattr(nodes_mod.LLMHubGenerate, "IS_CHANGED"))

    def test_returns_three_outputs_on_bad_backend(self):
        node = nodes_mod.LLMHubGenerate()
        result = node.generate(
            backend="nope", prompt="hi", system_prompt="", model="",
            file_access=False, workspace_dir="", temperature=0.7,
            max_tokens=128, timeout_sec=10, seed=0,
        )
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], "")
        self.assertTrue(result[1].startswith("error:"))

    def test_returns_three_outputs_on_bad_workspace(self):
        node = nodes_mod.LLMHubGenerate()
        result = node.generate(
            backend="lmstudio", prompt="hi", system_prompt="", model="",
            file_access=True, workspace_dir="", temperature=0.7,
            max_tokens=128, timeout_sec=10, seed=0,
        )
        self.assertEqual(len(result), 3)
        self.assertIn("workspace_dir 확인 필요", result[1])

    def test_mappings_registered(self):
        self.assertIn("LLMHubGenerate", _pack.NODE_CLASS_MAPPINGS)
        self.assertEqual(
            _pack.NODE_DISPLAY_NAME_MAPPINGS["LLMHubGenerate"], "LLM Hub Generate"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
