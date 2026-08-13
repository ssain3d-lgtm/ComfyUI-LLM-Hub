# -*- coding: utf-8 -*-
"""claude_model 드롭다운 (실측 회귀).

claude CLI 의 --model 별칭은 2026-08-12 에 넷 다 직접 호출해 확인했다:
  haiku -> claude-haiku-4-5-20251001   opus -> claude-opus-5
  sonnet -> claude-sonnet-5            fable -> claude-fable-5
"""

from __future__ import annotations

import importlib
import os
import sys
import unittest
from unittest import mock

_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_PACK_ROOT))
_PACK_NAME = os.path.basename(_PACK_ROOT)

base = importlib.import_module(f"{_PACK_NAME}.backends.base")
nodes_mod = importlib.import_module(f"{_PACK_NAME}.nodes")


class Spy:
    def __init__(self):
        self.model = None

    def generate(self, req):
        self.model = req.model
        return base.LLMResponse(text="x", status="ok")


def _run(**overrides):
    spy = Spy()
    kwargs = dict(
        backend="claude", prompt="hi", system_prompt="", model="",
        file_access=False, workspace_dir="", temperature=0.7, max_tokens=64,
        timeout_sec=10, stream_view="off", seed=0,
    )
    kwargs.update(overrides)
    with mock.patch.object(nodes_mod, "get_backend", return_value=spy):
        nodes_mod.LLMHubGenerate().generate(**kwargs)
    return spy.model


class TestClaudeModelDropdown(unittest.TestCase):
    def test_dropdown_offers_the_verified_aliases(self):
        self.assertEqual(
            nodes_mod.CLAUDE_MODELS,
            [nodes_mod.AUTO_MODEL, "haiku", "opus", "sonnet", "fable"],
        )

    def test_widget_is_still_in_the_frozen_order(self):
        """개별 위치가 아니라 WIDGET_ORDER 전체를 test_widget_order 가 고정한다.

        여기서는 이 드롭다운이 목록에서 사라지지 않았는지만 본다.
        """
        optional = list(nodes_mod.LLMHubGenerate.INPUT_TYPES()["optional"])
        self.assertIn("claude_model", optional)
        self.assertIn("claude_model", nodes_mod.WIDGET_ORDER)

    def test_dropdown_picks_the_model(self):
        self.assertEqual(_run(claude_model="haiku"), "haiku")

    def test_auto_falls_back_to_the_model_box(self):
        self.assertEqual(
            _run(model="claude-opus-5", claude_model=nodes_mod.AUTO_MODEL),
            "claude-opus-5",
        )

    def test_dropdown_wins_over_the_model_box(self):
        self.assertEqual(_run(model="sonnet", claude_model="haiku"), "haiku")

    def test_dropdown_is_ignored_by_other_backends(self):
        """claude 드롭다운이 lmstudio 실행의 모델을 가로채면 안 된다."""
        self.assertEqual(
            _run(backend="lmstudio", model="qwen3.6-27b", claude_model="opus"),
            "qwen3.6-27b",
        )

    def test_lmstudio_dropdown_is_ignored_by_claude(self):
        self.assertEqual(
            _run(backend="claude", model="", lmstudio_model="qwen3.6-27b",
                 claude_model=nodes_mod.AUTO_MODEL),
            "",
        )

    def test_old_workflow_without_the_widget_still_runs(self):
        """claude_model 을 아예 안 넘겨도 기본값으로 동작해야 한다."""
        self.assertEqual(_run(model="opus"), "opus")


if __name__ == "__main__":
    unittest.main()
