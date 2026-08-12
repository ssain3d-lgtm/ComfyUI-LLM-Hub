# -*- coding: utf-8 -*-
"""codex --json 이벤트 스키마 (실측 회귀).

DESIGN 이 "실측하지 못해 관대한 파서로 처리해뒀다" 고 적어둔 그 자리다.
2026-08-12, codex-cli 0.146.0 을 직접 돌려 받은 전부(4줄):

    {"type":"thread.started","thread_id":"..."}
    {"type":"turn.started"}
    {"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"..."}}
    {"type":"turn.completed","usage":{...}}

종류가 바깥의 "item.completed" 가 아니라 안쪽 item.type 에 있다. 바깥만 보면
"delta"/"message"/"agent" 어디에도 안 걸려서 본문이 통째로 버려진다 —
모니터 창은 끝까지 비어 있고, 로그도 남지 않는다.

토큰 단위 델타 이벤트는 이 버전에 없다(codex exec 에 해당 옵션 자체가 없다).
그래서 이 백엔드로는 "실시간 타이핑" 이 불가능하고, 완성 메시지가 한 번에 온다.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import unittest

_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_PACK_ROOT))
_PACK_NAME = os.path.basename(_PACK_ROOT)

codex_mod = importlib.import_module(f"{_PACK_NAME}.backends.codex")

# 실측 그대로.
REAL_STREAM = [
    {"type": "thread.started", "thread_id": "019ff591-e56b-7573-b6ee-f62941f28e34"},
    {"type": "turn.started"},
    {"type": "item.completed",
     "item": {"id": "item_0", "type": "agent_message",
              "text": "A fluffy cat by a sunlit window"}},
    {"type": "turn.completed",
     "usage": {"input_tokens": 17135, "output_tokens": 53}},
]


class Recorder:
    enabled = True

    def __init__(self):
        self.text = ""
        self.appends = 0
        self.statuses = []

    def append(self, delta):
        if delta:
            self.text += delta
            self.appends += 1

    def reset_text(self, text=""):
        self.text = text

    def set_status(self, status):
        self.statuses.append(status)

    def finish(self, status="", text=None):
        if text is not None:
            self.text = text


def _feed(events):
    emitter = Recorder()
    state = {"text": ""}
    for event in events:
        codex_mod._on_stream_line(json.dumps(event, ensure_ascii=False), emitter, state)
    return emitter, state


class TestCodexEventSchema(unittest.TestCase):
    def test_completed_agent_message_reaches_the_monitor(self):
        emitter, state = _feed(REAL_STREAM)
        self.assertEqual(emitter.text, "A fluffy cat by a sunlit window")
        self.assertEqual(state["text"], "A fluffy cat by a sunlit window")

    def test_thread_and_turn_events_produce_no_body(self):
        """본문이 없는 이벤트가 상태 줄이나 본문을 더럽히면 안 된다."""
        emitter, state = _feed([REAL_STREAM[0], REAL_STREAM[1], REAL_STREAM[3]])
        self.assertEqual(emitter.text, "")
        self.assertEqual(state["text"], "")

    def test_nested_command_item_is_reported_as_tool_use(self):
        emitter, _ = _feed([{
            "type": "item.completed",
            "item": {"id": "item_1", "type": "command_execution",
                     "command": "ls", "text": "ls"},
        }])
        self.assertTrue(emitter.statuses, "도구 사용이 상태로 보이지 않는다")
        self.assertEqual(emitter.text, "", "도구 이벤트가 본문에 섞였다")

    def test_delta_events_still_accumulate_if_a_version_emits_them(self):
        """나중 버전이 델타를 내면 그때도 동작해야 한다."""
        emitter, state = _feed([
            {"type": "item.delta", "item": {"type": "agent_message_delta", "text": "안"}},
            {"type": "item.delta", "item": {"type": "agent_message_delta", "text": "녕"}},
        ])
        self.assertEqual(state["text"], "안녕")
        self.assertEqual(emitter.appends, 2)


if __name__ == "__main__":
    unittest.main()
