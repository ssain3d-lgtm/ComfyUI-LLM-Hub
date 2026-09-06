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
        f"'{name}' executable not found. Add it to PATH, or set an absolute path "
        f"in config.json under cli_paths.{name}."
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
    # --- 검토에서 나온 누락분 ---
    # codex --full-auto: --sandbox workspace-write + 자동 승인의 별칭이다.
    #   노드가 붙인 -s read-only 뒤에 오므로 어느 쪽이 이기는지는 codex 의
    #   우선순위가 정한다 -- 우리가 정하는 게 아니다.
    "--full-auto",
    # codex -a / --ask-for-approval: 권한 상승 요청 정책.
    "-a", "--ask-for-approval",
    # gemini --include-directories: claude 의 --add-dir 에 해당한다.
    "--include-directories", "--include-dir",
)


def _normalize_flag(token: str) -> str:
    """플래그 표기를 하나로 모아 비교한다.

    gemini 는 yargs 를 쓰는데 기본 설정(camel-case-expansion)에서 --approvalMode
    와 --approval-mode 를 둘 다 받는다. 소문자로만 낮추면 --approvalmode 가 되어
    목록의 --approval-mode 와 안 맞는다 -- 차단목록을 그냥 지나간다.
    비교 전에 하이픈을 떼고 소문자로 맞춘 뒤, 목록도 같은 방식으로 정규화한다.
    """
    return token.lower().replace("-", "")


def _normalized_markers():
    return tuple(_normalize_flag(m) for m in _UNSAFE_EXACT_MARKERS)


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
        low = _normalize_flag(token)

        if any(low.startswith(_normalize_flag(m)) for m in _UNSAFE_PREFIX_MARKERS):
            rejected.append(token)
            i += 1
            continue

        markers = _normalized_markers()
        # "--flag=value" 는 정규화하면 "--flag=value" 의 하이픈만 빠진다.
        head = low.split("=", 1)[0]
        exact = head in markers and "=" not in token
        with_value = head in markers and "=" in token
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

    예전에는 Windows 에서 posix=False 를 썼다. 역슬래시 경로(C:\\work\\docs)를
    이스케이프로 먹지 않게 하려던 것인데, 그 모드는 **따옴표를 벗기지 않는다**.
    args 는 리스트로 shell=False 에 넘어가므로 자식 CLI 가
        --append-system-prompt "be brief"
    를 따옴표까지 포함한 한 덩어리로 받았다. Windows 가 이 팩의 주 플랫폼이고,
    관련 테스트는 win32 에서 skip 이라 CI 도 못 봤다.

    posix 렉서를 쓰되 escape 문자를 비워 두 성질을 동시에 얻는다:
    따옴표는 벗겨지고, 역슬래시는 글자 그대로 남는다.
    """
    if not extra_args or not extra_args.strip():
        return []
    try:
        lexer = shlex.shlex(extra_args, posix=True)
        lexer.whitespace_split = True
        lexer.escape = ""  # C:\\work\\docs 가 C:workdocs 가 되지 않게
        return list(lexer)
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


def _register(node_id, proc):
    """실행 중 프로세스를 중단 레지스트리에 올린다.

    이걸 해야 Stop 이 프로세스를 직접 죽일 수 있다. 스트리밍 경로는 should_stop
    폴링으로도 멈추지만, 비스트리밍(run_cli)은 폴링 지점이 없어 등록이 유일한
    중단 수단이다. cancel 은 여기서 지연 import 한다 -- cancel 쪽이 _kill_tree 를
    쓰므로 모듈 최상단에서 서로 부르면 순환이 된다.
    """
    if node_id is None:
        return
    try:
        from . import cancel

        cancel.register_process(node_id, proc)
    except Exception:
        pass


def _unregister(node_id):
    if node_id is None:
        return
    try:
        from . import cancel

        cancel.unregister_process(node_id)
    except Exception:
        pass


def run_cli(args: list, *, cwd: str, stdin_text=None, timeout_s: int = 300, node_id=None):
    """CLI 를 실행하고 (exit_code, stdout, stderr, duration_s) 를 돌려준다.

    타임아웃이면 exit_code = -1, stderr 에 "error: timeout(Ns)" 를 채운다.
    좀비 프로세스가 남지 않도록 트리 전체를 kill 후 반드시 회수한다 (T5).

    node_id 를 주면 실행 중 프로세스를 중단 레지스트리에 등록한다. 이 경로는
    스트리밍이 아니라 중간 폴링 지점이 없으므로, Stop 이 프로세스를 직접 죽이는
    것이 유일한 중단 방법이다.
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

    _register(node_id, proc)
    try:
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
    finally:
        _unregister(node_id)


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


def run_cli_stream(args: list, *, cwd: str, stdin_text=None, timeout_s: int = 300,
                   on_line=None, should_stop=None, node_id=None):
    """run_cli 와 같지만 stdout 을 한 줄씩 읽으며 on_line(line) 을 호출한다.

    스트리밍 출력(JSONL/SSE)을 실시간으로 노드에 흘려보내기 위한 변형이다.
    반환값은 run_cli 와 동일한 (exit_code, stdout, stderr, duration_s).

    should_stop 을 주면 그것이 참이 되는 즉시 프로세스 트리를 죽이고 돌아온다.
    proc.wait(timeout=timeout_s) 로 한 번에 기다리면 Stop 을 눌러도 타임아웃까지
    (기본 300초) 붙잡혀 있으므로, 짧게 끊어 기다리며 중간에 확인한다.

    node_id 를 주면 프로세스를 중단 레지스트리에도 올린다. 폴링은 최대 0.2초를
    기다리지만 등록해 두면 Stop 이 곧바로 죽인다.
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

    _register(node_id, proc)

    threads = [
        threading.Thread(target=feed_stdin, daemon=True),
        threading.Thread(target=pump_stdout, daemon=True),
        threading.Thread(target=pump_stderr, daemon=True),
    ]
    for thread in threads:
        thread.start()

    timed_out = False
    stopped = False
    deadline = started + timeout_s
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            timed_out = True
            break
        if should_stop is not None:
            try:
                if should_stop():
                    stopped = True
                    break
            except Exception:
                # 판정이 터져도 생성을 막지는 않는다.
                pass
        try:
            # 0.2초씩 끊어 기다린다. Stop 반응 속도와 폴링 비용의 타협점이다.
            proc.wait(timeout=min(0.2, remaining))
            break
        except subprocess.TimeoutExpired:
            continue

    if timed_out or stopped:
        _kill_tree(proc)
        try:
            proc.wait(timeout=10)
        except Exception:
            pass

    for thread in threads:
        thread.join(timeout=5)

    # 파이프를 안 닫으면 fd 가 Popen 이 수거될 때까지 남는다. 더 나쁜 경우는
    # 죽인 프로세스의 손자가 쓰기 끝을 쥐고 있을 때다 -- pump 스레드가
    # `for line in proc.stdout` 에서 영원히 막히고, 위의 join(timeout=5) 는
    # 조용히 포기한다. 그러면 그 데몬 스레드와 Popen(과 fd)이 ComfyUI 가 살아
    # 있는 내내 남는다. Stop 을 누를 때마다 하나씩.
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if pipe is not None:
                pipe.close()
        except Exception:
            pass

    _unregister(node_id)

    duration = time.time() - started
    stdout = "".join(out_chunks)
    stderr = "".join(err_chunks)

    if timed_out:
        return -1, stdout, stderr + f"\nerror: timeout({timeout_s}s)", duration
    # wait(timeout=10) 이 만료되면 returncode 가 None 으로 남는다. 그대로 두면
    # 백엔드가 "codex exit code None" 같은 문구를 내놓는다.
    code = proc.returncode
    return (-1 if code is None else code), stdout, stderr, duration
