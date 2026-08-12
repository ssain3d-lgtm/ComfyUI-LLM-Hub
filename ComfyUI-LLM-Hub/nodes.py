# -*- coding: utf-8 -*-
"""ComfyUI 노드 정의 (DESIGN §5)."""

from __future__ import annotations

import traceback

from .backends import BACKEND_NAMES, get_backend
from .backends.base import LLMRequest, truncate_debug
from .utils import image_io


class LLMHubGenerate:
    """LLM 백엔드를 골라 텍스트를 생성하는 노드."""

    CATEGORY = "LLM Hub"
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("text", "status", "debug")
    FUNCTION = "generate"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "backend": (BACKEND_NAMES,),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "system_prompt": ("STRING", {"multiline": True, "default": ""}),
                "model": ("STRING", {"default": ""}),  # 빈값 = 백엔드 기본값
                "file_access": ("BOOLEAN", {"default": False}),
                "workspace_dir": ("STRING", {"default": ""}),
                "temperature": (
                    "FLOAT",
                    {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.05},
                ),
                "max_tokens": ("INT", {"default": 2048, "min": 1, "max": 32768}),
                "timeout_sec": ("INT", {"default": 300, "min": 10, "max": 3600}),
                # seed 값 자체는 사용하지 않는다 (DESIGN §5-4).
                # ComfyUI 가 "입력이 바뀌었으니 다시 실행"으로 인식하게 만드는
                # 캐시 무효화 용도다. IS_CHANGED 는 일부러 구현하지 않는다.
                "seed": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 0xFFFFFFFF,
                        "control_after_generate": True,
                    },
                ),
            },
            "optional": {
                "image": ("IMAGE",),
                "mcp_config": ("STRING", {"default": ""}),  # JSON 파일 경로
                "extra_args": ("STRING", {"default": ""}),  # 고급 사용자용 원시 플래그
            },
        }

    def generate(
        self,
        backend,
        prompt,
        system_prompt,
        model,
        file_access,
        workspace_dir,
        temperature,
        max_tokens,
        timeout_sec,
        seed,
        image=None,
        mcp_config="",
        extra_args="",
    ):
        # 노드는 어떤 경우에도 예외를 밖으로 던지지 않는다 (DESIGN N4, §5-3).
        try:
            workspace_dir = (workspace_dir or "").strip()
            image_paths = []
            image_note = ""

            if image is not None:
                try:
                    image_paths = image_io.save_images(
                        image, workspace_dir, bool(file_access)
                    )
                except Exception as exc:
                    image_note = f"image: PNG 저장 실패 — {type(exc).__name__}: {exc}"

            req = LLMRequest(
                backend=backend,
                model=(model or "").strip(),
                system_prompt=system_prompt or "",
                user_prompt=prompt or "",
                image_paths=image_paths,
                workspace_dir=workspace_dir,
                file_access=bool(file_access),
                mcp_config=(mcp_config or "").strip(),
                temperature=float(temperature),
                max_tokens=int(max_tokens),
                timeout_s=int(timeout_sec),
                extra_args=(extra_args or "").strip(),
            )

            response = get_backend(backend).generate(req)

            debug_parts = []
            if image_note:
                debug_parts.append(image_note)
            if image_paths:
                debug_parts.append(f"images: {len(image_paths)}개 저장 → {image_paths[0]}")
            debug_parts.append(f"backend={backend} duration={response.duration_s:.1f}s")
            if response.raw_debug:
                debug_parts.append(response.raw_debug)

            return (
                response.text or "",
                response.status,
                truncate_debug("\n".join(p for p in debug_parts if p)),
            )

        except Exception as exc:
            return (
                "",
                f"error: 노드 내부 오류 — {type(exc).__name__}: {exc}",
                truncate_debug(traceback.format_exc()),
            )


NODE_CLASS_MAPPINGS = {"LLMHubGenerate": LLMHubGenerate}
NODE_DISPLAY_NAME_MAPPINGS = {"LLMHubGenerate": "LLM Hub Generate"}
