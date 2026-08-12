# -*- coding: utf-8 -*-
"""ComfyUI IMAGE 텐서 → PNG 임시 저장 (DESIGN §5-1)."""

from __future__ import annotations

import os
import tempfile

TMP_DIR_NAME = "_llmhub_tmp"


def get_tmp_dir(workspace_dir: str = "", file_access: bool = False) -> str:
    """이미지를 저장할 폴더를 정한다.

    file_access=True 이고 workspace_dir 가 유효하면 workspace_dir/_llmhub_tmp/,
    아니면 ComfyUI temp 폴더(없으면 시스템 temp) 아래를 쓴다.
    """
    if file_access and workspace_dir and os.path.isdir(workspace_dir):
        return os.path.join(workspace_dir, TMP_DIR_NAME)

    base = ""
    try:
        import folder_paths  # ComfyUI 런타임에만 존재

        base = folder_paths.get_temp_directory()
    except Exception:
        base = tempfile.gettempdir()
    return os.path.join(base, TMP_DIR_NAME)


def clear_dir(path: str) -> None:
    """매 실행마다 같은 폴더를 비우고 시작한다 (DESIGN §5-1).

    실행 후에는 지우지 않는다(디버깅 편의).
    """
    if not os.path.isdir(path):
        return
    for name in os.listdir(path):
        full = os.path.join(path, name)
        try:
            if os.path.isfile(full) or os.path.islink(full):
                os.remove(full)
        except OSError:
            pass


def save_images(image, workspace_dir: str = "", file_access: bool = False) -> list:
    """ComfyUI IMAGE 배치를 PNG 로 저장하고 절대경로 리스트를 돌려준다.

    IMAGE 는 [B, H, W, C] float32 (0..1) 텐서다.
    """
    if image is None:
        return []

    out_dir = get_tmp_dir(workspace_dir, file_access)
    os.makedirs(out_dir, exist_ok=True)
    clear_dir(out_dir)

    import numpy as np
    from PIL import Image

    array = image
    if hasattr(array, "cpu"):  # torch.Tensor
        array = array.cpu().numpy()
    array = np.asarray(array)

    if array.ndim == 3:  # 단일 이미지도 배치로 취급
        array = array[None, ...]

    paths = []
    for index in range(array.shape[0]):
        frame = np.clip(array[index] * 255.0, 0, 255).astype(np.uint8)
        if frame.ndim == 3 and frame.shape[2] == 1:
            frame = frame[:, :, 0]
        pil = Image.fromarray(frame)
        if pil.mode not in ("RGB", "RGBA", "L"):
            pil = pil.convert("RGB")
        path = os.path.join(out_dir, f"llmhub_input_{index:02d}.png")
        pil.save(path, format="PNG")
        paths.append(os.path.abspath(path))

    return paths
