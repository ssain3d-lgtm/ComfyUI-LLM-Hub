# -*- coding: utf-8 -*-
"""ComfyUI-LLM-Hub — LLM 백엔드 선택형 텍스트 생성 노드팩."""

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

# 노드 위 실시간 모니터링 창(web/js/llmhub_monitor.js)을 ComfyUI 가 로드하게 한다.
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
