# -*- coding: utf-8 -*-
"""web/js 의 백엔드별 위젯 숨김이 실제 위젯 이름과 어긋나지 않는지 확인한다.

JS 는 자동 테스트가 없어서, 위젯 이름 오타 하나면 그 위젯은 영영 안 숨겨진다.
증상이 "그냥 옵션이 좀 많네" 라서 아무도 버그로 신고하지 않는다 — 그래서 여기서 막는다.
"""

from __future__ import annotations

import importlib
import os
import re
import sys
import unittest

_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_PACK_ROOT))
_PACK_NAME = os.path.basename(_PACK_ROOT)

nodes_mod = importlib.import_module(f"{_PACK_NAME}.nodes")
backends_mod = importlib.import_module(f"{_PACK_NAME}.backends")

_JS_PATH = os.path.join(_PACK_ROOT, "web", "js", "llmhub_monitor.js")


def _javascript():
    with open(_JS_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


def _backend_only_map(javascript):
    """JS 의 BACKEND_ONLY 리터럴을 {위젯이름: [백엔드...]} 로 읽어낸다."""
    body = javascript.split("const BACKEND_ONLY = {", 1)[1].split("\n};", 1)[0]
    out = {}
    for name, backends in re.findall(r"(\w+)\s*:\s*\[([^\]]*)\]", body):
        out[name] = re.findall(r'"([^"]+)"', backends)
    return out


class TestWidgetVisibility(unittest.TestCase):
    def setUp(self):
        self.javascript = _javascript()
        self.mapping = _backend_only_map(self.javascript)
        body = self.javascript.split("const ADVANCED = [", 1)[1].split("];", 1)[0]
        # 주석 줄을 먼저 걷어낸다. 안 그러면 주석 안의 따옴표가 위젯 이름으로 잡힌다.
        body = "\n".join(l for l in body.splitlines() if not l.strip().startswith("//"))
        self.advanced = re.findall(r'"([^"]+)"', body)
        spec = nodes_mod.LLMHubGenerate.INPUT_TYPES()
        self.widget_names = set(spec.get("required", {})) | set(spec.get("optional", {}))

    def test_map_is_not_empty(self):
        self.assertTrue(self.mapping, "BACKEND_ONLY 를 못 읽었다 — JS 구조가 바뀌었나?")

    def test_every_hidden_widget_actually_exists(self):
        unknown = sorted(set(self.mapping) - self.widget_names)
        self.assertEqual(unknown, [], f"INPUT_TYPES 에 없는 위젯 이름: {unknown}")

    def test_every_named_backend_actually_exists(self):
        known = set(backends_mod.BACKEND_NAMES)
        for widget, backends in self.mapping.items():
            unknown = sorted(set(backends) - known)
            self.assertEqual(unknown, [], f"{widget}: 존재하지 않는 백엔드 {unknown}")

    def test_backend_widget_itself_is_never_hidden(self):
        """backend 를 숨기면 다시 바꿀 방법이 사라진다."""
        self.assertNotIn("backend", self.mapping)

    def test_core_widgets_are_never_hidden(self):
        """접든 펴든, 어느 백엔드든 이것들은 보여야 한다."""
        for name in ("backend", "prompt", "system_prompt"):
            self.assertNotIn(name, self.mapping, f"{name}: BACKEND_ONLY 에 있으면 안 된다")
            self.assertNotIn(name, self.advanced, f"{name}: ADVANCED 에 있으면 안 된다")

    # control_after_generate 는 INPUT_TYPES 에 없다. seed 의 control_after_generate:True
    # 를 보고 프론트엔드가 만들어 붙이는 짝꿍 위젯이다(실측: node.widgets 인덱스 10).
    FRONTEND_MADE = {"control_after_generate"}

    def test_every_advanced_widget_actually_exists(self):
        unknown = sorted(set(self.advanced) - self.widget_names - self.FRONTEND_MADE)
        self.assertEqual(unknown, [], f"INPUT_TYPES 에 없는 위젯 이름: {unknown}")

    def test_seed_and_its_companion_hide_together(self):
        """seed 만 숨기면 randomize 줄이 홀로 남는다 (실측으로 확인한 증상)."""
        spec = nodes_mod.LLMHubGenerate.INPUT_TYPES()
        seed_opts = spec["required"]["seed"][1]
        self.assertTrue(seed_opts.get("control_after_generate"),
                        "seed 가 짝꿍 위젯을 안 만든다면 이 규칙은 필요 없다")
        self.assertIn("seed", self.advanced)
        self.assertIn("control_after_generate", self.advanced)

    def test_dom_widget_sets_the_height_api_this_frontend_uses(self):
        """이 프론트엔드는 DOM 위젯 높이를 computeLayoutSize 로 잡는다.

        computeSize 만 주면 무시돼 패널이 위 위젯을 덮는다(실측).
        """
        self.assertIn("computeLayoutSize", self.javascript)
        self.assertIn("minHeight", self.javascript)

    def test_hiding_uses_both_mechanisms(self):
        """type 만 바꾸면 버전에 따라 일부 위젯만 숨는다."""
        body = self.javascript.split("const apply = () => {", 1)[1].split("\n  };", 1)[0]
        self.assertIn('w.type = "hidden"', body)
        self.assertIn("w.hidden = true", body)

    def test_monitor_is_not_in_either_list(self):
        """모니터 창은 DOM 위젯이라 이름 기반 숨김 대상이 아니다."""
        self.assertNotIn("llmhub_monitor", self.mapping)
        self.assertNotIn("llmhub_monitor", self.advanced)

    def test_model_dropdowns_stay_out_of_advanced(self):
        """사용자가 접은 채로도 모델을 고를 수 있어야 한다."""
        for name in ("lmstudio_model", "claude_model"):
            self.assertNotIn(name, self.advanced)
            self.assertIn(name, self.mapping, f"{name}: 해당 백엔드에서만 보여야 한다")

    def test_dom_widget_must_stay_last_and_not_serialize(self):
        """직렬화되지 않는 위젯이 중간에 끼면 그 뒤 위젯 값이 한 칸씩 밀린다.

        litegraph 의 serialize() 는 전체 배열 인덱스에 쓰고 configure() 는 압축해서
        읽어서 둘이 어긋난다. 맨 끝일 때만 구멍이 배열 끝이라 무해하다.
        """
        self.assertIn("widget.serialize = false", self.javascript)
        # addDOMWidget 은 createPanel 에서 한 번만, 그리고 위젯 재배치가 없어야 한다.
        self.assertEqual(self.javascript.count("addDOMWidget"), 1)
        for forbidden in ("widgets.splice", "widgets.unshift", "widgets.sort"):
            self.assertNotIn(forbidden, self.javascript,
                             f"{forbidden}: 위젯 순서를 바꾸면 저장된 워크플로우가 깨진다")

    def test_toggle_is_reapplied_after_a_saved_workflow_loads(self):
        """configure() 는 위젯 callback 을 부르지 않는다.

        onConfigure 에서 다시 적용하지 않으면 backend=claude 로 저장한 노드가
        lmstudio 위젯을 펼친 채 열린다.
        """
        self.assertIn("onConfigure", self.javascript)
        after = self.javascript.split("onConfigure", 1)[1]
        self.assertIn("_llmhubApplyBackendToggle", after)


if __name__ == "__main__":
    unittest.main()
