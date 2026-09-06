# -*- coding: utf-8 -*-
"""모델 조회 차단을 이 블록에서만 푼다.

tests/__init__.py 가 스위트 전체에서 LLMHUB_SKIP_MODEL_PROBE 를 세운다. 그래야
개발 PC 에 떠 있는 진짜 서버를 두드리지 않는다. 다만 조회 로직 자체를 검증하는
테스트는 그 차단을 잠깐 풀어야 한다 -- 이 테스트들은 가짜 서버나 mock 을 상대로
하므로 여전히 밖으로 나가지 않는다.
"""

import contextlib
import os

FLAG = "LLMHUB_SKIP_MODEL_PROBE"


@contextlib.contextmanager
def allow_probe():
    saved = os.environ.pop(FLAG, None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ[FLAG] = saved
