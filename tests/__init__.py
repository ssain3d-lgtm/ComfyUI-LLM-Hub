# -*- coding: utf-8 -*-
"""테스트 스위트를 개발 PC 로부터 떼어놓는다.

이 파일이 있으면 unittest discover 가 tests/ 를 패키지로 보고 **테스트 모듈보다
먼저** 여기를 import 한다. 그 순간에 환경변수를 세워두면 이후 어떤 모듈이
INPUT_TYPES() 를 부르든 실제 서버 조회가 일어나지 않는다.

왜 필요한가: nodes.INPUT_TYPES() 는 list_model_ids() 와 list_server_models() 를
부르고, 그 둘은 127.0.0.1 의 1234 / 11434 / 8000 / 8080 을 두드린다. 스위트에서
INPUT_TYPES() 를 부르는 테스트가 수십 개라, 개발 PC 에 LM Studio 나 Ollama 가
떠 있으면 -- 실제로 그렇다 -- 테스트가 그 서버로 요청을 보내고 위젯 기본값이
머신마다 달라진다. 읽기 전용이라 파괴는 없지만, 예전에 테스트가 사용자 서버에
실제로 `lms unload` 를 쐈던 사고와 같은 부류다.

CI 워크플로에도 같은 변수를 넣어 둔다 -- pytest 로 돌리는 등 다른 진입점으로도
새지 않게 하기 위한 이중 방어다.
"""

import os

os.environ.setdefault("LLMHUB_SKIP_MODEL_PROBE", "1")
