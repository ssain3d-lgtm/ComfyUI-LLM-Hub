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
PRESET_ROUTE = "/llmhub/presets"
PRESET_DELETE_ROUTE = "/llmhub/presets/delete"


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

    # --- 시스템 프롬프트 프리셋 -------------------------------------------
    # 편집창이 목록을 받아오고, 지금 쓴 프롬프트를 이름 붙여 저장한다.
    # 응답은 항상 전체 목록을 함께 돌려준다 -- 저장/삭제 직후 프론트엔드가
    # 다시 조회하지 않아도 되고, 두 쪽 목록이 어긋날 일도 없다.

    from .utils import presets as _presets

    def _presets_payload():
        return {"ok": True, "presets": _presets.load_presets()}

    @instance.routes.get(PRESET_ROUTE)
    async def _presets_list(_request):
        return web.json_response(_presets_payload())

    @instance.routes.post(PRESET_ROUTE)
    async def _presets_save(request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        try:
            saved = _presets.save_preset(payload.get("name"), payload.get("prompt"))
        except _presets.PresetError as exc:
            # 사용자가 읽을 메시지다. 400 으로 내려 편집창이 그대로 보여준다.
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            return web.json_response(
                {"ok": False, "error": f"could not save the preset: {exc!r}"}, status=500
            )
        return web.json_response({"ok": True, "presets": saved})

    @instance.routes.post(PRESET_DELETE_ROUTE)
    async def _presets_delete(request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        try:
            remaining = _presets.delete_preset(payload.get("name"))
        except _presets.PresetError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            return web.json_response(
                {"ok": False, "error": f"could not delete the preset: {exc!r}"}, status=500
            )
        return web.json_response({"ok": True, "presets": remaining})

    return True
