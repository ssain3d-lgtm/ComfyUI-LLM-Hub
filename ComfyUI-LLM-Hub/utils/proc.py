# -*- coding: utf-8 -*-
"""서브프로세스 공통 러너 (DESIGN §9).

규칙:
- shell=True 절대 금지. 인자는 항상 리스트로 전달한다.
- Windows 에서 콘솔 창이 뜨지 않도록 CREATE_NO_WINDOW 를 준다.
- Windows 의 claude/codex/gemini 는 .cmd 셔틀인 경우가 많으므로
  shutil.which() 로 해석한 실제 경로를 사용해야 shell=False 로 실행된다.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from .config import get_cli_path


class CliNotFoundError(RuntimeError):
    """PATH 에도 config.json 의 cli_paths 에도 실행 파일이 없을 때."""


def resolve_cli(name: str) -> str:
    """CLI 실행 파일의 실제 경로를 해석한다 (PATH 우선, 그다음 config.json).

    Windows 에서 claude/codex/gemini 는 .cmd/.ps1 셔틀일 수 있으므로
    which() 가 해석한 전체 경로를 그대로 쓴다.
    """
    found = shutil.which(name)
    if found:
        return found

    configured = get_cli_path(name)
    if configured and configured != name:
        if os.path.isabs(configured) and os.path.exists(configured):
            return configured
        found = shutil.which(configured)
        if found:
            return found

    raise CliNotFoundError(
        f"'{name}' 실행 파일을 찾을 수 없습니다. "
        f"PATH 에 추가하거나 config.json 의 cli_paths.{name} 에 절대경로를 지정하세요."
    )


def build_env() -> dict:
    """부모 환경 상속 + PYTHONIOENCODING=utf-8 (DESIGN §9)."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _creation_flags() -> int:
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def parse_extra_args(extra_args: str) -> list:
    """extra_args 문자열을 argv 리스트로 파싱한다 (DESIGN §9).

    Windows 경로의 역슬래시가 이스케이프로 먹히지 않도록 posix=False 를 쓴다.
    """
    if not extra_args or not extra_args.strip():
        return []
    try:
        return shlex.split(extra_args, posix=False)
    except ValueError:
        return extra_args.split()


def run_cli(args: list, *, cwd: str, stdin_text=None, timeout_s: int = 300):
    """CLI 를 실행하고 (exit_code, stdout, stderr, duration_s) 를 돌려준다.

    타임아웃이면 exit_code = -1, stderr 에 "error: timeout(Ns)" 를 채운다.
    좀비 프로세스가 남지 않도록 kill 후 반드시 회수한다 (T5).
    """
    started = time.time()
    proc = subprocess.Popen(
        args,
        cwd=cwd or None,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=build_env(),
        creationflags=_creation_flags(),
    )

    try:
        stdout, stderr = proc.communicate(input=stdin_text, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            # kill 후 파이프를 비워 좀비/파이프 잔류를 방지한다.
            stdout, stderr = proc.communicate(timeout=10)
        except Exception:
            stdout, stderr = "", ""
        duration = time.time() - started
        stderr = (stderr or "") + f"\nerror: timeout({timeout_s}s)"
        return -1, stdout or "", stderr, duration

    duration = time.time() - started
    return proc.returncode, stdout or "", stderr or "", duration


def make_empty_dir() -> str:
    """file_access=False 일 때 cwd 로 쓸 빈 임시 폴더 (DESIGN §7)."""
    return tempfile.mkdtemp(prefix="llmhub_empty_")


def cleanup_dir(path: str) -> None:
    """make_empty_dir() 로 만든 폴더를 조용히 정리한다."""
    if not path:
        return
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass
