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


class TestSaveAndDelete(unittest.TestCase):
    """편집창이 쓰는 경로. 파일을 직접 고치는 것이 아니라 노드에서 저장한다."""

    def test_save_then_load(self):
        with _TempPresets({"presets": []}):
            presets.save_preset("내 프롬프트", "너는 번역가다.")
            self.assertEqual(presets.load_presets()["내 프롬프트"], "너는 번역가다.")

    def test_save_overwrites_the_same_name(self):
        with _TempPresets({"presets": [{"name": "A", "prompt": "옛 내용"}]}):
            presets.save_preset("A", "새 내용")
            self.assertEqual(presets.load_presets(), {"A": "새 내용"})

    def test_multiline_is_stored_as_lines(self):
        """한 줄에 이스케이프를 잔뜩 박아두면 파일을 직접 열었을 때 읽을 수가 없다."""
        with _TempPresets({"presets": []}):
            presets.save_preset("A", "\uccab \uc904\n\ub458\uc9f8 \uc904")
            with open(presets.PRESET_PATH, encoding="utf-8") as fh:
                raw = json.load(fh)
            self.assertEqual(raw["presets"][0]["prompt"], ["\uccab \uc904", "\ub458\uc9f8 \uc904"])
            self.assertEqual(presets.load_presets()["A"], "\uccab \uc904\n\ub458\uc9f8 \uc904")

    def test_other_keys_survive_a_save(self):
        """사람이 파일에 적어둔 메모까지 날려버리면 안 된다."""
        with _TempPresets({"_comment": "손대지 마", "presets": []}):
            presets.save_preset("A", "가")
            with open(presets.PRESET_PATH, encoding="utf-8") as fh:
                raw = json.load(fh)
            self.assertEqual(raw["_comment"], "손대지 마")

    def test_empty_prompt_is_refused(self):
        """빈 프리셋은 불러와도 아무 일이 없다. 나중에 알기보다 지금 거절한다."""
        with _TempPresets({"presets": []}):
            with self.assertRaises(presets.PresetError):
                presets.save_preset("A", "   ")

    def test_blank_name_is_refused(self):
        with _TempPresets({"presets": []}):
            with self.assertRaises(presets.PresetError):
                presets.save_preset("  ", "가")

    def test_reserved_name_is_refused(self):
        """(none) 을 프리셋 이름으로 쓰면 드롭다운의 '안 씀' 항목을 가린다."""
        with _TempPresets({"presets": []}):
            with self.assertRaises(presets.PresetError):
                presets.save_preset(presets.PRESET_NONE, "가")

    def test_long_name_is_refused(self):
        with _TempPresets({"presets": []}):
            with self.assertRaises(presets.PresetError):
                presets.save_preset("가" * (presets.MAX_NAME_LEN + 1), "나")

    def test_delete_removes_only_that_one(self):
        payload = {"presets": [{"name": "A", "prompt": "가"}, {"name": "B", "prompt": "나"}]}
        with _TempPresets(payload):
            remaining = presets.delete_preset("A")
            self.assertEqual(remaining, {"B": "나"})
            self.assertEqual(presets.load_presets(), {"B": "나"})

    def test_delete_unknown_name_says_so(self):
        """조용히 성공시키면 지워진 줄 알고 넘어간다."""
        with _TempPresets({"presets": [{"name": "A", "prompt": "가"}]}):
            with self.assertRaises(presets.PresetError):
                presets.delete_preset("없는이름")

    def test_save_leaves_no_temp_file_behind(self):
        with _TempPresets({"presets": []}):
            presets.save_preset("A", "가")
            self.assertFalse(os.path.exists(presets.PRESET_PATH + ".tmp"))


class TestNodeIgnoresThePreset(unittest.TestCase):
    """프리셋은 화면 전용이다. 생성 시점에 다시 합치면 문장이 두 번 들어간다."""

    def _run(self, **kwargs):
        captured = {}

        class Spy:
            def generate(self, req):
                captured["system"] = req.system_prompt
                return base.LLMResponse(text="x", status="ok")

        with mock.patch.object(nodes_mod, "get_backend", return_value=Spy()):
            nodes_mod.LLMHubGenerate().generate(
                backend="claude", prompt="hi", model="",
                file_access=False, workspace_dir="", temperature=0.7, max_tokens=64,
                timeout_sec=10, stream_view="off", seed=0, **kwargs
            )
        return captured["system"]

    def test_system_prompt_is_passed_through_untouched(self):
        with _TempPresets({"presets": [{"name": "A", "prompt": "프리셋 본문"}]}):
            sent = self._run(system_prompt="화면에서 채워진 내용", system_preset="A")
        self.assertEqual(sent, "화면에서 채워진 내용")

    def test_stale_preset_name_changes_nothing(self):
        with _TempPresets({"presets": []}):
            sent = self._run(system_prompt="내 지시", system_preset="사라진이름")
        self.assertEqual(sent, "내 지시")


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
