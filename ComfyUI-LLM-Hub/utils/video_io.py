# -*- coding: utf-8 -*-
"""비디오 입력 처리 — 경로 해석 + 프레임 추출.

백엔드별 비디오 지원 현황(실측):
  - gemini   : 네이티브 지원. Gemini CLI 가 video/* 파일을 inlineData 로 모델에 넘긴다
               (read_file/read_many_files 경로에서 BINARY_INJECTION 으로 재주입).
  - claude   : 미지원. Read 툴은 이미지/PDF 만 받는다.
  - codex    : 미지원. -i/--image 만 있다.
  - lmstudio : 미지원. OpenAI 호환 /v1/chat/completions 에 비디오 콘텐츠 타입이 없다.

그래서 미지원 백엔드에는 프레임을 균등 간격으로 뽑아 이미지로 전달한다.
추출기는 새 pip 의존성 없이 ffmpeg(외부 실행 파일) 또는 이미 깔려 있는 cv2 를 쓴다.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

VIDEO_EXTS = {
    ".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v",
    ".mpeg", ".mpg", ".wmv", ".flv", ".3gp", ".ogv",
}


def is_video(path: str) -> bool:
    return os.path.splitext(path or "")[1].lower() in VIDEO_EXTS


def _creation_flags() -> int:
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _run(args, timeout=120):
    """짧은 보조 프로세스 실행 (shell=False)."""
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_creation_flags(),
    )


# ---------------------------------------------------------------------------
# 경로 해석
# ---------------------------------------------------------------------------


def resolve_video(video_obj=None, video_path: str = "", tmp_dir: str = "") -> tuple:
    """ComfyUI VIDEO 입력 또는 문자열 경로에서 실제 파일 경로를 얻는다.

    반환: (절대경로 또는 "", 안내 메시지)
    """
    path = (video_path or "").strip().strip('"')
    if path:
        if os.path.isfile(path):
            return os.path.abspath(path), ""
        return "", f"video: 파일을 찾을 수 없습니다 → {path}"

    if video_obj is None:
        return "", ""

    # 이미 문자열 경로인 경우
    if isinstance(video_obj, str):
        if os.path.isfile(video_obj):
            return os.path.abspath(video_obj), ""
        return "", f"video: 파일을 찾을 수 없습니다 → {video_obj}"

    # ComfyUI 계열 노드가 dict 로 넘기는 경우
    if isinstance(video_obj, dict):
        for key in ("fullpath", "filename", "path", "video_path"):
            value = video_obj.get(key)
            if value and os.path.isfile(value):
                return os.path.abspath(value), ""

    # ComfyUI VIDEO 타입(VideoInput): save_to() / get_stream_source()
    for attr in ("get_stream_source", "_VideoFromFile__file", "path", "file"):
        try:
            value = getattr(video_obj, attr, None)
            if callable(value):
                value = value()
            if isinstance(value, str) and os.path.isfile(value):
                return os.path.abspath(value), ""
        except Exception:
            pass

    save_to = getattr(video_obj, "save_to", None)
    if callable(save_to) and tmp_dir:
        try:
            os.makedirs(tmp_dir, exist_ok=True)
            dest = os.path.join(tmp_dir, "llmhub_input_video.mp4")
            save_to(dest)
            if os.path.isfile(dest):
                return os.path.abspath(dest), ""
        except Exception as exc:
            return "", f"video: VIDEO 입력 저장 실패 — {type(exc).__name__}: {exc}"

    return "", "video: 지원하지 않는 VIDEO 입력 형식 (video_path 로 파일 경로를 직접 넣어보세요)"


# ---------------------------------------------------------------------------
# 프레임 추출
# ---------------------------------------------------------------------------


def find_extractor() -> str:
    """사용 가능한 추출기를 고른다: "ffmpeg" | "opencv" | ""."""
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    try:
        import cv2  # noqa: F401

        return "opencv"
    except ImportError:
        return ""


def _probe_duration(path: str) -> float:
    """영상 길이(초)를 얻는다. 실패하면 0.

    ffprobe 가 있으면 그걸 쓰고, 없으면 ffmpeg 의 stderr 에 찍히는
    "Duration: 00:00:05.00" 줄을 파싱한다(ffmpeg 만 깔린 환경 대비).
    """
    if shutil.which("ffprobe"):
        try:
            result = _run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    path,
                ],
                timeout=60,
            )
            return float((result.stdout or "0").strip())
        except (ValueError, OSError, subprocess.SubprocessError):
            pass

    # ffprobe 가 없을 때: ffmpeg -i 의 stderr 에서 Duration 을 읽는다.
    try:
        result = _run(["ffmpeg", "-nostdin", "-i", path], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 0.0

    match = re.search(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)", result.stderr or "")
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    try:
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return 0.0


def _extract_ffmpeg(path: str, max_frames: int, out_dir: str) -> tuple:
    duration = _probe_duration(path)
    frames = []

    if duration > 0:
        # 균등 간격으로 max_frames 장을 뽑는다.
        step = duration / (max_frames + 1)
        for index in range(max_frames):
            timestamp = step * (index + 1)
            dest = os.path.join(out_dir, f"llmhub_frame_{index:02d}.png")
            try:
                result = _run(
                    [
                        "ffmpeg", "-nostdin", "-y",
                        "-ss", f"{timestamp:.3f}", "-i", path,
                        "-frames:v", "1", "-q:v", "2", dest,
                    ],
                    timeout=120,
                )
            except subprocess.SubprocessError as exc:
                return frames, f"video: ffmpeg 실행 실패 — {exc}"
            if result.returncode == 0 and os.path.isfile(dest):
                frames.append(os.path.abspath(dest))
        if frames:
            return frames, f"video: ffmpeg 로 {len(frames)}장 추출 (길이 {duration:.1f}s)"

    # 길이를 못 구했으면 앞에서부터 초당 1장씩 뽑아 max_frames 로 자른다.
    # ffmpeg image2 의 -start_number 기본값은 1 이므로 파일도 01 부터 생긴다.
    # -start_number 0 을 명시해 00 부터 만들고, 수집도 00 부터 한다.
    pattern = os.path.join(out_dir, "llmhub_frame_%02d.png")
    try:
        result = _run(
            [
                "ffmpeg", "-nostdin", "-y", "-i", path,
                "-vf", "fps=1", "-frames:v", str(max_frames),
                "-start_number", "0", "-q:v", "2", pattern,
            ],
            timeout=300,
        )
    except subprocess.SubprocessError as exc:
        return [], f"video: ffmpeg 실행 실패 — {exc}"

    if result.returncode != 0:
        tail = "\n".join((result.stderr or "").strip().splitlines()[-5:])
        return [], f"video: ffmpeg 프레임 추출 실패 — {tail}"

    # 실제로 만들어진 파일을 이름순으로 모은다(번호 시작점에 의존하지 않는다).
    for name in sorted(os.listdir(out_dir)):
        if name.startswith("llmhub_frame_") and name.endswith(".png"):
            frames.append(os.path.abspath(os.path.join(out_dir, name)))
    return frames, f"video: ffmpeg 로 {len(frames)}장 추출 (초당 1장)"


def _extract_opencv(path: str, max_frames: int, out_dir: str) -> tuple:
    import cv2

    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        return [], f"video: cv2 가 파일을 열지 못했습니다 → {path}"

    try:
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        frames = []

        if total > 0:
            step = max(total // (max_frames + 1), 1)
            indices = [min(step * (i + 1), total - 1) for i in range(max_frames)]
        else:
            indices = list(range(max_frames))

        for order, frame_index in enumerate(indices):
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok:
                break
            dest = os.path.join(out_dir, f"llmhub_frame_{order:02d}.png")
            if cv2.imwrite(dest, frame):
                frames.append(os.path.abspath(dest))

        if not frames:
            return [], "video: cv2 로 프레임을 읽지 못했습니다"
        return frames, f"video: cv2 로 {len(frames)}장 추출 (총 {total} 프레임)"
    finally:
        capture.release()


def extract_frames(path: str, max_frames: int = 8, out_dir: str = "") -> tuple:
    """비디오에서 균등 간격 프레임을 뽑아 PNG 로 저장한다.

    반환: (PNG 절대경로 리스트, 안내 메시지)
    """
    if not path or not os.path.isfile(path):
        return [], f"video: 파일을 찾을 수 없습니다 → {path}"

    max_frames = max(1, int(max_frames))
    out_dir = out_dir or os.path.join(os.path.dirname(path), "_llmhub_frames")
    os.makedirs(out_dir, exist_ok=True)
    # 이전 실행(다른 영상)의 프레임이 섞이지 않게 먼저 비운다.
    for name in os.listdir(out_dir):
        if name.startswith("llmhub_frame_") and name.endswith(".png"):
            try:
                os.remove(os.path.join(out_dir, name))
            except OSError:
                pass

    extractor = find_extractor()
    if extractor == "ffmpeg":
        return _extract_ffmpeg(path, max_frames, out_dir)
    if extractor == "opencv":
        try:
            return _extract_opencv(path, max_frames, out_dir)
        except Exception as exc:
            return [], f"video: cv2 프레임 추출 실패 — {type(exc).__name__}: {exc}"

    return [], (
        "video: 프레임 추출기가 없습니다. ffmpeg 를 설치하고 PATH 에 추가하세요 "
        "(https://ffmpeg.org/download.html). ComfyUI 환경에 opencv-python 이 있으면 그것도 쓸 수 있습니다."
    )
