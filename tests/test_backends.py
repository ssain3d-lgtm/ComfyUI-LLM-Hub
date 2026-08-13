# -*- coding: utf-8 -*-
"""ComfyUI 없이 단독 실행하는 스모크 테스트 (DESIGN §13).

예)
  python tests/test_backends.py --backend claude --prompt "hello"
  python tests/test_backends.py --backend lmstudio --workspace tests/fixtures --file-access
  python tests/test_backends.py --backend claude --image tests/fixtures/sample.png
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys

# 패키지 이름에 하이픈이 있어 일반 import 문을 쓸 수 없다 → importlib 로 불러온다.
_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_PACK_ROOT))
_PACK_NAME = os.path.basename(_PACK_ROOT)

_backends = importlib.import_module(f"{_PACK_NAME}.backends")
get_backend = _backends.get_backend
LLMRequest = _backends.LLMRequest


def main() -> int:
    parser = argparse.ArgumentParser(description="ComfyUI-LLM-Hub 백엔드 스모크 테스트")
    parser.add_argument(
        "--backend", required=True, choices=list(_backends.BACKEND_NAMES)
    )
    parser.add_argument("--prompt", default="안녕이라고만 답해")
    parser.add_argument("--system", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--workspace", default="")
    parser.add_argument(
        "--file-access", action="store_true", help="워크스페이스 파일 읽기 허용"
    )
    parser.add_argument("--image", action="append", default=[], help="이미지 경로 (반복 가능)")
    parser.add_argument("--video", default="", help="비디오 파일 경로")
    parser.add_argument("--video-max-frames", type=int, default=8,
                        help="비디오 미지원 백엔드에서 뽑을 프레임 수")
    parser.add_argument("--mcp-config", default="")
    parser.add_argument("--extra-args", default="")
    parser.add_argument("--base-url", default="",
                        help="openai_compat 서버 주소 (Ollama/vLLM/llama.cpp)")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    workspace = os.path.abspath(args.workspace) if args.workspace else ""
    images = [os.path.abspath(p) for p in args.image]
    videos = [os.path.abspath(args.video)] if args.video else []

    req = LLMRequest(
        backend=args.backend,
        model=args.model,
        system_prompt=args.system,
        user_prompt=args.prompt,
        image_paths=images,
        video_paths=videos,
        video_max_frames=args.video_max_frames,
        workspace_dir=workspace,
        file_access=bool(args.file_access),
        mcp_config=args.mcp_config,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout_s=args.timeout,
        extra_args=args.extra_args,
        base_url_override=args.base_url,
    )

    print(f"[{args.backend}] 요청 중... (timeout={args.timeout}s, file_access={req.file_access})")
    impl = get_backend(args.backend)
    if hasattr(impl, "apply_base_url"):
        impl.apply_base_url(args.base_url)
    response = impl.generate(req)

    print("=" * 60)
    print("STATUS :", response.status)
    print("TIME   : %.1fs" % response.duration_s)
    print("-" * 60)
    print("TEXT   :")
    print(response.text)
    print("-" * 60)
    print("DEBUG  :")
    print(response.raw_debug)
    print("=" * 60)

    return 0 if response.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
