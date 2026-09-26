# -*- coding: utf-8 -*-
"""ComfyUI 노드 정의 (DESIGN §5)."""

from __future__ import annotations

import traceback

from .backends import BACKEND_NAMES, OPENAI_COMPAT_ALIASES, get_backend
from .backends.base import LLMRequest, parse_extra_body, truncate_debug
from .backends.lmstudio import list_model_ids
from .backends.openai_compat import list_server_models
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

# 위젯이 화면에 놓이는 순서 = ComfyUI 가 widgets_values 배열에 값을 저장하는 순서.
# 여기에 새 이름은 반드시 "맨 뒤에만" 붙인다.
#
# 이걸 명시적으로 적어두는 이유: openai_base_url 을 중간에 끼워 넣은 적이 있는데,
# 그때 이전에 저장한 워크플로우의 값이 전부 한 칸씩 밀려 노드가 죽었다. INPUT_TYPES
# 안에서는 순서가 "그냥 코드를 적은 자리" 로 보여서 눈에 안 띈다 -- 이름을 따로
# 나열해두면 테스트가 어긋남을 잡아준다.
#
# IMAGE / VIDEO 는 링크 입력이라 위젯이 아니고 배열 자리도 차지하지 않는다.
# control_after_generate 는 seed 옵션을 보고 프론트엔드가 만들어 붙이는 짝꿍이다.
WIDGET_ORDER = [
    "backend", "prompt", "system_prompt", "model", "file_access", "workspace_dir",
    "temperature", "max_tokens", "timeout_sec", "seed", "control_after_generate",
    "video_max_frames", "stream_view",
    "video_path", "mcp_config", "extra_args",
    "lmstudio_model", "lmstudio_ttl_sec", "lmstudio_unload_after", "claude_model",
    "openai_base_url", "system_preset", "batch_mode", "extra_body", "server_model",
    "unload_after_generation",
]

# 릴리스별로 "그때 배포된 순서" 를 그대로 박아둔 원장.
#
# 왜 필요한가: WIDGET_ORDER 하나만으로는 규칙이 강제되지 않는다. 테스트가
# INPUT_TYPES 에서 뽑은 순서와 WIDGET_ORDER 를 비교하는데, 중간에 위젯을 끼우면서
# **양쪽을 똑같이** 고치면 둘은 여전히 일치한다 -- 테스트는 통과하고, 정작 저장된
# 워크플로우만 값이 밀린다. 실제로 재현되는 구멍이었다(v1.1 이후 추가된 뒤쪽 5개는
# 아무 테스트도 붙잡고 있지 않았다).
#
# 과거 접두사를 그대로 남겨두면 "맨 뒤에만 붙인다" 가 검사 가능한 명제가 된다.
# 새 릴리스를 낼 때 그 시점의 전체 목록을 한 줄 추가하면 된다. 기존 항목은
# 절대 고치지 않는다 -- 고치는 순간 그 릴리스로 저장된 워크플로우를 버리는 것이다.
FROZEN_WIDGET_ORDERS = (
    # v1.0.0
    (
        "backend", "prompt", "system_prompt", "model", "file_access", "workspace_dir",
        "temperature", "max_tokens", "timeout_sec", "seed", "control_after_generate",
        "video_max_frames", "stream_view",
        "video_path", "mcp_config", "extra_args",
    ),
    # v1.0.0 이후, lmstudio/claude 전용 위젯이 붙은 시점 (20칸 워크플로우)
    (
        "backend", "prompt", "system_prompt", "model", "file_access", "workspace_dir",
        "temperature", "max_tokens", "timeout_sec", "seed", "control_after_generate",
        "video_max_frames", "stream_view",
        "video_path", "mcp_config", "extra_args",
        "lmstudio_model", "lmstudio_ttl_sec", "lmstudio_unload_after", "claude_model",
    ),
    # v1.1.0 (릴리스 태그 시점, 24칸)
    (
        "backend", "prompt", "system_prompt", "model", "file_access", "workspace_dir",
        "temperature", "max_tokens", "timeout_sec", "seed", "control_after_generate",
        "video_max_frames", "stream_view",
        "video_path", "mcp_config", "extra_args",
        "lmstudio_model", "lmstudio_ttl_sec", "lmstudio_unload_after", "claude_model",
        "openai_base_url", "system_preset", "batch_mode", "extra_body",
    ),
    # v1.2.0 (25칸)
    (
        "backend", "prompt", "system_prompt", "model", "file_access", "workspace_dir",
        "temperature", "max_tokens", "timeout_sec", "seed", "control_after_generate",
        "video_max_frames", "stream_view",
        "video_path", "mcp_config", "extra_args",
        "lmstudio_model", "lmstudio_ttl_sec", "lmstudio_unload_after", "claude_model",
        "openai_base_url", "system_preset", "batch_mode", "extra_body", "server_model",
    ),
)

# server_model 드롭다운을 쓰는 백엔드 = openai_compat 과 그 별칭들.
OPENAI_COMPAT_BACKENDS = ("openai_compat",) + tuple(OPENAI_COMPAT_ALIASES)

# 이미지 배치를 어떻게 다룰지.
#   all_in_one    : 배치 전체를 한 요청에 넣는다 (지금까지의 동작).
#   one_per_image : 이미지 한 장씩 따로 호출하고 결과를 이어 붙인다.
# 데이터셋 캡션처럼 "장당 한 줄" 이 필요한 경우가 all_in_one 으로는 불가능했다 --
# 40장을 물리면 40장이 한 요청에 다 들어가고 답이 하나만 나왔다.
BATCH_ALL = "all_in_one"
BATCH_PER_IMAGE = "one_per_image"
BATCH_MODES = [BATCH_ALL, BATCH_PER_IMAGE]

# one_per_image 결과를 잇는 구분자. 다운스트림에서 이 문자열로 잘라 쓰라고
# README 에 적어둔다. 일반 문장에 잘 안 나오는 모양을 골랐다.
BATCH_SEPARATOR = "\n\n=====\n\n"

# 사용자가 Stop 을 눌렀을 때의 status. 상수로 빼둔 이유: 프론트엔드가 이 접두사
# ("stopped")를 보고 모니터 본문에 빨갛게 올릴지 정한다. 문구를 고치다 접두사가
# 바뀌면 화면에서 조용히 사라지므로 테스트가 이 값을 직접 읽어 검사한다.
STOPPED_STATUS = "stopped - cancelled by user, returning what arrived so far"


def _as_text(value) -> str:
    """위젯에서 온 값을 문자열로 안전하게 받는다.

    widgets_values 는 위치로 읽히므로, 위젯 순서가 한 번이라도 어긋났던
    워크플로우를 열면 엉뚱한 타입이 들어온다(실제로 bool 이 들어와 .strip()
    에서 노드가 죽었다). 노드는 어떤 입력에도 예외를 던지지 않아야 하므로
    (DESIGN N4) 문자열이 아니면 빈 값으로 본다.
    """
    return value.strip() if isinstance(value, str) else ""


def _as_number(value, fallback, cast=int):
    """숫자 위젯 값을 안전하게 받는다 (_as_text 의 숫자판, DESIGN N4).

    위젯 순서가 한 번이라도 어긋났던 워크플로우를 열면 숫자 자리에 문자열이
    들어온다. int("(auto)") 는 ValueError 를 던지고, 그러면 노드가 통째로
    죽는다 -- .strip() 에서 죽던 것과 똑같은 사고다.
    """
    try:
        return cast(value)
    except (TypeError, ValueError):
        return fallback


def _batch_status(statuses, total: int) -> str:
    """one_per_image 의 장별 상태를 한 줄로 요약한다.

    실패한 장이 하나라도 있으면 ok 라고 하지 않는다 -- 40장 중 3장이 조용히
    빈 문자열이 되면 다운스트림이 그대로 저장해버린다.
    """
    if len(statuses) < total:
        return (
            f"stopped - {len(statuses)}/{total} images done, "
            "returning what arrived so far"
        )
    stopped = [s for s in statuses if s.startswith("stopped")]
    if stopped:
        return f"{stopped[0]} ({total} images)"
    failed = [s for s in statuses if not s.startswith("ok")]
    if failed:
        return f"error: {len(failed)}/{total} images failed - {failed[0]}"
    return f"ok - {total} images"


def _ls_default(key, fallback, section="lmstudio"):
    """config.json 의 설정(기본 lmstudio 섹션)을 위젯 기본값으로 쓴다.

    설정 파일 값이 실제로 노드에 반영되게 하려면 여기서 읽어야 한다.
    """
    try:
        from .utils.config import load_config

        value = (load_config().get(section, {}) or {}).get(key)
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
                    "tooltip": "Which LLM to use. claude/codex/gemini = subscription CLIs; "
                               "everything else talks to a local server over HTTP. "
                               "ollama / vllm / llamacpp / ninfer are the same backend as "
                               "openai_compat, just preset to that server's standard port "
                               "(11434 / 8000 / 8080 / 8081) so you do not have to type it. "
                               "Each backend must already be installed and running.",
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
                # 캐시 무효화용이자, HTTP 백엔드에서는 실제 샘플링 시드다
                # (0 이면 안 보낸다). DESIGN §5-4 의 수정 주석 참고.
                "seed": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 0xFFFFFFFF,
                        "control_after_generate": True,
                        "tooltip": "Busts ComfyUI's cache so the same prompt runs again "
                                   "instead of reusing the last result. On lmstudio/"
                                   "openai_compat a non-zero value is also sent to the "
                                   "server as the sampling seed, so the same seed can "
                                   "reproduce the same text; 0 sends nothing. The three "
                                   "CLIs have no seed flag and ignore it.",
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
                # 이 위젯은 원래 lmstudio_ttl_sec 바로 뒤에 끼워 넣었었다. 그래서
                # openai_compat 병합 이전에 저장한 워크플로우는 값이 한 칸씩 밀려
                # openai_base_url 에 True 가, lmstudio_unload_after 에 "(auto)" 가
                # 들어갔고, True.strip() 에서 노드가 통째로 죽었다(재현 확인).
                # 예제 워크플로우 3개도 전부 그 상태였다.
                # 규칙대로 맨 뒤로 옮긴다. 순서는 WIDGET_ORDER 로 고정한다.
                "openai_base_url": ("STRING", {
                    "default": "",
                    "tooltip": "Address of the OpenAI-compatible server. Fill this in only "
                               "when the server is not at the standard port — for the "
                               "ollama / vllm / llamacpp / ninfer backends it is already set "
                               "(11434 / 8000 / 8080 / 8081), and for openai_compat an empty box "
                               "uses openai_compat.base_url from config.json. "
                               "Leave '/v1' off the end; the node appends "
                               "/v1/chat/completions itself."}),
                #
                # system_prompt 바로 밑에 두는 편이 자연스럽지만 그렇게 못 한다.
                # 위젯 순서가 곧 widgets_values 의 순서라, 중간에 끼우면 이 노드로
                # 저장해둔 예전 워크플로우의 값이 전부 한 칸씩 밀린다.
                "system_preset": (presets.preset_names(), {
                    "tooltip": "Load a saved system prompt. Picking one replaces the "
                               "system_prompt box above with the saved text. The pencil "
                               "button on the node title bar opens a larger window for "
                               "writing, pasting and saving presets."}),
                # --- 나중에 추가된 위젯 (반드시 맨 뒤에 붙인다) ---
                "batch_mode": (BATCH_MODES, {
                    "tooltip": "How to handle an image batch. all_in_one = every image "
                               "goes into a single request (one answer). one_per_image = "
                               "one request per image, results joined by a '=====' line, "
                               "in the same order as the batch — this is what you want for "
                               "captioning a dataset. one_per_image costs N calls, so with "
                               "the CLI backends it is N times the price and N cold starts. "
                               "Ignored when there is a video input or only one image."}),
                "extra_body": ("STRING", {"default": "", "multiline": True,
                    "tooltip": "[lmstudio/openai_compat only] Extra JSON fields merged into "
                               "the request body — the HTTP counterpart of extra_args. "
                               'Example: {"top_p": 0.9, "response_format": {"type": '
                               '"json_object"}}. Invalid JSON stops the run with an error '
                               "instead of being ignored. 'messages' and 'stream' are built "
                               "by the node and cannot be overridden."}),
                # --- 나중에 추가된 위젯 (반드시 맨 뒤에 붙인다) ---
                "server_model": ([AUTO_MODEL] + list_server_models(), {
                    "tooltip": "[openai_compat/ollama/vllm/llamacpp/ninfer] Model dropdown, read "
                               "from whichever of those servers is running on this machine. "
                               "Start the server, then press the refresh button on the title "
                               "bar. (auto) = follow the model field above. Only local "
                               "servers are listed — for a remote or paid endpoint, type the "
                               "name into model instead. ninfer: the .ninfer models of the "
                               "NInfer config UI are listed even while the server is off — "
                               "pick one and Run starts NInfer with it."}),
                # --- 나중에 추가된 위젯 (반드시 맨 뒤에 붙인다) ---
                #
                # lmstudio_unload_after 의 OpenAI 호환 서버판. 이름을 따로 둔 이유:
                # 그 위젯의 기본값(True)은 LM Studio 설정에서 오고, 서버마다 언로드
                # 수단이 달라서(없는 서버도 있다) 기본값도 달라야 한다.
                "unload_after_generation": ("BOOLEAN", {
                    "default": _ls_default("unload_after", False, section="openai_compat"),
                    "tooltip": "[openai_compat/ollama/vllm/llamacpp/ninfer] Load the model, "
                               "generate, then unload it right away so the VRAM is free for "
                               "the image model. The model is loaded again on the next run. "
                               "Ollama: keep_alive 0. llama.cpp: router mode only "
                               "(/models/unload). vLLM: sleep mode (needs --enable-sleep-mode "
                               "and VLLM_SERVER_DEV_MODE=1; woken up before the next run). "
                               "NInfer: the server is started on Run and stopped after the "
                               "answer (through the NInfer config UI). What happened is "
                               "written to debug."}),
            },
            # 모니터링 창이 어느 노드에 그려질지 알기 위해 노드 id 를 받는다.
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    @classmethod
    def VALIDATE_INPUTS(cls, input_types=None, lmstudio_model=None, system_preset=None,
                        server_model=None):
        """가변 목록인 두 입력만 검증을 건너뛴다(나머지는 정상 검증됨).

        lmstudio_model / server_model 은 서버를 조회해 만든 목록이라 서버가
        꺼졌거나 모델이 언로드되면 줄어든다. system_preset 은 프리셋 파일에서
        오므로 편집창에서 프리셋을 지우거나 이름을 바꾸면 줄어든다.

        그때 ComfyUI 기본 검증이 저장된 값을 거부해 워크플로우 실행 자체가
        실패한다 -- 해당 기능을 안 쓰는 경우까지 같이 죽는다.
        → 이 둘만 통과시킨다. system_preset 은 화면 전용이라 목록에 없는 이름이
          남아 있어도 생성에는 아무 영향이 없다.

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
        server_model=AUTO_MODEL,
        batch_mode=BATCH_ALL,
        extra_body="",
        unload_after_generation=False,
        unique_id=None,
    ):
        # 노드는 어떤 경우에도 예외를 밖으로 던지지 않는다 (DESIGN N4, §5-3).
        emitter = None
        # 지난 실행에서 Stop 을 눌렀다면 그 표시를 지운다. 안 지우면 한 번 멈춘
        # 노드가 영원히 즉시 중지된다.
        cancel.begin(unique_id)
        try:
            workspace_dir = _as_text(workspace_dir)

            # extra_body 는 조용히 버리지 않는다. 잘못 적었으면 아무 일도 하기
            # 전에 멈춰서 그렇게 말해준다 -- extra_args 가 HTTP 백엔드에서 debug
            # 한 줄만 남기고 사라지던 것이 이 위젯을 만든 이유다.
            # 이미지를 저장하기 전에 본다. 40장짜리 배치를 다 써놓고 JSON 오타로
            # 끝내면 그 쓰기가 전부 헛일이다.
            extra_body_dict, extra_body_error = parse_extra_body(_as_text(extra_body))
            if extra_body_error:
                early = stream.make_emitter(
                    node_id=unique_id, enabled=(stream_view != "off")
                )
                early.finish(status=extra_body_error, text="")
                return {
                    "ui": {"text": [""], "llmhub_status": [extra_body_error]},
                    "result": ("", extra_body_error, _as_text(extra_body)),
                }

            image_paths = []
            video_paths = []
            media_notes = []

            # system_preset 은 화면 전용이다. 프리셋을 고르면 프론트엔드가 그
            # 본문을 system_prompt 칸에 그대로 채워 넣으므로, 여기서 다시 합치면
            # 같은 문장이 두 번 들어간다. 이 위젯의 값은 "마지막에 무엇을
            # 불러왔는지" 를 워크플로우에 남기는 표시일 뿐이다.

            if image is not None:
                try:
                    image_paths = image_io.save_images(
                        image, workspace_dir, bool(file_access)
                    )
                except Exception as exc:
                    media_notes.append(f"image: failed to save PNG - {type(exc).__name__}: {exc}")

            if video is not None or _as_text(video_path):
                tmp_dir = image_io.get_tmp_dir(workspace_dir, bool(file_access))
                resolved, note = video_io.resolve_video(video, video_path, tmp_dir)
                if note:
                    media_notes.append(note)
                if resolved:
                    video_paths = [resolved]

            emitter = stream.make_emitter(
                node_id=unique_id, enabled=(stream_view != "off")
            )

            # 붙인 미디어가 모델에 못 갔으면 생성을 시작하지 않는다.
            #
            # 예전에는 media_notes 에만 적고 그대로 진행했다. 그러면 모델은 그림을
            # 못 본 채 프롬프트만으로 답하고 status 는 ok 로 나온다 -- 사용자는
            # 그림을 보고 쓴 답이라고 믿는다. 캡션 작업이라면 환각 캡션이 전부
            # 저장된다. 흔한 원인: workspace_dir 이 읽기 전용이거나 video_path 오타.
            #
            # "잘린 결과를 성공으로 위장하지 않는다" 는 이 프로젝트의 규칙을
            # 미디어에도 똑같이 적용한다.
            media_error = ""
            if image is not None and not image_paths:
                media_error = media_notes[0] if media_notes else (
                    "image: no PNG was produced from the IMAGE input"
                )
            elif (video is not None or _as_text(video_path)) and not video_paths:
                media_error = media_notes[0] if media_notes else (
                    "video: the video input could not be resolved to a file"
                )
            if media_error:
                status_out = f"error: {media_error}"
                emitter.finish(status=status_out, text="")
                return {
                    "ui": {"text": [""], "llmhub_status": [status_out]},
                    "result": ("", status_out, truncate_debug("\n".join(media_notes))),
                }

            emitter.set_status(f"{backend} starting...")

            # 백엔드별 드롭다운에서 고른 모델이 있으면 그쪽이 우선한다.
            # 드롭다운은 그 백엔드에서만 본다 — claude 를 쓰는데 lmstudio_model 이
            # 남아 있다고 그걸 집어가면 안 된다.
            chosen_model = _as_text(model)
            if backend == "lmstudio" and lmstudio_model and lmstudio_model != AUTO_MODEL:
                chosen_model = lmstudio_model
            elif backend == "claude" and claude_model and claude_model != AUTO_MODEL:
                chosen_model = claude_model
            elif (
                backend in OPENAI_COMPAT_BACKENDS
                and server_model
                and server_model != AUTO_MODEL
            ):
                chosen_model = server_model

            # 이미지 배치를 장별로 쪼갤지 결정한다.
            # 비디오가 있으면 쪼개지 않는다 -- 프레임들은 한 영상의 조각이라
            # 따로따로 물어보면 의미가 무너진다.
            per_image = (
                batch_mode == BATCH_PER_IMAGE
                and len(image_paths) > 1
                and not video_paths
            )
            runs = [[path] for path in image_paths] if per_image else [image_paths]

            impl = get_backend(backend)

            # openai_compat 만 노드에서 주소를 갈아탈 수 있다.
            # 루프 밖에서 한 번만 한다 -- 장마다 같은 값이고, 주소가 틀렸으면
            # 40장을 저장해 두고 첫 장에서 멈추는 것보다 여기서 멈추는 게 낫다.
            # 잘못된 주소(스킴 없음)는 extra_body 오타와 같은 모양으로 status 에
            # 말해준다. 바깥 except 로 흘리면 "internal node error" 가 되어
            # 사용자 입력 문제가 버그처럼 보인다.
            if hasattr(impl, "apply_base_url"):
                try:
                    impl.apply_base_url(_as_text(openai_base_url))
                except ValueError as exc:
                    status_out = f"error: {exc}"
                    emitter.finish(status=status_out, text="")
                    return {
                        "ui": {"text": [""], "llmhub_status": [status_out]},
                        "result": ("", status_out, _as_text(openai_base_url)),
                    }

            # 생성 후 VRAM 에서 모델을 내릴지. 백엔드마다 자기 위젯만 본다.
            # None = 백엔드가 자기 config 를 따른다(CLI 3종에는 언로드가 없다).
            #
            # lmstudio_* 위젯은 LM Studio 에서만 보인다(프론트엔드의 BACKEND_ONLY).
            # 그런데 값 자체는 항상 넘어오므로, 다른 백엔드에 흘리면 화면에 없는
            # 위젯이 동작을 바꾼다. OpenAI 호환 서버는 unload_after_generation 을 본다.
            if backend == "lmstudio":
                unload_wanted = bool(lmstudio_unload_after)
            elif backend in OPENAI_COMPAT_BACKENDS:
                unload_wanted = bool(unload_after_generation)
            else:
                unload_wanted = None

            texts, statuses, run_debug = [], [], []
            total_duration = 0.0

            for index, run_images in enumerate(runs):
                if per_image:
                    # 장 사이마다 확인한다. 40장짜리 배치에서 Stop 이 안 들으면
                    # 멈출 방법이 없다.
                    if cancel.is_stopped(unique_id):
                        break
                    emitter.set_status(f"{backend} image {index + 1}/{len(runs)}...")

                req = LLMRequest(
                    backend=backend,
                    model=chosen_model,
                    # 다른 문자열 위젯은 모두 _as_text 를 거치는데 이 둘만 빠져
                    # 있었다. 하필 인덱스 1~2 -- widgets_values 가 밀리면 제일
                    # 먼저 당하는 자리다. 백엔드의 except 가 잡아주긴 하지만,
                    # status 가 "AttributeError: 'bool' object has no attribute
                    # 'strip'" 같은 내부 오류로 나간다. 그걸 없애려고 만든 것이
                    # _as_text 다.
                    system_prompt=_as_text(system_prompt),
                    user_prompt=_as_text(prompt),
                    image_paths=run_images,
                    video_paths=video_paths,
                    video_max_frames=_as_number(video_max_frames, 8),
                    workspace_dir=workspace_dir,
                    file_access=bool(file_access),
                    mcp_config=_as_text(mcp_config),
                    temperature=_as_number(temperature, 0.7, float),
                    max_tokens=_as_number(max_tokens, 2048),
                    timeout_s=_as_number(timeout_sec, 300),
                    seed=_as_number(seed, 0),
                    extra_args=_as_text(extra_args),
                    extra_body=extra_body_dict,
                    base_url_override=_as_text(openai_base_url),
                    # lmstudio_ttl_sec 도 LM Studio 전용이다(위의 unload_wanted
                    # 주석 참고). None 을 주면 각 백엔드가 자기 설정을 따른다.
                    ttl_sec=(
                        _as_number(lmstudio_ttl_sec, 300)
                        if backend == "lmstudio" else None
                    ),
                    # one_per_image 는 장마다 백엔드를 부르는데, 백엔드는 호출마다
                    # VRAM 에서 모델을 내린다. 그대로 두면 40장짜리 캡션 작업이
                    # 모델을 40번 다시 로드한다 -- 이 모드를 만든 이유가 바로 그
                    # 작업인데 가장 느린 방식이 되는 셈이다. 마지막 장에서만 내린다.
                    # (중간에 Stop 으로 끊기면 루프 뒤에서 대신 내린다.)
                    unload_after=(
                        None if unload_wanted is None
                        else unload_wanted and (index == len(runs) - 1)
                    ),
                    emitter=emitter,
                )

                response = impl.generate(req)

                # 실패한 장도 빈 문자열로 자리를 채운다. 안 그러면 결과의 n번째가
                # 이미지의 n번째와 어긋나 캡션이 엉뚱한 그림에 붙는다.
                texts.append(response.text or "")
                statuses.append(response.status)
                total_duration += response.duration_s
                if per_image:
                    run_debug.append(f"[{index + 1}] {response.status}")
                if response.raw_debug:
                    run_debug.append(response.raw_debug)

            # 장별 루프가 Stop 으로 중간에 끊기면 "마지막 장에서 내린다" 가 오지
            # 않는다. 그대로 두면 모델이 VRAM 에 남아 다음 이미지 모델이 밀려난다.
            # 한 장이라도 돌았을 때만 내린다(안 돌았으면 올린 적도 없다).
            if (
                unload_wanted
                and statuses
                and len(statuses) < len(runs)
                and hasattr(impl, "unload_model")
            ):
                try:
                    run_debug.append(impl.unload_model(
                        getattr(impl, "_served_model", "") or chosen_model
                    ))
                except Exception as exc:
                    run_debug.append(f"unload: failed - {type(exc).__name__}: {exc}")

            if per_image:
                # 중간에 멈췄으면 남은 자리를 채워 순서를 유지한다.
                texts += [""] * (len(runs) - len(texts))
                text_out = BATCH_SEPARATOR.join(texts)
                status_out = _batch_status(statuses, len(runs))
            else:
                text_out = texts[0] if texts else ""
                status_out = statuses[0] if statuses else "error: the backend returned nothing"

            # 백엔드마다 중지가 다른 모양으로 끝난다(SSE 중단 / 프로세스 사망 →
            # "종료 코드 -1" 등). 사용자가 누른 것은 오류가 아니므로 여기 한 곳에서
            # 통일해 말해준다. 받은 데까지는 그대로 둔다.
            if cancel.is_stopped(unique_id) and not status_out.startswith("stopped"):
                status_out = STOPPED_STATUS

            emitter.finish(status=status_out, text=text_out or emitter.text)

            debug_parts = list(media_notes)
            if image_paths:
                debug_parts.append(f"images: {len(image_paths)} saved -> {image_paths[0]}")
            if per_image:
                debug_parts.append(
                    f"batch_mode=one_per_image: {len(runs)} images -> {len(runs)} calls, "
                    f"results joined by the '=====' line"
                )
            if video_paths:
                debug_parts.append(f"video: {video_paths[0]}")
            debug_parts.append(f"backend={backend} duration={total_duration:.1f}s")
            debug_parts.extend(run_debug)
            debug_out = truncate_debug("\n".join(p for p in debug_parts if p))
            return {
                # ui 는 노드에 표시되고 워크플로우에 저장된다.
                "ui": {"text": [text_out], "llmhub_status": [status_out]},
                # result 는 그대로 아래 노드로 흐른다(터미널 노드가 되지 않게).
                "result": (text_out, status_out, debug_out),
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
