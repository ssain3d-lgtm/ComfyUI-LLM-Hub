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

# claude CLI 의 --model 별칭. 실측으로 확인한 것만 넣는다 (claude --help 의 예시는
# 전부가 아니다). 각각 실제로 무엇으로 풀리는지는 2026-08-12 기준:
#   haiku -> claude-haiku-4-5-20251001 / opus -> claude-opus-5
#   sonnet -> claude-sonnet-5          / fable -> claude-fable-5
# 별칭을 쓰면 최신판을 따라가므로 날짜 박힌 전체 이름보다 오래 간다.
# 목록에 없는 모델은 위의 model 칸에 전체 이름을 직접 적으면 된다.
CLAUDE_MODELS = [AUTO_MODEL, "haiku", "opus", "sonnet", "fable"]


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
                "backend": (BACKEND_NAMES, {
                    "tooltip": "사용할 LLM. lmstudio=로컬 / claude·codex·gemini=구독 CLI. "
                               "각 백엔드는 로그인/실행이 미리 돼 있어야 한다.",
                }),
                "prompt": ("STRING", {"multiline": True, "default": "",
                    "tooltip": "유저 프롬프트. 여기에 시킬 일을 적는다."}),
                "system_prompt": ("STRING", {"multiline": True, "default": "",
                    "tooltip": "역할/말투/제약 지시. 비워도 된다."}),
                "model": ("STRING", {"default": "",
                    "tooltip": "모델 이름. 비우면 백엔드 기본값. "
                               "lmstudio 는 아래 lmstudio_model 드롭다운이 우선한다."}),
                "file_access": ("BOOLEAN", {"default": False,
                    "tooltip": "켜면 아래 workspace_dir 폴더의 파일을 읽을 수 있다(읽기 전용). "
                               "파일 내용은 신뢰할 수 없는 입력이니 필요한 폴더만 좁게 지정."}),
                "workspace_dir": ("STRING", {"default": "",
                    "tooltip": "작업 루트 폴더 절대경로. file_access 를 켰다면 필수. 예: C:\\\\work\\\\docs"}),
                "temperature": (
                    "FLOAT",
                    {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.05,
                     "tooltip": "창의성(0=일관, 높을수록 다양). lmstudio 에만 적용, CLI 3종은 무시."},
                ),
                "max_tokens": ("INT", {"default": 2048, "min": 1, "max": 32768,
                    "tooltip": "최대 생성 토큰. lmstudio 에만 적용, CLI 3종은 무시."}),
                "timeout_sec": ("INT", {"default": 300, "min": 10, "max": 3600,
                    "tooltip": "제한 시간(초). CLI 는 콜드스타트에 몇 초 걸리니 넉넉히."}),
                # seed 값 자체는 사용하지 않는다 (DESIGN §5-4).
                "seed": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 0xFFFFFFFF,
                        "control_after_generate": True,
                        "tooltip": "값 자체는 쓰지 않는다. 바꾸면 ComfyUI 가 '다시 실행'하게 만드는 "
                                   "캐시 무효화용. 같은 입력을 재생성하려면 값을 바꿔라.",
                    },
                ),
                # --- 아래는 나중에 추가된 위젯이다. 기존 위젯 뒤에 붙여야
                #     예전에 저장한 워크플로우의 widgets_values 위치가 밀리지 않는다. ---
                "video_max_frames": ("INT", {"default": 8, "min": 1, "max": 64,
                    "tooltip": "비디오를 프레임으로 바꿀 때 뽑을 장수. "
                               "claude/codex/lmstudio 만 해당(gemini 는 영상을 그대로 넘김)."}),
                "stream_view": (["plain", "markdown", "off"], {"default": "plain",
                    "tooltip": "노드 안 실시간 창 표시 방식. "
                               "plain=원문 그대로(프롬프트 생성용) / markdown=렌더링(문서용) / off=끔."}),
            },
            "optional": {
                "image": ("IMAGE", {"tooltip": "멀티모달 이미지 입력(옵션). 4개 백엔드 모두 지원."}),
                "video": ("VIDEO", {"tooltip": "ComfyUI VIDEO 입력(옵션)."}),
                "video_path": ("STRING", {"default": "",
                    "tooltip": "비디오 파일 경로 직접 지정(옵션). 지정 시 video 입력보다 우선."}),
                "mcp_config": ("STRING", {"default": "",
                    "tooltip": "MCP 설정 JSON 경로(옵션). claude 만 실제 적용."}),
                "extra_args": ("STRING", {"default": "",
                    "tooltip": "CLI 에 덧붙일 원시 플래그(고급). 샌드박스를 푸는 위험 플래그는 "
                               "자동 차단된다."}),
                # --- 나중에 추가된 위젯 (위와 같은 이유로 뒤에 붙인다) ---
                "lmstudio_model": ([AUTO_MODEL] + list_model_ids(), {
                    "tooltip": "[lmstudio 전용] 모델 드롭다운. 서버를 켠 뒤 브라우저를 새로고침하면 "
                               "목록이 채워진다. (auto)=위 model 칸/설정을 따름."}),
                "lmstudio_ttl_sec": ("INT", {"default": _ls_default("ttl_sec", 300),
                    "min": 0, "max": 86400,
                    "tooltip": "[lmstudio 전용] 유휴 TTL(초). 이 시간 요청이 없으면 VRAM 에서 내림. 0=끔."}),
                "lmstudio_unload_after": ("BOOLEAN", {"default": _ls_default("unload_after", True),
                    "tooltip": "[lmstudio 전용] 응답 직후 즉시 VRAM 해제(lms CLI 필요). "
                               "반복 호출이 잦으면 꺼서 재로드를 피할 수 있다."}),
                # --- 나중에 추가된 위젯 (반드시 맨 뒤에 붙인다) ---
                "claude_model": (CLAUDE_MODELS, {
                    "tooltip": "[claude 전용] 모델 선택. (auto)=위 model 칸/CLI 기본값. "
                               "비용은 haiku < opus < sonnet < fable 순으로 커진다"
                               "(실측 2026-08-12, 같은 호출 기준 haiku 는 opus 의 약 1/3). "
                               "속도는 모델을 바꿔도 별 차이가 없다 — 매 호출의 약 2초가 "
                               "claude CLI 를 새로 띄우는 고정비라 그쪽이 병목이다."}),
            },
            # 모니터링 창이 어느 노드에 그려질지 알기 위해 노드 id 를 받는다.
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    @classmethod
    def VALIDATE_INPUTS(cls, input_types=None, lmstudio_model=None):
        """lmstudio_model 만 검증을 건너뛴다(다른 입력은 정상 검증됨).

        lmstudio_model 은 LM Studio 를 조회해 만든 가변 목록이라, 서버가 꺼졌거나
        모델이 언로드되면 목록이 줄어든다. 그때 ComfyUI 기본 검증이 저장된 값을
        거부해 워크플로우 실행 자체가 실패한다(lmstudio 를 안 쓰는 경우까지).
        → 이 입력만 통과시키고 실제 처리는 백엔드가 한다.

        주의: **kwargs 로 받으면 ComfyUI 가 모든 입력의 검증을 통째로 건너뛴다.
        그래서 lmstudio_model 만 명시적으로 받아 우회 범위를 이 입력으로 좁힌다.
        (input_types 는 ComfyUI 가 넘겨주는 표준 인자라 함께 받아 흡수한다.)
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
        claude_model=AUTO_MODEL,
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

            # 백엔드별 드롭다운에서 고른 모델이 있으면 그쪽이 우선한다.
            # 드롭다운은 그 백엔드에서만 본다 — claude 를 쓰는데 lmstudio_model 이
            # 남아 있다고 그걸 집어가면 안 된다.
            chosen_model = (model or "").strip()
            if backend == "lmstudio" and lmstudio_model and lmstudio_model != AUTO_MODEL:
                chosen_model = lmstudio_model
            elif backend == "claude" and claude_model and claude_model != AUTO_MODEL:
                chosen_model = claude_model

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
