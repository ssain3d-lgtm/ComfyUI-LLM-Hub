# -*- coding: utf-8 -*-
"""ComfyUI-LLM-Hub — LLM 백엔드 선택형 텍스트 생성 노드팩."""

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
from .server_routes import register as _register_routes

# 노드의 Stop 버튼이 쓰는 경로. ComfyUI 밖에서는 조용히 건너뛴다.
_register_routes()

# 노드 위 실시간 모니터링 창(web/js/llmhub_monitor.js)을 ComfyUI 가 로드하게 한다.
#
# "./web" 이 아니라 "./web/js" 여야 한다. ComfyUI 는 이 폴더를
# /extensions/<팩이름>/ 아래에 통째로 붙이므로, "./web" 이면 파일이
# /extensions/ComfyUI-LLM-Hub/js/llmhub_monitor.js 로 한 단계 깊게 서빙된다.
# 그러면 관용구인 `import { app } from "../../scripts/app.js"` 가
# /extensions/scripts/app.js 로 풀려 404 가 나고, 모듈 로드가 통째로 실패한다.
# 파이썬 쪽은 멀쩡히 임포트되므로 로그에는 아무것도 안 남고, 노드는 뜨는데
# JS 기능(모니터 창, 위젯 접기)만 전부 사라진다. 실측으로 확인한 증상이다.
WEB_DIRECTORY = "./web/js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
