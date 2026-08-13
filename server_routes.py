# -*- coding: utf-8 -*-
"""노드의 Stop 버튼이 두드리는 경로.

생성은 워커 스레드에서 돌고 Stop 은 HTTP 로 들어온다. 여기서 받아 utils/cancel 의
깃발을 세우면, 실행 중인 루프가 그것을 보고 빠져나온다(자세한 설명은 그 파일에).

ComfyUI 밖(테스트/단독 실행)에서는 PromptServer 가 없으므로 아무 것도 하지 않는다.
"""

from __future__ import annotations

from .utils import cancel

ROUTE = "/llmhub/stop"
HEALTH_ROUTE = "/llmhub/health"


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
            return web.json_response({"ok": False, "error": "missing 'node'"}, status=400)
        cancel.request_stop(node_id)
        return web.json_response({"ok": True, "node": str(node_id)})

    @instance.routes.get(HEALTH_ROUTE)
    async def _health(request):
        """브라우저에서 그냥 열어보는 자가 진단.

        기본이 text/plain 인 이유: 이걸 여는 사람은 "뭐가 문제인지" 를 눈으로
        읽으려는 것이지 JSON 을 파싱하려는 게 아니다. 기계용은 ?json=1.
        """
        from .utils import health

        try:
            report = health.collect()
        except Exception as exc:  # 진단이 죽으면 진단할 방법이 없어진다
            return web.json_response(
                {"ok": False, "error": f"diagnostics failed: {exc!r}"}, status=500
            )

        if request.query.get("json"):
            return web.json_response(report)
        return web.Response(
            text=health.as_text(report),
            content_type="text/plain",
            charset="utf-8",
        )

    return True
