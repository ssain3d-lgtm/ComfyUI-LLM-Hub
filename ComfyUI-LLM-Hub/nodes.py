# -*- coding: utf-8 -*-
"""ComfyUI 노드 정의 (DESIGN §5)."""

from __future__ import annotations

import traceback

from .backends import BACKEND_NAMES, get_backend
from .backends.base import LLMRequest, truncate_debug
from .backends.lmstudio import list_model_ids
from .utils import image_io, stream, video_io

# lmstudio_model 드롭다운의 첫 항목 (= 노드의 model 칸/설정을 따름)
AUTO_MODEL = "(auto)"


def _ls_default(key, fallback):
    """config.json 의 lmstudio 설정을 위젯 기본값으로 쓴다.

    설정 파일 값이 실제로 노드에 반영되게 하려면 여기서 읽어야 한다.
    """
    try:
        from .utils.config import load_config

        value = (load_config().get("lmstudio", {}) or {}).get(key)
        return fallback if value is None else value
    except Exception:
        return fallback


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
                # --- 아래는 나중에 추가된 위젯이다. 기존 위젯 뒤에 붙여야
                #     예전에 저장한 워크플로우의 widgets_values 위치가 밀리지 않는다. ---
                # 비디오를 네이티브로 못 받는 백엔드(claude/codex/lmstudio)에서
                # 뽑아 보낼 프레임 수. gemini 는 파일을 그대로 넘기므로 무시된다.
                "video_max_frames": ("INT", {"default": 8, "min": 1, "max": 64}),
                # 실시간 모니터링 창 표시 방식.
                #   plain    = 원문 그대로 (프롬프트 생성용 — 문자를 있는 그대로 확인)
                #   markdown = 마크다운 렌더링 (문서 요약/분석용)
                #   off      = 표시 안 함(스트리밍도 하지 않음)
                "stream_view": (["plain", "markdown", "off"],),
            },
            "optional": {
                "image": ("IMAGE",),
                "video": ("VIDEO",),  # ComfyUI VIDEO 입력
                "video_path": ("STRING", {"default": ""}),  # 비디오 파일 경로 직접 지정
                "mcp_config": ("STRING", {"default": ""}),  # JSON 파일 경로
                "extra_args": ("STRING", {"default": ""}),  # 고급 사용자용 원시 플래그
                # --- 나중에 추가된 위젯 (위와 같은 이유로 뒤에 붙인다) ---
                # LM Studio 에 있는 모델 목록. 서버가 꺼져 있으면 (auto) 만 보인다
                # (LM Studio 를 켠 뒤 브라우저를 새로고침하면 목록이 채워진다).
                "lmstudio_model": ([AUTO_MODEL] + list_model_ids(),),
                # 유휴 TTL(초). 이 시간 동안 요청이 없으면 LM Studio 가 VRAM 에서 내린다.
                "lmstudio_ttl_sec": ("INT", {"default": _ls_default("ttl_sec", 300),
                                             "min": 0, "max": 86400}),
                # 응답 직후 즉시 VRAM 에서 내릴지 (lms CLI 필요)
                "lmstudio_unload_after": ("BOOLEAN",
                                          {"default": _ls_default("unload_after", True)}),
            },
            # 모니터링 창이 어느 노드에 그려질지 알기 위해 노드 id 를 받는다.
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    @classmethod
    def VALIDATE_INPUTS(cls, lmstudio_model=None, **_kwargs):
        """lmstudio_model 은 LM Studio 를 조회해 만든 목록이라 가변적이다.

        서버가 꺼졌거나 모델이 언로드되면 목록이 줄어드는데, 그때 ComfyUI 기본
        검증이 저장된 값을 거부해 워크플로우 실행 자체가 실패한다.
        (lmstudio 백엔드를 쓰지 않는 경우까지 막힌다.)
        → 이 입력은 검증을 건너뛰고, 실제 처리는 백엔드가 한다.
        """
        return True

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
        # ComfyUI 는 입력을 키워드로 넘기므로 순서는 INPUT_TYPES 와 달라도 된다.
        # 기본값을 둬서 이 입력이 없는 예전 워크플로우도 그대로 동작한다.
        stream_view="plain",
        video_max_frames=8,
        lmstudio_model=AUTO_MODEL,
        lmstudio_ttl_sec=300,
        lmstudio_unload_after=True,
        image=None,
        video=None,
        video_path="",
        mcp_config="",
        extra_args="",
        unique_id=None,
    ):
        # 노드는 어떤 경우에도 예외를 밖으로 던지지 않는다 (DESIGN N4, §5-3).
        emitter = None
        try:
            workspace_dir = (workspace_dir or "").strip()
            image_paths = []
            video_paths = []
            media_notes = []

            if image is not None:
                try:
                    image_paths = image_io.save_images(
                        image, workspace_dir, bool(file_access)
                    )
                except Exception as exc:
                    media_notes.append(f"image: PNG 저장 실패 — {type(exc).__name__}: {exc}")

            if video is not None or (video_path or "").strip():
                tmp_dir = image_io.get_tmp_dir(workspace_dir, bool(file_access))
                resolved, note = video_io.resolve_video(video, video_path, tmp_dir)
                if note:
                    media_notes.append(note)
                if resolved:
                    video_paths = [resolved]

            emitter = stream.make_emitter(
                node_id=unique_id, enabled=(stream_view != "off")
            )
            emitter.set_status(f"{backend} 준비 중...")

            # lmstudio 드롭다운에서 고른 모델이 있으면 그쪽이 우선한다.
            chosen_model = (model or "").strip()
            if backend == "lmstudio" and lmstudio_model and lmstudio_model != AUTO_MODEL:
                chosen_model = lmstudio_model

            req = LLMRequest(
                backend=backend,
                model=chosen_model,
                system_prompt=system_prompt or "",
                user_prompt=prompt or "",
                image_paths=image_paths,
                video_paths=video_paths,
                video_max_frames=int(video_max_frames),
                workspace_dir=workspace_dir,
                file_access=bool(file_access),
                mcp_config=(mcp_config or "").strip(),
                temperature=float(temperature),
                max_tokens=int(max_tokens),
                timeout_s=int(timeout_sec),
                extra_args=(extra_args or "").strip(),
                ttl_sec=int(lmstudio_ttl_sec),
                unload_after=bool(lmstudio_unload_after),
                emitter=emitter,
            )

            response = get_backend(backend).generate(req)
            emitter.finish(status=response.status, text=response.text or emitter.text)

            debug_parts = list(media_notes)
            if image_paths:
                debug_parts.append(f"images: {len(image_paths)}개 저장 → {image_paths[0]}")
            if video_paths:
                debug_parts.append(f"video: {video_paths[0]}")
            debug_parts.append(f"backend={backend} duration={response.duration_s:.1f}s")
            if response.raw_debug:
                debug_parts.append(response.raw_debug)

            return (
                response.text or "",
                response.status,
                truncate_debug("\n".join(p for p in debug_parts if p)),
            )

        except Exception as exc:
            # 실패해도 모니터링 창이 "생성 중..." 으로 멈춰 있지 않게 마무리한다.
            if emitter is not None:
                try:
                    emitter.finish(status=f"error: {type(exc).__name__}")
                except Exception:
                    pass
            return (
                "",
                f"error: 노드 내부 오류 — {type(exc).__name__}: {exc}",
                truncate_debug(traceback.format_exc()),
            )


NODE_CLASS_MAPPINGS = {"LLMHubGenerate": LLMHubGenerate}
NODE_DISPLAY_NAME_MAPPINGS = {"LLMHubGenerate": "LLM Hub Generate"}
