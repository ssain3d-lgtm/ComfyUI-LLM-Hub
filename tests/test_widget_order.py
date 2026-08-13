# -*- coding: utf-8 -*-
"""위젯 순서 고정.

ComfyUI 는 노드의 값을 widgets_values 라는 **순서 배열**로 저장한다. 이름이
아니라 자리로 읽으므로, 중간에 위젯을 하나 끼우면 그 뒤 값이 전부 한 칸씩
밀린다 -- 이미 저장해둔 워크플로우가 조용히 다른 값으로 열린다.

실제로 그랬다. openai_base_url 을 lmstudio_ttl_sec 바로 뒤에 끼워 넣는 바람에
그 이전에 저장한 워크플로우는 openai_base_url 에 bool 이, lmstudio_unload_after
에 문자열이 들어왔고, True.strip() 에서 노드가 통째로 죽었다. 배포된 예제
워크플로우 3개도 전부 그 상태였다.

INPUT_TYPES 안에서는 순서가 "코드를 적은 자리" 로만 보여서 눈에 띄지 않는다.
그래서 이름 목록을 따로 두고 여기서 대조한다.
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

nodes_mod = importlib.import_module(f"{_PACK_NAME}.nodes")
base = importlib.import_module(f"{_PACK_NAME}.backends.base")

# IMAGE / VIDEO 는 링크 입력이라 위젯이 아니고 배열 자리도 차지하지 않는다.
LINK_TYPES = {"IMAGE", "VIDEO"}


def actual_widget_order():
    """INPUT_TYPES 에서 실제 위젯 순서를 뽑는다 (프론트엔드가 만드는 순서와 같다)."""
    spec = nodes_mod.LLMHubGenerate.INPUT_TYPES()
    order = []
    for section in ("required", "optional"):
        for name, decl in spec[section].items():
            if isinstance(decl[0], str) and decl[0] in LINK_TYPES:
                continue
            order.append(name)
            options = decl[1] if len(decl) > 1 else {}
            # seed 에 control_after_generate 를 주면 프론트엔드가 짝꿍 위젯을
            # 하나 더 만들어 붙인다. 그것도 배열 자리를 차지한다.
            if isinstance(options, dict) and options.get("control_after_generate"):
                order.append("control_after_generate")
    return order


class TestWidgetOrder(unittest.TestCase):
    def test_matches_the_frozen_list(self):
        """어긋나면 저장된 워크플로우가 깨진다. 새 이름은 맨 뒤에만 붙인다."""
        self.assertEqual(actual_widget_order(), nodes_mod.WIDGET_ORDER)

    def test_no_duplicates(self):
        order = nodes_mod.WIDGET_ORDER
        self.assertEqual(len(order), len(set(order)))

    def test_core_widgets_never_move(self):
        """v1.0.0 워크플로우가 지금도 열려야 한다. 앞쪽은 그때 그대로여야 한다."""
        original = [
            "backend", "prompt", "system_prompt", "model", "file_access",
            "workspace_dir", "temperature", "max_tokens", "timeout_sec", "seed",
            "control_after_generate", "video_max_frames", "stream_view",
            "video_path", "mcp_config", "extra_args",
        ]
        self.assertEqual(nodes_mod.WIDGET_ORDER[:len(original)], original)


class TestLegacyWorkflowLoads(unittest.TestCase):
    """예전에 저장한 워크플로우를 열었을 때 값이 제자리로 가는지."""

    # openai_compat 이전에 저장된 배열(20칸). 배포된 예제 3개가 이 모양이다.
    LEGACY_20 = [
        "lmstudio", "안녕이라고만 답해", "", "", False, "", 0.7, 2048, 300, 0,
        "randomize", 8, "plain", "", "", "", "(auto)", 300, True, "(auto)",
    ]

    def test_legacy_values_land_on_the_right_widgets(self):
        mapped = dict(zip(nodes_mod.WIDGET_ORDER, self.LEGACY_20))
        self.assertEqual(mapped["backend"], "lmstudio")
        self.assertEqual(mapped["lmstudio_model"], "(auto)")
        self.assertEqual(mapped["lmstudio_ttl_sec"], 300)
        # 이 둘이 이 버그의 피해자였다.
        self.assertEqual(mapped["lmstudio_unload_after"], True)
        self.assertEqual(mapped["claude_model"], "(auto)")
        # 뒤에 새로 붙은 것은 값이 없어 기본값이 된다 — 이게 정상 동작이다.
        self.assertNotIn("openai_base_url", mapped)
        self.assertNotIn("system_preset", mapped)


class TestWrongTypesNeverCrash(unittest.TestCase):
    """순서가 한 번이라도 어긋났던 워크플로우는 엉뚱한 타입을 들고 온다.

    노드는 어떤 입력에도 예외를 밖으로 던지지 않아야 한다 (DESIGN N4).
    """

    def _run(self, **kwargs):
        class Spy:
            def generate(self, req):
                return base.LLMResponse(text="x", status="ok")

        args = dict(
            backend="lmstudio", prompt="hi", system_prompt="", model="",
            file_access=False, workspace_dir="", temperature=0.7, max_tokens=64,
            timeout_sec=10, stream_view="off", seed=0,
        )
        args.update(kwargs)
        with mock.patch.object(nodes_mod, "get_backend", return_value=Spy()):
            return nodes_mod.LLMHubGenerate().generate(**args)

    def test_bool_in_a_string_slot(self):
        """재현했던 그대로: openai_base_url 에 True 가 들어와 .strip() 이 터졌다."""
        out = self._run(openai_base_url=True)
        self.assertEqual(out["result"][1], "ok", out["result"][2][:200])

    def test_string_in_a_bool_slot(self):
        out = self._run(lmstudio_unload_after="(auto)")
        self.assertEqual(out["result"][1], "ok")

    def test_none_everywhere_is_survivable(self):
        out = self._run(model=None, workspace_dir=None, video_path=None,
                        mcp_config=None, extra_args=None, openai_base_url=None)
        self.assertEqual(out["result"][1], "ok")


class TestShippedExamples(unittest.TestCase):
    """배포되는 예제는 현재 위젯 수와 맞아야 한다.

    모자라도 동작은 하지만(뒤쪽이 기본값), 예제가 보여주려는 값이 빠져 있으면
    예제 구실을 못 한다 -- 03_lmstudio_vram 의 lmstudio_unload_after 가 그랬다.
    """

    def _examples(self):
        folder = os.path.join(_PACK_ROOT, "example_workflows")
        for name in sorted(os.listdir(folder)):
            if name.endswith(".json"):
                with open(os.path.join(folder, name), encoding="utf-8") as fh:
                    yield name, json.load(fh)

    def test_every_example_has_a_full_widget_array(self):
        for name, data in self._examples():
            for node in data.get("nodes", []):
                if node.get("type") != "LLMHubGenerate":
                    continue
                values = node.get("widgets_values", [])
                self.assertEqual(
                    len(values), len(nodes_mod.WIDGET_ORDER),
                    f"{name}: 위젯 {len(nodes_mod.WIDGET_ORDER)}개인데 값은 {len(values)}개",
                )

    def test_example_values_have_the_right_types(self):
        """자리가 밀리면 여기서 걸린다 (bool 자리에 문자열이 오는 식)."""
        expected = {
            "backend": str, "file_access": bool, "temperature": (int, float),
            "max_tokens": int, "timeout_sec": int, "video_max_frames": int,
            "lmstudio_ttl_sec": int, "lmstudio_unload_after": bool,
            "openai_base_url": str, "system_preset": str,
        }
        for name, data in self._examples():
            for node in data.get("nodes", []):
                if node.get("type") != "LLMHubGenerate":
                    continue
                mapped = dict(zip(nodes_mod.WIDGET_ORDER, node["widgets_values"]))
                for widget, kind in expected.items():
                    self.assertIsInstance(
                        mapped[widget], kind,
                        f"{name}: {widget} 가 {type(mapped[widget]).__name__}",
                    )

    def test_backend_values_are_real_backends(self):
        backends = importlib.import_module(f"{_PACK_NAME}.backends")
        for name, data in self._examples():
            for node in data.get("nodes", []):
                if node.get("type") != "LLMHubGenerate":
                    continue
                mapped = dict(zip(nodes_mod.WIDGET_ORDER, node["widgets_values"]))
                self.assertIn(mapped["backend"], backends.BACKEND_NAMES, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
