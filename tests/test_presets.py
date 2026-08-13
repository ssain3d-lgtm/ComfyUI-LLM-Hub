# -*- coding: utf-8 -*-
"""시스템 프롬프트 프리셋.

사용자가 손으로 고치는 파일이 입력이라, "잘못 적었을 때 어떻게 되는가" 가
기능 자체만큼 중요하다. 오타 하나로 노드가 안 도는 것이 제일 나쁜 결과다.
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

presets = importlib.import_module(f"{_PACK_NAME}.utils.presets")
nodes_mod = importlib.import_module(f"{_PACK_NAME}.nodes")
base = importlib.import_module(f"{_PACK_NAME}.backends.base")


class _TempPresets:
    """PRESET_PATH 를 임시 파일로 갈아끼운다(사용자 파일을 건드리지 않게)."""

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        self.dir = tempfile.TemporaryDirectory()
        path = os.path.join(self.dir.name, "system_prompts.json")
        with open(path, "w", encoding="utf-8") as fh:
            if isinstance(self.payload, str):
                fh.write(self.payload)
            else:
                json.dump(self.payload, fh, ensure_ascii=False)
        self.patches = [
            mock.patch.object(presets, "PRESET_PATH", path),
            # 예제 복사가 끼어들지 않게 한다
            mock.patch.object(presets, "EXAMPLE_PATH", os.path.join(self.dir.name, "none.json")),
        ]
        for p in self.patches:
            p.start()
        presets._CACHE.update(mtime=None, presets={})
        return self

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()
        presets._CACHE.update(mtime=None, presets={})
        self.dir.cleanup()


class TestParsing(unittest.TestCase):
    def test_list_form(self):
        with _TempPresets({"presets": [{"name": "A", "prompt": "가"}]}):
            self.assertEqual(presets.load_presets(), {"A": "가"})

    def test_dict_form_is_also_accepted(self):
        with _TempPresets({"presets": {"A": "가"}}):
            self.assertEqual(presets.load_presets(), {"A": "가"})

    def test_list_of_lines_is_joined_with_newlines(self):
        """JSON 한 줄에 \\n 을 박아 넣는 것보다 손으로 고치기 낫다."""
        with _TempPresets({"presets": [{"name": "A", "prompt": ["첫 줄", "둘째 줄"]}]}):
            self.assertEqual(presets.load_presets()["A"], "첫 줄\n둘째 줄")

    def test_order_is_preserved(self):
        """드롭다운 순서는 파일에 적은 순서여야 한다."""
        entries = [{"name": n, "prompt": "x"} for n in ("셋", "하나", "둘")]
        with _TempPresets({"presets": entries}):
            self.assertEqual(presets.preset_names(), ["(none)", "셋", "하나", "둘"])

    def test_none_is_always_first(self):
        with _TempPresets({"presets": [{"name": "A", "prompt": "가"}]}):
            self.assertEqual(presets.preset_names()[0], presets.PRESET_NONE)

    def test_a_preset_named_none_cannot_shadow_the_placeholder(self):
        with _TempPresets({"presets": [{"name": "(none)", "prompt": "가"}]}):
            self.assertEqual(presets.preset_names(), ["(none)"])


class TestBadInput(unittest.TestCase):
    """손으로 고치는 파일이라 깨질 수 있다. 깨져도 노드는 떠야 한다."""

    def test_broken_json_yields_empty_list_not_a_crash(self):
        with _TempPresets("{{{ 이건 JSON 이 아니다"):
            self.assertEqual(presets.load_presets(), {})
            self.assertEqual(presets.preset_names(), ["(none)"])

    def test_missing_file_yields_empty_list(self):
        with _TempPresets({"presets": []}) as tmp:
            os.remove(presets.PRESET_PATH)
            presets._CACHE.update(mtime=None, presets={})
            self.assertEqual(presets.preset_names(), ["(none)"])

    def test_one_bad_entry_does_not_drop_the_good_ones(self):
        payload = {"presets": [
            {"name": "좋음", "prompt": "가"},
            {"name": "", "prompt": "이름 없음"},
            {"name": "본문 없음"},
            "문자열이 끼어듦",
        ]}
        with _TempPresets(payload):
            self.assertEqual(presets.load_presets(), {"좋음": "가"})

    def test_edits_are_picked_up_without_a_restart(self):
        """파일을 고치고 브라우저만 새로고침하면 반영돼야 한다."""
        with _TempPresets({"presets": [{"name": "A", "prompt": "가"}]}):
            self.assertEqual(presets.preset_names(), ["(none)", "A"])
            with open(presets.PRESET_PATH, "w", encoding="utf-8") as fh:
                json.dump({"presets": [{"name": "B", "prompt": "나"}]}, fh, ensure_ascii=False)
            os.utime(presets.PRESET_PATH, (0, 0))  # mtime 을 확실히 바꾼다
            self.assertEqual(presets.preset_names(), ["(none)", "B"])


class TestResolve(unittest.TestCase):
    def test_none_uses_the_typed_prompt_only(self):
        text, note = presets.resolve(presets.PRESET_NONE, "내가 쓴 것")
        self.assertEqual(text, "내가 쓴 것")
        self.assertEqual(note, "")

    def test_preset_alone(self):
        with _TempPresets({"presets": [{"name": "A", "prompt": "프리셋 본문"}]}):
            text, note = presets.resolve("A", "")
            self.assertEqual(text, "프리셋 본문")
            self.assertIn("A", note)

    def test_preset_first_then_typed(self):
        """프리셋이 입력을 덮어쓰면 방금 타이핑한 것이 조용히 사라진다."""
        with _TempPresets({"presets": [{"name": "A", "prompt": "기본 성격"}]}):
            text, _ = presets.resolve("A", "이번만 짧게")
            self.assertEqual(text, "기본 성격\n\n이번만 짧게")

    def test_unknown_name_is_ignored_not_fatal(self):
        """파일에서 프리셋 이름을 바꿨다고 워크플로우가 안 돌면 안 된다."""
        with _TempPresets({"presets": [{"name": "A", "prompt": "가"}]}):
            text, note = presets.resolve("사라진이름", "내가 쓴 것")
            self.assertEqual(text, "내가 쓴 것")
            self.assertIn("사라진이름", note)

    def test_empty_everything(self):
        self.assertEqual(presets.resolve("", ""), ("", ""))


class TestNodeWiring(unittest.TestCase):
    def test_widget_exists_and_is_last(self):
        spec = nodes_mod.LLMHubGenerate.INPUT_TYPES()
        self.assertIn("system_preset", spec["optional"])
        names = list(spec["optional"])
        self.assertEqual(names[-1], "system_preset",
                         "맨 뒤가 아니면 예전 워크플로우의 widgets_values 가 밀린다")

    def test_dropdown_starts_with_none(self):
        spec = nodes_mod.LLMHubGenerate.INPUT_TYPES()
        self.assertEqual(spec["optional"]["system_preset"][0][0], presets.PRESET_NONE)

    def test_validate_inputs_lets_a_stale_preset_through(self):
        """목록에서 사라진 이름 때문에 ComfyUI 검증이 실행을 막으면 안 된다."""
        self.assertTrue(
            nodes_mod.LLMHubGenerate.VALIDATE_INPUTS(system_preset="사라진이름")
        )

    def test_node_applies_the_preset(self):
        captured = {}

        class Spy:
            def generate(self, req):
                captured["system"] = req.system_prompt
                return base.LLMResponse(text="x", status="ok")

        with _TempPresets({"presets": [{"name": "A", "prompt": "프리셋 본문"}]}):
            with mock.patch.object(nodes_mod, "get_backend", return_value=Spy()):
                out = nodes_mod.LLMHubGenerate().generate(
                    backend="claude", prompt="hi", system_prompt="덧붙임", model="",
                    file_access=False, workspace_dir="", temperature=0.7, max_tokens=64,
                    timeout_sec=10, stream_view="off", seed=0, system_preset="A",
                )
        self.assertEqual(captured["system"], "프리셋 본문\n\n덧붙임")
        self.assertIn("preset", out["result"][2])

    def test_old_workflows_without_the_widget_still_run(self):
        """이 입력이 없던 시절에 저장한 워크플로우도 그대로 돌아야 한다."""
        class Spy:
            def generate(self, req):
                return base.LLMResponse(text="x", status="ok")

        with mock.patch.object(nodes_mod, "get_backend", return_value=Spy()):
            out = nodes_mod.LLMHubGenerate().generate(
                backend="claude", prompt="hi", system_prompt="", model="",
                file_access=False, workspace_dir="", temperature=0.7, max_tokens=64,
                timeout_sec=10, stream_view="off", seed=0,
            )
        self.assertEqual(out["result"][1], "ok")


class TestShippedExample(unittest.TestCase):
    def test_example_file_is_valid_and_parses(self):
        """배포되는 예제가 깨져 있으면 첫 실행부터 프리셋이 비어 보인다."""
        with open(presets.EXAMPLE_PATH, "r", encoding="utf-8") as fh:
            parsed = presets._parse(json.load(fh))
        self.assertGreaterEqual(len(parsed), 3)
        for name, text in parsed.items():
            self.assertTrue(text.strip(), f"{name}: 본문이 비었다")

    def test_example_is_not_gitignored_but_the_real_file_is(self):
        with open(os.path.join(_PACK_ROOT, ".gitignore"), encoding="utf-8") as fh:
            # 주석은 패턴이 아니다. 걷어내지 않으면 주석에 적힌 파일 이름이
            # 무시 대상으로 잡힌다.
            patterns = [line.strip() for line in fh
                        if line.strip() and not line.strip().startswith("#")]
        self.assertIn("system_prompts.json", patterns)
        self.assertNotIn("system_prompts.example.json", patterns)


if __name__ == "__main__":
    unittest.main(verbosity=2)
