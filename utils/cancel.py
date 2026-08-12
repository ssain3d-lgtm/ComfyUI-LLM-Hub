# -*- coding: utf-8 -*-
"""생성 중단 레지스트리 (노드의 Stop 버튼).

프론트엔드의 Stop 은 HTTP 로 들어오는데 생성은 워커 스레드에서 돌고 있다. 둘을
잇는 것이 여기다: 라우트가 request_stop() 으로 깃발을 세우면, 실행 중인 루프가
is_stopped() 를 보고 빠져나온다.

멈추는 방식이 백엔드마다 다르다.

  lmstudio : SSE 루프가 깃발을 보고 스스로 빠져나온다
  CLI 3종  : 프로세스를 죽여야 한다. .cmd 셔틀 때문에 트리째 죽여야 하므로
             (proc.py 의 _kill_tree 참고) 실행 중인 프로세스를 여기 등록해 둔다

ComfyUI 자체 Cancel 도 같은 판정에 넣는다. 버튼이 둘로 갈려서 하나는 듣고 하나는
안 듣는 상태가 제일 나쁘다.
"""

from __future__ import annotations

import threading

_LOCK = threading.Lock()
_STOPPED = set()
_PROCS = {}


def _key(node_id) -> str:
    return "" if node_id is None else str(node_id)


def _comfy_interrupted() -> bool:
    """ComfyUI 의 Cancel 이 눌렸는가. ComfyUI 밖에서는 항상 False."""
    try:
        import comfy.model_management as mm

        return bool(mm.processing_interrupted())
    except Exception:
        return False


def _kill(proc) -> None:
    from .proc import _kill_tree

    try:
        _kill_tree(proc)
    except Exception:
        pass


def begin(node_id) -> None:
    """이 노드의 새 실행을 시작한다. 지난 실행의 중지 표시를 지운다.

    이걸 안 지우면 한 번 멈춘 노드가 영원히 멈춘 채로 남는다.
    """
    key = _key(node_id)
    with _LOCK:
        _STOPPED.discard(key)
        _PROCS.pop(key, None)


def request_stop(node_id) -> bool:
    """중지를 요청한다. 등록된 프로세스가 있으면 함께 죽인다."""
    key = _key(node_id)
    with _LOCK:
        _STOPPED.add(key)
        proc = _PROCS.get(key)
    if proc is not None:
        _kill(proc)
    return True


def is_stopped(node_id) -> bool:
    key = _key(node_id)
    with _LOCK:
        if key in _STOPPED:
            return True
    return _comfy_interrupted()


def register_process(node_id, proc) -> None:
    """실행 중인 자식 프로세스를 등록한다. Stop 이 이걸 죽인다."""
    key = _key(node_id)
    with _LOCK:
        _PROCS[key] = proc


def unregister_process(node_id) -> None:
    key = _key(node_id)
    with _LOCK:
        _PROCS.pop(key, None)


def stopper(node_id):
    """루프에 넘길 판정 함수. node_id 가 없으면 항상 False 를 주는 함수."""
    if node_id is None:
        return lambda: _comfy_interrupted()
    return lambda: is_stopped(node_id)
