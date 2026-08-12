# -*- coding: utf-8 -*-
"""노드 모니터링 창으로 생성 중인 텍스트를 실시간 전송한다.

ComfyUI 의 PromptServer 웹소켓으로 부분 결과를 밀어 넣는다.
ComfyUI 밖(테스트/단독 실행)에서는 아무 것도 하지 않는 no-op 으로 동작한다.
"""

from __future__ import annotations

import time

EVENT_NAME = "llmhub.stream"

# 너무 잦은 전송으로 프론트엔드를 괴롭히지 않도록 최소 간격을 둔다.
DEFAULT_THROTTLE_S = 0.12


def _prompt_server():
    """ComfyUI 런타임에서만 존재한다."""
    try:
        from server import PromptServer

        return PromptServer.instance
    except Exception:
        return None


class StreamEmitter:
    """생성 중 텍스트를 모아 노드로 흘려보낸다.

    누적 전문(全文)을 보내므로 프론트엔드가 순서를 맞출 필요가 없다.
    """

    def __init__(self, node_id=None, enabled: bool = True, throttle_s: float = DEFAULT_THROTTLE_S):
        self.node_id = str(node_id) if node_id is not None else ""
        self.enabled = bool(enabled and self.node_id)
        self.throttle_s = throttle_s
        self.text = ""
        self.status = ""
        self.started = time.time()
        self._last_push = 0.0
        self._server = _prompt_server() if self.enabled else None
        if self._server is None:
            self.enabled = False

    # -- 수집 ---------------------------------------------------------------

    def append(self, delta: str) -> None:
        """모델이 낸 텍스트 조각을 붙인다."""
        if not delta:
            return
        self.text += delta
        self._push()

    def set_status(self, status: str) -> None:
        """진행 상황 한 줄 (예: 'test.txt 읽는 중')."""
        if status == self.status:
            return
        self.status = status or ""
        self._push(force=True)

    def reset_text(self, text: str = "") -> None:
        """툴 루프처럼 본문이 다시 시작되는 경우."""
        self.text = text
        self._push(force=True)

    def finish(self, status: str = "", text: str = None) -> None:
        """생성 종료. 최종 상태와 전문을 한 번 더 보낸다."""
        if text is not None:
            self.text = text
        self.status = status or self.status
        self._push(force=True, done=True)

    # -- 전송 ---------------------------------------------------------------

    def _push(self, force: bool = False, done: bool = False) -> None:
        if not self.enabled:
            return
        now = time.time()
        if not force and (now - self._last_push) < self.throttle_s:
            return
        self._last_push = now
        payload = {
            "node": self.node_id,
            "text": self.text,
            "status": self.status,
            "elapsed": round(now - self.started, 1),
            "done": bool(done),
        }
        try:
            self._server.send_sync(EVENT_NAME, payload)
        except Exception:
            # 모니터링 실패가 생성 자체를 막으면 안 된다.
            self.enabled = False


def make_emitter(node_id=None, enabled: bool = True) -> StreamEmitter:
    return StreamEmitter(node_id=node_id, enabled=enabled)
