# -*- coding: utf-8 -*-
"""자가 진단(/llmhub/health) 과 버전 표기.

진단 도구가 조용히 틀리면 진단할 방법이 없어진다 — 그래서 "실패해도 안 죽는지"
와 "버전이 세 군데에서 같은지" 를 여기서 고정한다.
"""

from __future__ import annotations

import importlib
import os
import re
import sys
import unittest
from unittest import mock

_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_PACK_ROOT))
_PACK_NAME = os.path.basename(_PACK_ROOT)

health = importlib.import_module(f"{_PACK_NAME}.utils.health")
version_mod = importlib.import_module(f"{_PACK_NAME}.version")
routes = importlib.import_module(f"{_PACK_NAME}.server_routes")


class TestVersionIsOneNumber(unittest.TestCase):
    """세 군데에 흩어져 있다. 어긋나면 진단할 때 제일 믿어야 할 숫자를 못 믿는다."""

    def _read(self, *parts):
        with open(os.path.join(_PACK_ROOT, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_pyproject_matches(self):
        found = re.search(r'^version\s*=\s*"([^"]+)"', self._read("pyproject.toml"), re.M)
        self.assertIsNotNone(found, "pyproject.toml 에서 version 을 못 읽었다")
        self.assertEqual(found.group(1), version_mod.__version__)

    def test_frontend_javascript_matches(self):
        js = self._read("web", "js", "llmhub_monitor.js")
        found = re.search(r'const VERSION = "([^"]+)"', js)
        self.assertIsNotNone(found, "JS 에서 VERSION 을 못 읽었다")
        self.assertEqual(found.group(1), version_mod.__version__)

    def test_javascript_logs_the_version_on_load(self):
        """이 줄이 콘솔에 없으면 JS 가 로드되지 않은 것 — 유일한 판별 수단이다."""
        js = self._read("web", "js", "llmhub_monitor.js")
        self.assertIn("console.log", js)
        self.assertIn("${VERSION}", js)


class TestCollect(unittest.TestCase):
    def setUp(self):
        # 실제 LM Studio 를 두드리지 않는다(느리고, 켜져 있으면 결과가 달라진다).
        patcher = mock.patch.object(health, "_check_lmstudio", return_value={
            "name": "LM Studio", "optional": True, "ok": False, "detail": "테스트",
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reports_version_and_checks(self):
        report = health.collect()
        self.assertEqual(report["version"], version_mod.__version__)
        self.assertTrue(report["checks"])
        for check in report["checks"]:
            for key in ("name", "ok", "optional", "detail"):
                self.assertIn(key, check, f"{check.get('name')}: {key} 가 없다")

    def test_frontend_js_is_a_required_check(self):
        """WEB_DIRECTORY 가 어긋나면 JS 가 통째로 죽는데 로그에 아무것도 안 남는다.

        그 사고를 실제로 겪었다. 이 항목만큼은 선택이 아니라 필수여야 한다.
        """
        report = health.collect()
        js = [c for c in report["checks"] if "JS" in c["name"]]
        self.assertEqual(len(js), 1)
        self.assertFalse(js[0]["optional"])
        self.assertTrue(js[0]["ok"], "web/js/llmhub_monitor.js 가 있어야 한다")

    def test_optional_failures_do_not_fail_the_whole_report(self):
        """ffmpeg 이 없다고 '고장' 이라고 하면 비디오를 안 쓰는 사람에게 거짓 경보다."""
        report = health.collect()
        optional_failed = [c for c in report["checks"] if c["optional"] and not c["ok"]]
        if not optional_failed:
            self.skipTest("이 환경에는 실패한 선택 항목이 없다")
        self.assertTrue(report["ok"])
        for check in optional_failed:
            self.assertNotIn(check["name"], report["failed"])

    def test_no_backend_is_required(self):
        """어느 백엔드가 필요한지는 노드에서 무엇을 고르느냐에 달려 있다.

        처음에는 claude/codex/gemini 를 필수로 뒀는데, 그러면 LM Studio 만 쓰는
        사람의 멀쩡한 설치가 '필수 항목 실패' 로 보인다. CI 가 잡은 실제 버그다.
        """
        report = health.collect()
        for name in ("claude", "codex", "gemini", "lms", "LM Studio"):
            entry = next((c for c in report["checks"] if c["name"] == name), None)
            self.assertIsNotNone(entry, f"{name} 항목이 사라졌다")
            self.assertTrue(entry["optional"], f"{name}: 필수로 두면 거짓 경보가 된다")

    def test_only_pack_integrity_is_required(self):
        """'필수' 는 노드팩 자체가 성립하는가이지 사용자의 준비 상태가 아니다."""
        report = health.collect()
        required = [c["name"] for c in report["checks"] if not c["optional"]]
        self.assertEqual(required, ["frontend JS"])

    def test_backend_summary_lists_what_is_ready(self):
        checks = [
            {"name": "claude", "optional": True, "ok": True, "detail": ""},
            {"name": "gemini", "optional": True, "ok": False, "detail": ""},
            {"name": "LM Studio", "optional": True, "ok": True, "detail": ""},
        ]
        summary = health._backend_summary(checks)
        self.assertTrue(summary["ok"])
        self.assertIn("claude", summary["detail"])
        self.assertIn("lmstudio", summary["detail"])
        self.assertNotIn("gemini", summary["detail"])

    def test_backend_summary_does_not_claim_openai_compat_is_missing(self):
        """주소를 노드에서 넣는 구조라 진단이 알 수 없다. 모르는 걸 '없음' 이라 하면 거짓말이다."""
        summary = health._backend_summary([])
        self.assertFalse(summary["ok"])
        self.assertTrue(summary["optional"], "이게 필수면 새 설치가 전부 빨갛게 보인다")
        self.assertIn("openai_compat", summary["detail"])

    def test_an_unexpected_error_is_reported_not_raised(self):
        """진단이 예외로 죽으면 진단할 방법 자체가 사라진다."""
        proc = importlib.import_module(f"{_PACK_NAME}.utils.proc")
        with mock.patch.object(proc, "resolve_cli", side_effect=RuntimeError("boom")):
            entries = health._check_clis()
        self.assertTrue(entries)
        for entry in entries:
            self.assertFalse(entry["ok"])
            self.assertIn("boom", entry["detail"])

    def test_cli_lookup_failure_is_reported_not_raised(self):
        proc = importlib.import_module(f"{_PACK_NAME}.utils.proc")
        with mock.patch.object(proc, "resolve_cli", side_effect=proc.CliNotFoundError("없음")):
            entries = health._check_clis()
        self.assertTrue(entries)
        for entry in entries:
            self.assertFalse(entry["ok"])
            self.assertIn("없음", entry["detail"])


class TestAsText(unittest.TestCase):
    def test_is_readable_plain_text(self):
        report = {
            "version": "9.9.9", "ok": True, "failed": [],
            "python": "3.12.0", "platform": "Windows 11", "pack_dir": "C:\\x",
            "checks": [
                {"name": "필수것", "optional": False, "ok": True, "detail": "좋음"},
                {"name": "선택것", "optional": True, "ok": False, "detail": "없음"},
            ],
        }
        text = health.as_text(report)
        self.assertIn("9.9.9", text)
        self.assertIn("[ OK ] 필수것", text)
        # 선택 항목 실패는 FAIL 이 아니다. 빨간 글씨로 보이면 겁만 준다.
        self.assertIn("[ -- ] 선택것", text)
        self.assertNotIn("[FAIL]", text)

    def test_required_failure_is_marked_fail(self):
        report = {
            "version": "1", "ok": False, "failed": ["필수것"],
            "python": "3.12.0", "platform": "Linux", "pack_dir": "/x",
            "checks": [{"name": "필수것", "optional": False, "ok": False, "detail": "깨짐"}],
        }
        text = health.as_text(report)
        self.assertIn("[FAIL] 필수것", text)
        self.assertIn("Required checks failed: 필수것", text)


class TestRouteWiring(unittest.TestCase):
    def test_health_route_is_declared(self):
        self.assertEqual(routes.HEALTH_ROUTE, "/llmhub/health")

    def test_register_is_safe_outside_comfyui(self):
        """ComfyUI 밖(테스트/CI)에서는 PromptServer 가 없다. 죽으면 안 된다."""
        self.assertFalse(routes.register())


if __name__ == "__main__":
    unittest.main(verbosity=2)
