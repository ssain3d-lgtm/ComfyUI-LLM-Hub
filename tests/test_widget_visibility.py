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

    def _advanced_for(self):
        """JS 의 ADVANCED_FOR 리터럴을 {위젯: [백엔드...]} 로 읽는다."""
        body = self.javascript.split("const ADVANCED_FOR = {", 1)[1].split("\n};", 1)[0]
        return {
            name: re.findall(r'"([^"]+)"', names)
            for name, names in re.findall(r"(\w+)\s*:\s*\[([^\]]*)\]", body)
        }

    def test_address_box_is_folded_away_for_the_preset_backends(self):
        """ollama/vllm/llamacpp 는 고른 순간 주소가 잡힌다.

        그런데도 빈 주소 칸이 눈에 띄는 자리에 남아 있으면 "여기를 채워야
        도는구나" 로 읽힌다 -- 안 채워도 도는데.
        """
        aliases = set(backends_mod.OPENAI_COMPAT_ALIASES)
        folded = set(self._advanced_for().get("openai_base_url", []))
        self.assertEqual(folded, aliases)

    def test_address_box_stays_visible_for_plain_openai_compat(self):
        """이쪽은 미리 잡아둔 주소가 없다. 접어버리면 어디에 붙일지 알 방법이 없다."""
        self.assertNotIn(
            "openai_compat", self._advanced_for().get("openai_base_url", [])
        )
        self.assertNotIn("openai_base_url", self.advanced)

    def test_advanced_for_names_real_widgets_and_backends(self):
        known = set(backends_mod.BACKEND_NAMES)
        for widget, names in self._advanced_for().items():
            self.assertIn(widget, self.widget_names, widget)
            self.assertEqual(sorted(set(names) - known), [], widget)

    def test_advanced_for_widgets_are_visible_for_those_backends(self):
        """BACKEND_ONLY 로 이미 숨는 위젯을 ADVANCED_FOR 에 또 넣으면,
        펼쳐도 안 나타나는데 접힌 것처럼 보이는 유령 설정이 된다."""
        for widget, names in self._advanced_for().items():
            allowed = self.mapping.get(widget)
            if allowed is None:
                continue
            self.assertEqual(
                sorted(set(names) - set(allowed)), [],
                f"{widget}: 이 백엔드에서는 애초에 안 보인다",
            )

    def _body(self, declaration):
        after = self.javascript.split(declaration, 1)[1]
        return after.split("\n}", 1)[0]

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
        """접든 펴든, 어느 백엔드든 이것들은 보여야 한다.

        system_prompt 는 여기서 빠졌다. 노드의 작은 칸 대신 ✎ 편집창에서 쓰고
        저장하는 방식으로 바꿨기 때문이다. 대신 아래 세 테스트가 "그래도 닿을
        수 있는가" 를 대신 지킨다 -- 그게 원래 이 테스트가 지키려던 것이다.
        """
        for name in ("backend", "prompt"):
            self.assertNotIn(name, self.mapping, f"{name}: BACKEND_ONLY 에 있으면 안 된다")
            self.assertNotIn(name, self.advanced, f"{name}: ADVANCED 에 있으면 안 된다")

    def test_system_prompt_is_still_reachable_by_unfolding(self):
        """접어두는 것과 없애는 것은 다르다.

        편집창이 안 뜨는 상황(프론트엔드 버전 차이, JS 오류)에서도 값을 보고
        고칠 길이 남아 있어야 한다. ▾ 로 펼치면 나오는 상태가 그 안전판이다.
        """
        self.assertIn("system_prompt", self.advanced)
        self.assertNotIn(
            "system_prompt", self.mapping,
            "BACKEND_ONLY 에도 들어가면 어떤 백엔드에서는 펼쳐도 안 나온다",
        )

    def test_the_prompt_box_gets_the_freed_space(self):
        """system_prompt 를 접었으면 그 자리는 prompt 가 받아야 한다.

        접기만 하고 끝내면 노드만 작아진다 -- 정작 매번 쓰는 칸은 그대로다.
        """
        body = self._body("function applyPromptSize")
        # 구/신 프론트엔드가 서로 다른 쪽을 본다. 한쪽만 걸면 버전에 따라 무시된다.
        self.assertIn("widget.computeSize", body)
        self.assertIn("widget.computeLayoutSize", body)
        self.assertIn("PROMPT_MIN_HEIGHT", body)

    def test_the_prompt_size_is_not_wiped_when_widgets_are_reshown(self):
        """보이게 만드는 자리에서 computeSize 를 지운다. 거기서 다시 안 주면
        고급 옵션을 한 번 접었다 펴는 순간 프롬프트 칸이 원래대로 줄어든다."""
        body = self._body("const apply = () =>")
        self.assertIn('if (w.name === "prompt") applyPromptSize(w)', body)

    def test_reshowing_restores_the_dom_widget_height_instead_of_wiping_it(self):
        """멀티라인 칸을 되살릴 때는 대입이 아니라 delete 여야 한다.

        멀티라인 칸(system_prompt / extra_body)은 이 프론트엔드에서 DOMWidgetImpl
        인스턴스이고, 높이를 프로토타입 메서드 computeLayoutSize 로 스스로 잰다
        (요소의 --comfy-widget-min-height 를 읽는다, 1.49.6 번들에서 확인).

        인스턴스에 `= undefined` 를 대입하면 그 프로토타입 메서드가 가려진다.
        레이아웃은 `if (w.computeLayoutSize)` 로 truthy 검사만 하므로 falsy 가 되고,
        고급 옵션을 펼친 순간 그 칸들이 제 높이를 못 받아 윗부분이 잘린다.
        prompt 만 멀쩡했던 건 바로 다음 줄에서 다시 넣어줬기 때문이다.

        delete 는 인스턴스 속성만 지워 프로토타입이 다시 보이게 한다.
        """
        body = self._body("const apply = () =>")
        self.assertIn("delete w.computeSize", body)
        self.assertIn("delete w.computeLayoutSize", body)
        self.assertNotIn(
            "w.computeLayoutSize = undefined", body,
            "대입은 프로토타입 메서드를 가린다 — DOM 위젯이 높이를 잃는다",
        )

    def test_the_editor_button_exists_as_the_main_path(self):
        """칸을 접었으니 편집창이 주 경로가 된다. 그게 없으면 그냥 잃은 것이다."""
        spec = self.javascript.split('key: "prompt"', 1)[1].split("},", 1)[0]
        self.assertIn("openPromptEditor(node)", spec)

    def test_a_set_system_prompt_is_visible_at_a_glance(self):
        """칸이 접혀 있으면 "들어 있는지" 가 화면에서 사라진다.

        저장해둔 워크플로우를 열었을 때 빈 것처럼 보이면, 시스템 프롬프트가
        걸린 줄 모르고 결과가 왜 이러지 하게 된다.
        """
        spec = self.javascript.split('key: "prompt"', 1)[1].split("},", 1)[0]
        self.assertIn("hasSystemPrompt(node)", spec)
        body = self._body("function hasSystemPrompt")
        self.assertIn('w.name === "system_prompt"', body)
        self.assertIn("trim()", body)

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


class TestAdvancedButton(unittest.TestCase):
    """고급 옵션 접기/펴기 버튼 (타이틀 바에 직접 그린다)."""

    def setUp(self):
        self.javascript = _javascript()
        # 주석을 걷어낸 코드. "addWidget 을 쓰지 않는다" 는 주석이 그 검사에
        # 스스로 걸리는 것을 막는다(예전 shell=True 검사에서 겪은 그 함정).
        self.code = "\n".join(
            line.split("//", 1)[0] for line in self.javascript.splitlines()
        )

    def test_button_is_not_a_widget(self):
        """addWidget 으로 만들면 widgets_values 자리를 차지한다.

        중간에 한 칸이 끼면 이 노드로 저장해둔 예전 워크플로우의 값이 전부
        밀린다. 그래서 캔버스에 직접 그린다.
        """
        self.assertNotIn("addWidget", self.code)
        self.assertIn("onDrawForeground", self.code)

    def test_click_blocks_node_drag(self):
        """onMouseDown 이 true 를 안 돌려주면 버튼을 누를 때 노드가 딸려 움직인다."""
        body = self.javascript.split("nodeType.prototype.onMouseDown = function", 1)[1]
        body = body.split("\n    };", 1)[0]
        self.assertIn("hitButton", body)
        self.assertIn("return true", body)

    def test_right_click_menu_survives_as_a_fallback(self):
        """프론트엔드 버전에 따라 onMouseDown 이 안 불릴 수 있다.

        그때 조작 수단이 통째로 사라지면 고급 옵션을 영영 못 연다.
        """
        self.assertIn("getExtraMenuOptions", self.javascript)
        self.assertIn("Hide advanced options", self.javascript)

    def test_both_paths_share_one_toggle(self):
        """버튼과 메뉴가 각자 상태를 뒤집으면 한쪽이 어긋난다.

        호출 횟수를 세는 대신 "상태를 뒤집는 곳" 을 센다. 그게 진짜 지켜야 할
        규칙이고, 호출 방식을 바꿔도 이 테스트는 계속 유효하다.
        """
        self.assertEqual(self.javascript.count("function toggleAdvanced"), 1)
        # 기본값 초기화 1곳 + toggleAdvanced 안 1곳. 그 외에서 직접 뒤집으면 안 된다.
        # `=` 뒤에 `=` 가 오면 비교문(=== undefined)이지 대입이 아니다.
        writes = re.findall(
            r"properties\[SHOW_ADVANCED_PROP\]\s*=(?!=)", self.code
        )
        self.assertEqual(len(writes), 2, "고급 상태를 직접 바꾸는 곳이 늘었다")
        # 두 진입점이 모두 살아 있어야 한다
        self.assertIn("toggleAdvanced(node)", self.javascript)   # 타이틀 바 버튼
        self.assertIn("toggleAdvanced(this)", self.javascript)   # 우클릭 메뉴

    def test_hover_is_cleared_on_leave(self):
        """노드 밖으로 나가면 onMouseMove 가 안 불린다. 안 꺼주면 강조된 채 굳는다."""
        self.assertIn("onMouseLeave", self.javascript)
        after = self.javascript.split("nodeType.prototype.onMouseLeave", 1)[1]
        self.assertIn("= null", after.split("\n    };", 1)[0])

    def test_button_hides_when_the_node_is_collapsed(self):
        """접힌 노드는 본문이 없다. 계속 그리면 타이틀 위에 유령이 남는다."""
        for func in ("function drawTitleButtons", "function hitButton"):
            body = self.javascript.split(func, 1)[1].split("\n}", 1)[0]
            self.assertIn("flags?.collapsed", body, f"{func}: 접힘 검사가 없다")

    def test_buttons_do_not_overlap(self):
        """오른쪽 끝부터 폭+간격만큼 물러나며 놓아야 겹치지 않는다."""
        body = self.javascript.split("function buttonRects", 1)[1].split("\n}", 1)[0]
        self.assertIn("right -= BUTTON_WIDTH + BUTTON_GAP", body)

    def test_buttons_are_narrow_enough_to_leave_the_title_visible(self):
        """글자 라벨을 쓰면 둘이 190px 을 먹어 노드 제목을 덮는다 (실제로 겪음).

        아이콘만 쓰면 50px 이면 된다. 여기서 폭을 다시 키우면 같은 증상이
        돌아오므로 상한을 걸어둔다.
        """
        numbers = {}
        for name in ("BUTTON_WIDTH", "BUTTON_MARGIN", "BUTTON_GAP"):
            found = re.search(rf"const {name} = (\d+);", self.javascript)
            self.assertIsNotNone(found, f"{name} 를 못 읽었다")
            numbers[name] = int(found.group(1))
        count = self.javascript.split("const TITLE_BUTTONS = [", 1)[1] \
                               .split("\n];", 1)[0].count("key:")
        total = (numbers["BUTTON_WIDTH"] * count
                 + numbers["BUTTON_GAP"] * (count - 1)
                 + numbers["BUTTON_MARGIN"])
        self.assertLessEqual(total, 80, f"버튼 줄이 {total}px 이라 제목을 덮는다")

    def test_icon_only_buttons_explain_themselves_on_hover(self):
        """아이콘만으로는 무슨 버튼인지 알 수 없다.

        캔버스라 HTML title= 툴팁을 못 쓰므로 직접 그려야 한다.
        """
        self.assertIn("function drawButtonHint", self.javascript)
        body = self.javascript.split("const TITLE_BUTTONS = [", 1)[1].split("\n];", 1)[0]
        self.assertEqual(body.count("icon:"), body.count("key:"))
        self.assertEqual(body.count("hint:"), body.count("key:"))


class TestMonitorPanel(unittest.TestCase):
    """모니터 창 자체의 표시/숨김과 부가 버튼."""

    def setUp(self):
        self.javascript = _javascript()

    def test_off_hides_the_panel_three_ways(self):
        """DOM 위젯은 일반 위젯과 숨기는 방법이 다르고, 프론트엔드마다 보는 게 다르다.

        하나만 걸면 어떤 버전에서는 빈 패널이 240px 를 그대로 차지한다 —
        stream_view 를 끄는 이유가 보통 '노드를 작게' 인데 앞뒤가 안 맞는다.
        """
        body = self.javascript.split("function applyMonitorVisibility", 1)[1]
        body = body.split("\n}", 1)[0]
        self.assertIn("element.style.display", body)
        self.assertIn("widget.hidden", body)
        self.assertIn("computeLayoutSize", body)
        self.assertIn("computeSize", body)

    def test_off_also_shrinks_the_node(self):
        """패널만 숨고 노드 높이가 그대로면 빈칸이 남는다."""
        body = self.javascript.split('w.name === "stream_view"', 1)[1]
        body = body.split("\n      }", 1)[0]
        self.assertIn("applyMonitorVisibility", body)
        self.assertIn("resizeToWidgets", body)

    def test_visibility_survives_a_workflow_reload(self):
        """저장된 워크플로우를 열 때도 적용돼야 off 로 저장한 노드가 작게 열린다."""
        body = self.javascript.split("const apply = () => {", 1)[1].split("\n  };", 1)[0]
        self.assertIn("applyMonitorVisibility", body)

    def test_copy_button_has_a_non_secure_context_fallback(self):
        """navigator.clipboard 는 http://<LAN IP> 로 열면 아예 없다.

        localhost 만 예외다. 폴백이 없으면 LAN 으로 접속하는 사람에게는
        복사 버튼이 그냥 안 먹는 버튼이 된다.
        """
        self.assertIn("navigator.clipboard", self.javascript)
        self.assertIn("execCommand", self.javascript)

    def test_copy_never_writes_an_empty_clipboard(self):
        """빈 값을 쓰면 클립보드에 들어 있던 것이 조용히 지워진다."""
        body = self.javascript.split('copyEl.addEventListener("click"', 1)[1]
        body = body.split("\n  });", 1)[0]
        self.assertIn("if (!text)", body)

    def test_buttons_never_shrink_or_wrap(self):
        """상태 문구가 길면 버튼이 눌려서 "복/사" 처럼 세로로 접힌다 (실제로 겪음).

        flex 기본값이 shrink:1 이라 글자 하나 너비까지 줄어든다. 도구 이름에
        파일 경로가 붙는 실행에서 재현됐다.
        """
        body = self.javascript.split(".llmhub-copy, .llmhub-stop {", 1)[1]
        body = body.split("\n}", 1)[0]
        self.assertIn("flex: 0 0 auto", body)
        self.assertIn("white-space: nowrap", body)

    def test_status_truncates_instead_of_pushing_buttons(self):
        """줄바꿈을 허용하면 생성 중에 헤더 높이가 들쭉날쭉해진다."""
        body = self.javascript.split(".llmhub-status {", 1)[1].split("\n}", 1)[0]
        # min-width:0 이 없으면 flex 항목이 내용보다 작아지지 않아 말줄임이 안 걸린다
        self.assertIn("min-width: 0", body)
        self.assertIn("text-overflow: ellipsis", body)
        # 잘린 내용을 볼 방법이 있어야 한다
        self.assertIn("statusEl.title", self.javascript)

    def test_panel_buttons_do_not_drag_the_node(self):
        """캔버스가 클릭을 먼저 삼키면 버튼이 눌리지 않고 노드만 움직인다."""
        body = self.javascript.split("캔버스가 이 클릭을", 1)[1].split("\n  }", 1)[0]
        for element in ("stopEl", "copyEl"):
            self.assertIn(f"{element}.addEventListener", body)
        self.assertIn("stopPropagation", body)


class TestFailureIsVisible(unittest.TestCase):
    """결과 없이 끝났을 때 사용자가 이유를 볼 수 있어야 한다.

    상태 줄(.llmhub-status)은 한 줄로 잘린다 -- 바로 옆 TestMonitorPanel 의
    test_status_truncates_instead_of_pushing_buttons 가 그렇게 되도록 지키고
    있다. 그래서 실패 사유를 거기에만 넣으면 69자짜리 메시지가 잘려나가고,
    본문은 빈 채로 남아 "아무 일도 안 일어났다" 처럼 보인다.

    실측(2026-08-16): file_access 를 켜고 workspace_dir 을 비운 채 세 번
    연속 실행했는데 화면에 아무 것도 안 나왔다. 원인은 서버의 /history 를
    뒤져서야 나왔다 -- 사용자가 할 수 있는 일이 아니다.
    """

    def setUp(self):
        self.javascript = _javascript()

    def _promotion_pattern(self):
        """JS 의 noticeFor 판정식을 그대로 파이썬 정규식으로 가져온다."""
        found = re.search(
            r"return /\^\(([^)]+)\)\\b/i\.test\(text\)", self.javascript
        )
        self.assertIsNotNone(found, "noticeFor 의 판정식을 못 읽었다 — 구조가 바뀌었나?")
        return re.compile(r"^(" + found.group(1) + r")\b", re.I)

    def test_real_failure_text_is_promoted_to_the_body(self):
        """파이썬이 실제로 만드는 문구로 판정한다.

        문구 쪽을 고치면서 접두사를 바꾸면 화면에서 조용히 사라진다.
        그때 여기서 걸린다.
        """
        base_mod = importlib.import_module(f"{_PACK_NAME}.backends.base")
        req = base_mod.LLMRequest("lmstudio", "", "", "안녕", file_access=True,
                                  workspace_dir="")
        message = base_mod.validate_workspace(req)
        self.assertTrue(message, "이 설정은 실패해야 한다(전제가 깨졌다)")
        self.assertRegex(message, self._promotion_pattern())

    def test_user_stop_is_promoted_too(self):
        """중단도 빈 화면과 구별되어야 한다. 문구는 nodes.py 에서 가져온다.

        예전에는 소스를 정규식으로 긁었는데, 대입 변수 이름을 바꾸자 문구를 못
        찾고 테스트가 깨졌다(문구 자체는 멀쩡했다). 상수를 직접 읽는다.
        """
        self.assertRegex(nodes_mod.STOPPED_STATUS, self._promotion_pattern())

    def test_batch_statuses_are_promoted_too(self):
        """one_per_image 요약 문구도 같은 규칙을 지켜야 한다.

        40장 중 3장이 실패했는데 화면이 조용하면, 빈 캡션 3개가 그대로 저장된다.
        """
        pattern = self._promotion_pattern()
        failed = nodes_mod._batch_status(["ok", "error: boom", "ok"], 3)
        self.assertRegex(failed, pattern)
        halted = nodes_mod._batch_status(["ok"], 3)
        self.assertRegex(halted, pattern)
        # 전부 성공한 경우는 올리면 안 된다 — 본문에 크게 뜨면 답으로 읽힌다.
        self.assertNotRegex(nodes_mod._batch_status(["ok", "ok"], 2), pattern)

    def test_success_is_not_promoted(self):
        """"ok" 를 본문에 크게 써두면 그게 모델이 낸 답처럼 보인다."""
        pattern = self._promotion_pattern()
        for status in ("ok", "", "Done", "Generating…"):
            self.assertNotRegex(status, pattern, f"{status!r} 를 본문에 올리면 안 된다")

    def test_notice_is_not_mistaken_for_an_answer(self):
        """색이 같으면 사용자는 실패 문구를 답으로 읽는다."""
        self.assertIn(".llmhub-body.llmhub-notice", self.javascript)

    def test_notice_does_not_leak_into_the_next_run(self):
        """지난 실행의 붉은 문구가 남으면 이번 결과가 실패처럼 보인다."""
        self.assertIn("this.lastIsNotice = false", self.javascript)  # clear()
        self.assertIn("control.lastIsNotice = false", self.javascript)  # 본문 도착 시


class TestConnectButton(unittest.TestCase):
    """LM Studio 모델 목록 재조회 (타이틀 바의 ⟳ Connect).

    ComfyUI 가 LM Studio 보다 먼저 뜨면 lmstudio_model 목록이 "(auto)" 하나로
    굳는다. 사용자에게 보이는 증상은 "드롭다운이 안 열린다" 뿐이라 원인을 알
    길이 없다 — 실제로 로그를 뒤져서야 찾았다. 여기서 그 복구 경로를 지킨다.
    """

    def setUp(self):
        self.javascript = _javascript()
        self.code = "\n".join(
            line.split("//", 1)[0] for line in self.javascript.splitlines()
        )

    def _body(self, declaration):
        after = self.javascript.split(declaration, 1)[1]
        return after.split("\n}", 1)[0]

    def test_auto_model_constant_matches_python(self):
        """JS 와 nodes.py 가 다른 문자열을 쓰면 "목록이 비었다" 판정이 깨진다.

        조용히 깨진다는 게 문제다 -- 버튼은 "1 models" 라고 자랑스럽게 답하고,
        정작 목록에는 (auto) 하나뿐이다.
        """
        found = re.search(r'const AUTO_MODEL = "([^"]+)"', self.javascript)
        self.assertIsNotNone(found, "JS 에서 AUTO_MODEL 을 못 읽었다")
        self.assertEqual(found.group(1), nodes_mod.AUTO_MODEL)

    def _widget_names(self):
        spec = nodes_mod.LLMHubGenerate.INPUT_TYPES()
        return set(spec.get("required", {})) | set(spec.get("optional", {}))

    def _model_widgets(self):
        body = self.javascript.split("const MODEL_WIDGETS = [", 1)[1].split("]", 1)[0]
        return re.findall(r'"([^"]+)"', body)

    def _model_widget_for(self):
        body = self.javascript.split("const MODEL_WIDGET_FOR = {", 1)[1].split("\n};", 1)[0]
        return dict(re.findall(r'(\w+)\s*:\s*"([^"]+)"', body))

    def test_target_widgets_actually_exist(self):
        """이름에 오타가 나면 아무 일도 안 일어난다(에러도 없이)."""
        names = self._widget_names()
        self.assertTrue(self._model_widgets(), "MODEL_WIDGETS 를 못 읽었다")
        for widget in self._model_widgets():
            self.assertIn(widget, names, widget)

    def test_every_dropdown_is_reachable_from_some_backend(self):
        """새로 받아오기만 하고 아무도 안 보는 드롭다운은 죽은 코드다."""
        self.assertEqual(
            set(self._model_widget_for().values()), set(self._model_widgets())
        )

    def test_model_widget_map_names_real_backends(self):
        known = set(backends_mod.BACKEND_NAMES)
        unknown = sorted(set(self._model_widget_for()) - known)
        self.assertEqual(unknown, [], f"존재하지 않는 백엔드: {unknown}")

    def test_the_map_agrees_with_what_is_actually_shown(self):
        """MODEL_WIDGET_FOR 가 BACKEND_ONLY 와 어긋나면, 새로고침 버튼이 그
        백엔드에서 "안 보이는 드롭다운" 을 세어 보고하게 된다."""
        mapping = _backend_only_map(self.javascript)
        for backend, widget in self._model_widget_for().items():
            allowed = mapping.get(widget)
            if allowed is None:
                continue
            self.assertIn(backend, allowed, f"{backend}: {widget} 이 안 보이는데 센다")

    def test_no_new_server_route(self):
        """ComfyUI 코어의 /object_info 를 다시 받는다.

        파이썬에 라우트를 추가하면 이 기능을 쓰려고 ComfyUI 를 재시작해야 한다.
        고치려는 증상 자체가 "재시작 순서" 문제인데 그건 앞뒤가 안 맞는다.
        """
        self.assertIn("/object_info/${NODE_NAME}", self.javascript)
        self.assertNotIn("/llmhub/models", self.javascript)

    def test_selected_model_is_never_reset(self):
        """목록만 갈아끼우고 고른 값은 건드리지 않는다.

        목록에 없는 이름이어도 파이썬의 VALIDATE_INPUTS 가 통과시키므로 실행에는
        지장이 없다. 반대로 여기서 (auto) 로 되돌리면 사용자가 골라둔 모델이
        조용히 바뀐다 -- 그게 목록이 비어 있는 것보다 나쁘다.
        """
        body = self._body("function applyModelList")
        self.assertIn("options.values = values", body)
        # `.values =` 는 걸리지 않는다(뒤에 s 가 오므로 \s*= 와 안 맞는다).
        self.assertEqual(
            re.findall(r"\.value\s*=(?!=)", body), [], "고른 값을 덮어쓰고 있다"
        )

    def test_every_node_is_updated_not_just_the_clicked_one(self):
        """목록은 전역이다. 누른 노드만 고치면 나머지는 낡은 채로 남는다."""
        body = self._body("function applyModelList")
        self.assertIn("app.graph?._nodes", body)

    def test_one_request_even_with_many_nodes(self):
        """노드가 5개라고 5번 물어보면, LM Studio 가 꺼져 있을 때 그만큼 멎는다."""
        body = self._body("function fetchModelList")
        self.assertIn("if (modelFetchInFlight) return modelFetchInFlight", body)

    def test_auto_refresh_gives_up_after_one_try(self):
        """LM Studio 를 아예 안 쓰는 사람이 페이지를 열 때마다 멎으면 안 된다."""
        body = self._body("function maybeAutoRefresh")
        self.assertIn("if (autoRefreshTried) return", body)
        self.assertIn("autoRefreshTried = true", body)

    def test_empty_list_is_not_reported_as_success(self):
        """LM Studio 가 꺼져 있어도 /object_info 는 200 을 준다.

        목록만 비어서 온다. 그래서 "응답 없음" 은 예외로 안 잡히고, (auto) 를
        걸러낸 개수로 판별해야 한다.
        """
        body = self._body("function refreshModels")
        self.assertIn("v !== AUTO_MODEL", body)

    def test_result_is_shown_without_hovering(self):
        """아이콘 버튼이라 라벨을 바꿔서 결과를 알릴 자리가 없다.

        그래서 호버 설명 자리를 빌려 쓰는데, 방금 누른 사람은 마우스를 그 위에
        올리고 있지 않을 수 있다. notice 가 호버보다 우선해야 하는 이유다.
        """
        body = self._body("function drawTitleButtons")
        self.assertIn("r.spec.notice?.(node)", body)
        # notice 가 먼저, 호버는 그 다음
        self.assertLess(body.index("if (notice)"), body.index("else if (hover"))

    def test_button_only_appears_where_there_is_a_dropdown(self):
        """CLI 3종에는 새로 받아올 목록 자체가 없다. 버튼이 할 일이 없다."""
        spec = self.javascript.split('key: "connect"', 1)[1].split("},", 1)[0]
        self.assertIn("visible: hasModelDropdown", spec)

        body = self._body("function hasModelDropdown")
        self.assertIn("modelWidgetFor(node)", body)

        # 판정의 근거가 MODEL_WIDGET_FOR 이므로, CLI 3종이 거기 없어야 숨는다.
        mapped = set(self._model_widget_for())
        for cli in ("claude", "codex", "gemini"):
            self.assertNotIn(cli, mapped, f"{cli}: 새로고침할 목록이 없다")
        self.assertIn("lmstudio", mapped)

    def test_hidden_button_is_also_unclickable(self):
        """그리기와 클릭 판정이 갈리면 "안 보이는데 눌리는" 자리가 생긴다.

        둘 다 buttonRects 를 쓰므로, 거르는 곳이 거기 하나여야 한다.
        """
        body = self._body("function buttonRects")
        self.assertIn("spec.visible?.(node) !== false", body)
        for func in ("function drawTitleButtons", "function hitButton"):
            self.assertIn("buttonRects(node)", self._body(func))

    def test_right_click_menu_survives_as_a_fallback(self):
        """타이틀 바 버튼과 같은 이유 -- onMouseDown 이 안 불리는 버전이 있다."""
        self.assertIn("Refresh LM Studio models", self.javascript)
        self.assertIn("Refresh the server model list", self.javascript)
        # 메뉴도 버튼과 같은 조건을 써야 "버튼은 없는데 메뉴엔 있는" 이 안 생긴다.
        self.assertIn("if (hasModelDropdown(this))", self.javascript)


if __name__ == "__main__":
    unittest.main()
