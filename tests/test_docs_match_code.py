# -*- coding: utf-8 -*-
"""문서가 코드를 따라가는지 검사한다.

3인 검토에서 README 가 코드와 어긋난 곳이 여럿 나왔다. 전부 "예전엔 맞았는데
코드만 바뀐" 것들이라, 사람이 다시 읽어서 잡기를 기대하는 대신 몇 가지는
기계가 잡게 한다.

여기서 문장 전체를 검사하지는 않는다 -- 문서 톤까지 테스트로 묶으면 글을 못
고친다. 대신 "이름" 과 "키" 처럼 코드에서 뽑아낼 수 있는 것만 대조한다.
"""

from __future__ import annotations

import importlib
import io
import json
import os
import re
import sys
import unittest

_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_PACK_ROOT))
_PACK_NAME = os.path.basename(_PACK_ROOT)

nodes_mod = importlib.import_module(f"{_PACK_NAME}.nodes")
backends_mod = importlib.import_module(f"{_PACK_NAME}.backends")


def _read(name):
    with io.open(os.path.join(_PACK_ROOT, name), encoding="utf-8") as fh:
        return fh.read()


class TestConfigSample(unittest.TestCase):
    """README 의 config.json 예시가 실제 파일과 같은 키를 가져야 한다.

    실제로 어긋나 있었다: §2-1 이 "openai_compat.api_token 을 설정하라" 고
    안내하는데 정작 §6 의 예시에는 그 섹션이 없었다. 예시를 그대로 복사한
    사람은 그 키와 allow_unsafe_extra_args 를 잃는다.
    """

    def _blocks(self):
        readme = _read("README.md")
        return [
            b for b in re.findall(r"```json\n(.*?)\n```", readme, re.S)
            if '"lmstudio"' in b
        ]

    def test_both_languages_have_a_sample(self):
        self.assertEqual(len(self._blocks()), 2, "영문/국문 각각 하나여야 한다")

    def test_samples_match_the_real_example_file(self):
        example = json.loads(_read("config.example.json"))
        for index, block in enumerate(self._blocks(), 1):
            with self.subTest(block=index):
                doc = json.loads(block)
                self.assertEqual(
                    sorted(doc), sorted(example),
                    "README 의 예시와 config.example.json 의 최상위 키가 다르다",
                )


class TestBackendNamesAreDocumented(unittest.TestCase):
    def test_every_backend_appears_in_the_readme(self):
        """드롭다운에 있는데 문서에 한 번도 안 나오는 백엔드가 없어야 한다."""
        readme = _read("README.md")
        for name in backends_mod.BACKEND_NAMES:
            self.assertIn(name, readme, f"{name} 이 README 에 없다")


class TestWidgetNamesAreDocumented(unittest.TestCase):
    def test_every_widget_appears_in_the_readme(self):
        """§3 입력 표에 빠진 위젯이 없어야 한다.

        위젯을 추가하면서 표를 잊는 것이 이 저장소의 반복 패턴이었다.
        """
        readme = _read("README.md")
        # 프론트엔드가 만들어 붙이는 짝꿍이라 INPUT_TYPES 에 없다.
        skip = {"control_after_generate"}
        for name in nodes_mod.WIDGET_ORDER:
            if name in skip:
                continue
            with self.subTest(widget=name):
                self.assertIn(f"`{name}`", readme, f"{name} 이 README 에 없다")


class TestAdvertisedTestCount(unittest.TestCase):
    """README 가 말하는 테스트 개수가 실제와 같아야 한다.

    이 숫자는 손으로 고쳐왔고 계속 어긋났다(한때 README 389 / 릴리스 노트 350 /
    실제 408 이 동시에 돌아다녔다). 신뢰하라고 적어둔 숫자가 틀려 있으면
    안 적느니만 못하다. 세는 일을 기계에 넘긴다.

    테스트를 추가하면 이 테스트가 깨진다 -- 그게 의도다. README 의 숫자 한 줄을
    같이 고치라는 뜻이다.
    """

    def _actual(self):
        loader = unittest.TestLoader()
        suite = loader.discover(
            start_dir=os.path.join(_PACK_ROOT, "tests"),
            pattern="test_*.py",
            top_level_dir=os.path.join(_PACK_ROOT, "tests"),
        )
        self.assertFalse(loader.errors, f"테스트 수집 중 오류: {loader.errors}")
        return suite.countTestCases()

    def _advertised(self):
        readme = _read("README.md")
        return [int(n) for n in re.findall(r"\*\*(\d+) tests, all passing", readme)] + \
               [int(n) for n in re.findall(r"\*\*(\d+)종이며", readme)]

    def test_both_languages_state_a_number(self):
        self.assertEqual(len(self._advertised()), 2, "영문/국문 각각 하나여야 한다")

    def test_the_number_is_the_real_one(self):
        actual = self._actual()
        for stated in self._advertised():
            self.assertEqual(
                stated, actual,
                f"README 는 {stated}개라고 하는데 실제는 {actual}개다. "
                "README 의 숫자를 고쳐라.",
            )


class TestSeedTooltipMatchesTheCode(unittest.TestCase):
    """툴팁이 코드와 반대되는 말을 하고 있었다.

    seed 를 실제로 서버에 보내도록 바꾸면서 README 만 고치고 툴팁을 안 고쳤다.
    사용자가 캔버스에서 읽는 것은 툴팁 쪽이다.
    """

    def _seed_tooltip(self):
        spec = nodes_mod.LLMHubGenerate.INPUT_TYPES()
        return spec["required"]["seed"][1]["tooltip"]

    def test_it_does_not_claim_the_value_is_unused(self):
        self.assertNotIn("never used", self._seed_tooltip())

    def test_it_says_the_value_reaches_the_server(self):
        tooltip = self._seed_tooltip()
        self.assertIn("sampling seed", tooltip)
        # 어느 백엔드에서만 통하는지도 말해야 한다.
        self.assertIn("lmstudio", tooltip)

    def test_the_code_really_sends_it(self):
        """툴팁만 고치고 코드가 도로 바뀌면 반대 방향으로 어긋난다."""
        self.assertIn('payload["seed"]', _read("backends/lmstudio.py"))


if __name__ == "__main__":
    unittest.main()
