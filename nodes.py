# -*- coding: utf-8 -*-
"""ComfyUI 노드 정의 (DESIGN §5)."""

from __future__ import annotations

import traceback

from .backends import BACKEND_NAMES, get_backend
from .backends.base import LLMRequest, truncate_debug
from .backends.lmstudio import list_model_ids
from .utils import cancel, image_io, presets, stream, video_io

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
    # 결과를 노드에 남기면서 동시에 아래로 흘려보낸다.
    # 모니터 창(웹소켓)은 실행 중에만 보이는 휘발성이라, 워크플로우를 다시 열면
    # 아무것도 남지 않는다. OUTPUT_NODE + {"ui": ...} 는 저장되는 쪽이다.
    # 둘은 대체재가 아니라 보완재다 -- 라이브는 모니터가, 보존은 이쪽이 맡는다.
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "backend": (BACKEND_NAMES, {
                    "tooltip": "Which LLM to use. lmstudio/openai_compat = local server, "
                               "claude/codex/gemini = subscription CLIs. Each backend must "
                               "already be installed and logged in.",
                }),
                "prompt": ("STRING", {"multiline": True, "default": "",
                    "tooltip": "The user prompt. Write what you want done here."}),
                "system_prompt": ("STRING", {"multiline": True, "default": "",
                    "tooltip": "Role, tone and constraints. May be left empty."}),
                "model": ("STRING", {"default": "",
                    "tooltip": "Model name. Empty = the backend default. "
                               "For lmstudio the lmstudio_model dropdown below wins."}),
                "file_access": ("BOOLEAN", {"default": False,
                    "tooltip": "Let the model read files inside workspace_dir (read-only). "
                               "File contents are untrusted input, so point it at the "
                               "narrowest folder that works."}),
                "workspace_dir": ("STRING", {"default": "",
                    "tooltip": "Absolute path of the working root folder. Required when "
                               "file_access is on. Example: C:\\\\work\\\\docs"}),
                "temperature": (
                    "FLOAT",
                    {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.05,
                     "tooltip": "Creativity (0 = consistent, higher = more varied). "
                                "Applies to lmstudio/openai_compat only; the three CLIs "
                                "ignore it."},
                ),
                # 상한은 32768 이었는데 근거가 없었다. LM Studio 에 올라와 있는 모델들의
                # max_context_length 를 조회해보면 전부 262144 라, 위젯이 실제 능력의
                # 1/8 에서 막고 있었다. 상한만 키우는 것이라 저장된 워크플로우는 영향이 없다.
                "max_tokens": ("INT", {"default": 2048, "min": 1, "max": 262144,
                    "tooltip": "Maximum tokens to generate. Applies to lmstudio/"
                               "openai_compat only; the three CLIs ignore it. Reasoning "
                               "models emit hidden thinking before the answer and it counts "
                               "against this budget, so a small value can cut the reply off "
                               "empty. 1000 or more is recommended."}),
                "timeout_sec": ("INT", {"default": 300, "min": 10, "max": 3600,
                    "tooltip": "Time limit in seconds. The CLIs need a few seconds of cold "
                               "start, so leave room."}),
                # seed 값 자체는 사용하지 않는다 (DESIGN §5-4).
                "seed": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 0xFFFFFFFF,
                        "control_after_generate": True,
                        "tooltip": "The value itself is never used. Changing it is how you "
                                   "tell ComfyUI the input changed so it runs again instead "
                                   "of reusing the cache. Change it to regenerate the same "
                                   "prompt.",
                    },
                ),
                # --- 아래는 나중에 추가된 위젯이다. 기존 위젯 뒤에 붙여야
                #     예전에 저장한 워크플로우의 widgets_values 위치가 밀리지 않는다. ---
                "video_max_frames": ("INT", {"default": 8, "min": 1, "max": 64,
                    "tooltip": "How many frames to sample when converting video to images. "
                               "Applies to claude/codex/lmstudio only — gemini takes the "
                               "video file as it is."}),
                "stream_view": (["plain", "markdown", "off"], {"default": "plain",
                    "tooltip": "How the live monitor on the node displays text. "
                               "plain = literal characters (for image prompts) / "
                               "markdown = rendered (for documents) / "
                               "off = hidden, which also removes the panel."}),
            },
            "optional": {
                "image": ("IMAGE", {
                    "tooltip": "Multimodal image input (optional). All backends support it."}),
                "video": ("VIDEO", {"tooltip": "ComfyUI VIDEO input (optional)."}),
                "video_path": ("STRING", {"default": "",
                    "tooltip": "Path to a video file (optional). Takes priority over the "
                               "video input when both are set."}),
                "mcp_config": ("STRING", {"default": "",
                    "tooltip": "Path to an MCP config JSON (optional). Only claude actually "
                               "applies it."}),
                "extra_args": ("STRING", {"default": "",
                    "tooltip": "Raw extra flags for the CLI (advanced). Flags that unlock "
                               "the read-only sandbox are blocked automatically."}),
                # --- 나중에 추가된 위젯 (위와 같은 이유로 뒤에 붙인다) ---
                "lmstudio_model": ([AUTO_MODEL] + list_model_ids(), {
                    "tooltip": "[lmstudio only] Model dropdown. Start the server, then "
                               "refresh the browser to populate the list. "
                               "(auto) = follow the model field above and config.json."}),
                "lmstudio_ttl_sec": ("INT", {"default": _ls_default("ttl_sec", 300),
                    "min": 0, "max": 86400,
                    "tooltip": "[lmstudio only] Idle TTL in seconds. LM Studio unloads the "
                               "model from VRAM after this long with no request. 0 = off."}),
                "openai_base_url": ("STRING", {
                    "default": "",
                    "tooltip": "Address of the OpenAI-compatible server. Empty = use "
                               "openai_compat.base_url from config.json. "
                               "Ollama http://127.0.0.1:11434 / "
                               "vLLM http://127.0.0.1:8000 / "
                               "llama.cpp http://127.0.0.1:8080"}),
                "lmstudio_unload_after": ("BOOLEAN", {"default": _ls_default("unload_after", True),
                    "tooltip": "[lmstudio only] Unload from VRAM immediately after the "
                               "response (needs the lms CLI). Turn it off if you call this "
                               "node repeatedly and reloading each time is too slow."}),
                # --- 나중에 추가된 위젯 (반드시 맨 뒤에 붙인다) ---
                "claude_model": (CLAUDE_MODELS, {
                    "tooltip": "[claude only] Model choice. (auto) = the model field above, "
                               "or the CLI default. Cost rises haiku < opus < sonnet < fable "
                               "(measured 2026-08-12: for the same call haiku is about a "
                               "third of opus). Speed barely changes between models — the "
                               "~2 s of starting the claude CLI on every call is the "
                               "bottleneck."}),
                # --- 나중에 추가된 위젯 (반드시 맨 뒤에 붙인다) ---
                #
                # system_prompt 바로 밑에 두는 편이 자연스럽지만 그렇게 못 한다.
                # 위젯 순서가 곧 widgets_values 의 순서라, 중간에 끼우면 이 노드로
                # 저장해둔 예전 워크플로우의 값이 전부 한 칸씩 밀린다.
                "system_preset": (presets.preset_names(), {
                    "tooltip": "Pick a saved system prompt from system_prompts.json. "
                               "(none) = use only the system_prompt box above. When both "
                               "are set the preset goes first and your text is appended, so "
                               "you can add a one-off instruction on top of a preset. "
                               "Edit the file, then refresh the browser to reload this list."}),
            },
            # 모니터링 창이 어느 노드에 그려질지 알기 위해 노드 id 를 받는다.
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    @classmethod
    def VALIDATE_INPUTS(cls, input_types=None, lmstudio_model=None, system_preset=None):
        """가변 목록인 두 입력만 검증을 건너뛴다(나머지는 정상 검증됨).

        lmstudio_model 은 LM Studio 를 조회해 만든 목록이라 서버가 꺼졌거나
        모델이 언로드되면 줄어든다. system_preset 은 사용자가 손으로 고치는
        파일에서 오므로 프리셋 이름을 바꾸거나 지우면 줄어든다.

        그때 ComfyUI 기본 검증이 저장된 값을 거부해 워크플로우 실행 자체가
        실패한다 -- 해당 기능을 안 쓰는 경우까지 같이 죽는다.
        → 이 둘만 통과시키고, 목록에 없는 값의 처리는 각자 담당 코드가 한다
          (presets.resolve 는 무시하고 debug 에 이유를 남긴다).

        주의: **kwargs 로 받으면 ComfyUI 가 모든 입력의 검증을 통째로 건너뛴다.
        그래서 우회할 입력만 명시적으로 받아 범위를 좁힌다.
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
        openai_base_url="",
        claude_model=AUTO_MODEL,
        image=None,
        video=None,
        video_path="",
        mcp_config="",
        extra_args="",
        system_preset=presets.PRESET_NONE,
        unique_id=None,
    ):
        # 노드는 어떤 경우에도 예외를 밖으로 던지지 않는다 (DESIGN N4, §5-3).
        emitter = None
        # 지난 실행에서 Stop 을 눌렀다면 그 표시를 지운다. 안 지우면 한 번 멈춘
        # 노드가 영원히 즉시 중지된다.
        cancel.begin(unique_id)
        try:
            workspace_dir = (workspace_dir or "").strip()
            image_paths = []
            video_paths = []
            media_notes = []

            # 프리셋을 시스템 프롬프트에 반영한다. 이름이 목록에 없어도 실패로
            # 만들지 않고 메모만 남긴다 -- 파일을 고치다 이름이 어긋났다고
            # 워크플로우 전체가 안 도는 것은 과한 처벌이다.
            system_prompt, preset_note = presets.resolve(system_preset, system_prompt)
            if preset_note:
                media_notes.append(preset_note)

            if image is not None:
                try:
                    image_paths = image_io.save_images(
                        image, workspace_dir, bool(file_access)
                    )
                except Exception as exc:
                    media_notes.append(f"image: failed to save PNG - {type(exc).__name__}: {exc}")

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
            emitter.set_status(f"{backend} starting...")

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
                base_url_override=(openai_base_url or "").strip(),
                ttl_sec=int(lmstudio_ttl_sec),
                unload_after=bool(lmstudio_unload_after),
                emitter=emitter,
            )

            impl = get_backend(backend)
            # openai_compat 만 노드에서 주소를 갈아탈 수 있다.
            if hasattr(impl, "apply_base_url"):
                impl.apply_base_url(req.base_url_override)
            response = impl.generate(req)

            # 백엔드마다 중지가 다른 모양으로 끝난다(SSE 중단 / 프로세스 사망 →
            # "종료 코드 -1" 등). 사용자가 누른 것은 오류가 아니므로 여기 한 곳에서
            # 통일해 말해준다. 받은 데까지는 그대로 둔다.
            if cancel.is_stopped(unique_id):
                response.status = "stopped - cancelled by user, returning what arrived so far"

            emitter.finish(status=response.status, text=response.text or emitter.text)

            debug_parts = list(media_notes)
            if image_paths:
                debug_parts.append(f"images: {len(image_paths)} saved -> {image_paths[0]}")
            if video_paths:
                debug_parts.append(f"video: {video_paths[0]}")
            debug_parts.append(f"backend={backend} duration={response.duration_s:.1f}s")
            if response.raw_debug:
                debug_parts.append(response.raw_debug)

            text_out = response.text or ""
            debug_out = truncate_debug("\n".join(p for p in debug_parts if p))
            return {
                # ui 는 노드에 표시되고 워크플로우에 저장된다.
                "ui": {"text": [text_out], "llmhub_status": [response.status]},
                # result 는 그대로 아래 노드로 흐른다(터미널 노드가 되지 않게).
                "result": (text_out, response.status, debug_out),
            }

        except Exception as exc:
            # 실패해도 모니터링 창이 "생성 중..." 으로 멈춰 있지 않게 마무리한다.
            if emitter is not None:
                try:
                    emitter.finish(status=f"error: {type(exc).__name__}")
                except Exception:
                    pass
            status_out = f"error: internal node error - {type(exc).__name__}: {exc}"
            return {
                "ui": {"text": [""], "llmhub_status": [status_out]},
                "result": ("", status_out, truncate_debug(traceback.format_exc())),
            }


NODE_CLASS_MAPPINGS = {"LLMHubGenerate": LLMHubGenerate}
NODE_DISPLAY_NAME_MAPPINGS = {"LLMHubGenerate": "LLM Hub Generate"}
