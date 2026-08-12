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


# 읽기 전용 샌드박스를 푸는 위험 플래그. extra_args 로 들어오면 거부한다.
# (claude Bash 툴 허용 / codex 샌드박스 해제 / gemini yolo 등 → LAN 노출 시 RCE 표면)
# 접두사 마커: 뒤에 무엇이 붙든 차단 (--dangerously-skip-permissions 등)
_UNSAFE_PREFIX_MARKERS = ("--dangerously",)
# 정확 마커: 플래그가 정확히 일치하거나 --flag=value 형태면 차단하고,
#           "--flag value" 형태면 뒤따르는 값 토큰도 함께 버린다.
_UNSAFE_EXACT_MARKERS = (
    "--allowedtools", "--allowed-tools", "--tools",
    "--disallowedtools", "--disallowed-tools", "--permission-mode",
    "--sandbox", "-s", "--approval-mode", "--yolo", "-y",
    "--add-dir", "--mcp-config", "--agents", "--agent",
    "--setting", "--settings", "-c", "--config", "--enable",
)


def screen_extra_args(tokens: list) -> tuple:
    """위험 플래그를 걸러낸다. 반환: (안전한 토큰, 거부된 토큰).

    개별 실행이 노드가 강제한 읽기 전용 잠금을 스스로 풀지 못하게 한다.
    꼭 필요하면 config.json 의 allow_unsafe_extra_args=true 로 열 수 있다(§보안).
    """
    from .config import load_config

    if load_config().get("allow_unsafe_extra_args"):
        return list(tokens), []

    safe, rejected = [], []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        low = token.lower()

        if any(low.startswith(m) for m in _UNSAFE_PREFIX_MARKERS):
            rejected.append(token)
            i += 1
            continue

        exact = low in _UNSAFE_EXACT_MARKERS
        with_value = any(low.startswith(m + "=") for m in _UNSAFE_EXACT_MARKERS)
        if exact or with_value:
            rejected.append(token)
            # "--flag value" 형태면 다음 토큰(값)도 함께 버린다.
            # 단 다음 토큰이 또 다른 플래그(-)면 값이 아니므로 남긴다.
            if exact and i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                rejected.append(tokens[i + 1])
                i += 2
                continue
            i += 1
            continue

        safe.append(token)
        i += 1
    return safe, rejected


def parse_extra_args(extra_args: str) -> list:
    """extra_args 문자열을 argv 리스트로 파싱한다 (DESIGN §9).

    Windows 에서만 posix=False (역슬래시 경로 보호). 리눅스/맥에서 posix=False 를
    쓰면 따옴표가 argv 에 그대로 남는 문제가 있어 플랫폼으로 가른다.
    """
    if not extra_args or not extra_args.strip():
        return []
    try:
        return shlex.split(extra_args, posix=(sys.platform != "win32"))
    except ValueError:
        return extra_args.split()


def _kill_tree(proc) -> None:
    """프로세스와 그 자식들을 함께 죽인다.

    Windows 의 claude/codex/gemini 는 .cmd 셔틀이라 proc.kill() 은 cmd.exe 만
    죽이고 실제 node.exe 는 살아남는다 → taskkill /T 로 트리 전체를 정리한다.
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=15, creationflags=_creation_flags(),
            )
            return
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass


def run_cli(args: list, *, cwd: str, stdin_text=None, timeout_s: int = 300):
    """CLI 를 실행하고 (exit_code, stdout, stderr, duration_s) 를 돌려준다.

    타임아웃이면 exit_code = -1, stderr 에 "error: timeout(Ns)" 를 채운다.
    좀비 프로세스가 남지 않도록 트리 전체를 kill 후 반드시 회수한다 (T5).
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
        _kill_tree(proc)
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


def run_cli_stream(args: list, *, cwd: str, stdin_text=None, timeout_s: int = 300, on_line=None):
    """run_cli 와 같지만 stdout 을 한 줄씩 읽으며 on_line(line) 을 호출한다.

    스트리밍 출력(JSONL/SSE)을 실시간으로 노드에 흘려보내기 위한 변형이다.
    반환값은 run_cli 와 동일한 (exit_code, stdout, stderr, duration_s).
    """
    import threading

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

    out_chunks = []
    err_chunks = []

    def pump_stdout():
        try:
            for line in proc.stdout:
                out_chunks.append(line)
                if on_line:
                    try:
                        on_line(line)
                    except Exception:
                        # 콜백 오류가 생성 자체를 막으면 안 된다.
                        pass
        except Exception:
            pass

    def pump_stderr():
        try:
            for line in proc.stderr:
                err_chunks.append(line)
        except Exception:
            pass

    def feed_stdin():
        try:
            if stdin_text is not None:
                proc.stdin.write(stdin_text)
            proc.stdin.close()
        except Exception:
            pass

    threads = [
        threading.Thread(target=feed_stdin, daemon=True),
        threading.Thread(target=pump_stdout, daemon=True),
        threading.Thread(target=pump_stderr, daemon=True),
    ]
    for thread in threads:
        thread.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            proc.wait(timeout=10)
        except Exception:
            pass

    for thread in threads:
        thread.join(timeout=5)

    duration = time.time() - started
    stdout = "".join(out_chunks)
    stderr = "".join(err_chunks)

    if timed_out:
        return -1, stdout, stderr + f"\nerror: timeout({timeout_s}s)", duration
    return proc.returncode, stdout, stderr, duration
