# -*- coding: utf-8 -*-
"""노드의 Stop 버튼이 두드리는 경로.

생성은 워커 스레드에서 돌고 Stop 은 HTTP 로 들어온다. 여기서 받아 utils/cancel 의
깃발을 세우면, 실행 중인 루프가 그것을 보고 빠져나온다(자세한 설명은 그 파일에).

ComfyUI 밖(테스트/단독 실행)에서는 PromptServer 가 없으므로 아무 것도 하지 않는다.
"""

from __future__ import annotations

from .utils import cancel

ROUTE = "/llmhub/stop"


def register() -> bool:
    """PromptServer 에 라우트를 붙인다. 성공하면 True."""
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception:
        return False

    instance = getattr(PromptServer, "instance", None)
    if instance is None or not hasattr(instance, "routes"):
        return False

    @instance.routes.post(ROUTE)
    async def _stop(request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        node_id = payload.get("node")
        if node_id is None:
            return web.json_response({"ok": False, "error": "node 가 없습니다"}, status=400)
        cancel.request_stop(node_id)
        return web.json_response({"ok": True, "node": str(node_id)})

    return True
