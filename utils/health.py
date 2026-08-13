# -*- coding: utf-8 -*-
"""자가 진단.

"노드가 안 되는데 뭐가 문제죠" 를 사람이 하나씩 짚어보지 않아도 되게 한다.
브라우저에서 http://127.0.0.1:8188/llmhub/health 를 열면 이 결과가 나온다.

여기서 확인하는 것은 전부 "노드가 실제로 실행 중에 의존하는 것" 이다.
추측성 항목은 넣지 않는다 — 초록 불이 의미가 없어지면 진단 도구가 아니라
장식이 된다.

느린 것(HTTP 왕복)은 짧은 타임아웃을 준다. 진단 페이지가 30초 매달려 있으면
그것부터가 고장으로 보인다.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys

_HTTP_TIMEOUT_S = 2.0

# 진단 대상 CLI. lms 는 LM Studio 의 즉시 VRAM 해제에만 쓰이므로 없어도 치명적이지
# 않다 -- 그 구분을 optional 로 표시한다.
_CLIS = [
    ("claude", False),
    ("codex", False),
    ("gemini", False),
    ("lms", True),
]


def _pack_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _version() -> str:
    try:
        from ..version import __version__

        return __version__
    except Exception:
        return "unknown"


def _check_clis() -> list:
    from .proc import CliNotFoundError, resolve_cli

    out = []
    for name, optional in _CLIS:
        entry = {"name": name, "optional": optional}
        try:
            entry["ok"] = True
            entry["detail"] = resolve_cli(name)
        except CliNotFoundError as exc:
            entry["ok"] = False
            entry["detail"] = str(exc)
        except Exception as exc:  # 진단이 예외로 죽으면 안 된다
            entry["ok"] = False
            entry["detail"] = f"확인 실패: {exc!r}"
        out.append(entry)
    return out


def _check_video() -> list:
    """비디오 프레임 추출 경로. 둘 중 하나만 있으면 된다."""
    out = []

    ffmpeg = shutil.which("ffmpeg")
    out.append({
        "name": "ffmpeg",
        "optional": True,
        "ok": bool(ffmpeg),
        "detail": ffmpeg or "PATH 에 없습니다 (비디오를 안 쓰면 무관)",
    })

    try:
        import cv2  # noqa: F401

        cv2_detail = f"cv2 {getattr(cv2, '__version__', '?')}"
        cv2_ok = True
    except Exception:
        cv2_detail = "설치되어 있지 않습니다 (ffmpeg 이 있으면 무관)"
        cv2_ok = False
    out.append({"name": "opencv (cv2)", "optional": True, "ok": cv2_ok, "detail": cv2_detail})

    return out


def _check_lmstudio() -> dict:
    """LM Studio 서버가 응답하는지, 모델이 몇 개 보이는지."""
    entry = {"name": "LM Studio", "optional": True}
    try:
        from .config import load_config

        base_url = ((load_config().get("lmstudio", {}) or {}).get("base_url")
                    or "http://127.0.0.1:1234")
    except Exception:
        base_url = "http://127.0.0.1:1234"

    try:
        from ..backends.lmstudio import list_model_ids

        # 이 함수는 10초 캐시를 쓴다. LM Studio 를 방금 켰다면 한 번 더 새로고침해야
        # 반영될 수 있다 -- 진단 결과가 순간적으로 낡을 뿐이라 그대로 둔다.
        models = list_model_ids(timeout_s=_HTTP_TIMEOUT_S)
    except Exception as exc:
        entry.update(ok=False, detail=f"{base_url} — 확인 실패: {exc!r}")
        return entry

    if models:
        entry.update(ok=True, detail=f"{base_url} — 모델 {len(models)}개: {', '.join(models[:3])}")
    else:
        entry.update(ok=False, detail=f"{base_url} — 응답 없음 (LM Studio 서버가 꺼져 있거나 포트가 다릅니다)")
    return entry


def _check_frontend() -> dict:
    """JS 확장이 ComfyUI 에 실제로 서빙되는 경로에 있는지.

    WEB_DIRECTORY 가 한 단계 어긋나 있으면 파이썬은 멀쩡히 뜨고 JS 만 통째로
    죽는다. 로그에 아무것도 안 남아서 제일 찾기 어려웠던 버그다 -- 그래서 여기서
    파일 존재 여부를 직접 확인한다.
    """
    root = _pack_root()
    js = os.path.join(root, "web", "js", "llmhub_monitor.js")
    pack = os.path.basename(root)
    return {
        "name": "프론트엔드 JS",
        "optional": False,
        "ok": os.path.isfile(js),
        "detail": (f"/extensions/{pack}/llmhub_monitor.js 로 서빙됩니다"
                   if os.path.isfile(js) else f"파일이 없습니다: {js}"),
    }


def _check_config() -> dict:
    path = os.path.join(_pack_root(), "config.json")
    exists = os.path.isfile(path)
    return {
        "name": "config.json",
        "optional": True,
        "ok": exists,
        "detail": path if exists else "없습니다 (config.example.json 의 기본값으로 동작합니다)",
    }


def collect() -> dict:
    """진단 결과를 모은다. 어떤 항목이 실패해도 전체가 죽지 않는다."""
    checks = []
    checks.append(_check_frontend())
    checks.append(_check_config())
    checks.extend(_check_clis())
    checks.extend(_check_video())
    checks.append(_check_lmstudio())

    # 필수 항목만 전체 판정에 넣는다. ffmpeg 이 없다고 "고장" 이라고 하면
    # 비디오를 안 쓰는 사람에게 거짓 경보가 된다.
    required_failed = [c["name"] for c in checks if not c["optional"] and not c["ok"]]

    return {
        "version": _version(),
        "ok": not required_failed,
        "failed": required_failed,
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()}",
        "pack_dir": _pack_root(),
        "checks": checks,
    }


def as_text(report: dict = None) -> str:
    """브라우저에서 그냥 읽을 수 있는 형태. 그대로 복사해 붙여넣기 좋게."""
    report = report or collect()
    lines = [
        f"ComfyUI-LLM-Hub v{report['version']} 자가 진단",
        "=" * 52,
        f"Python   {report['python']}",
        f"OS       {report['platform']}",
        f"경로     {report['pack_dir']}",
        "",
    ]
    for check in report["checks"]:
        if check["ok"]:
            mark = "[ OK ]"
        else:
            mark = "[ -- ]" if check["optional"] else "[FAIL]"
        lines.append(f"{mark} {check['name']}")
        lines.append(f"       {check['detail']}")

    lines.append("")
    if report["ok"]:
        lines.append("필수 항목은 모두 정상입니다.")
        lines.append("[ -- ] 는 없어도 되는 항목입니다 (해당 기능만 못 씁니다).")
    else:
        lines.append(f"필수 항목 실패: {', '.join(report['failed'])}")
    lines.append("")
    lines.append("모니터 창이 안 보이면: 브라우저에서 Ctrl+Shift+R (하드 새로고침) 후")
    lines.append("F12 콘솔에 '[LLM Hub] v...' 줄이 찍히는지 확인하세요.")
    lines.append("그 줄이 없으면 JS 가 로드되지 않은 것입니다.")
    return "\n".join(lines)
