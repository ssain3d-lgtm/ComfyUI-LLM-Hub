# -*- coding: utf-8 -*-
"""LM Studio API 토큰 해석 (실측 회귀).

실제 설치에서 나온 증상: lmstudio_model 드롭다운에 "(auto)" 하나만 뜬다.
원인은 LM Studio 의 "Enable API key" 가 켜져 있어 /v1/models 가 401 을 주는데,
토큰을 config.json 에서만 읽고 있었다는 것. 401 은 조용히 삼켜져 빈 목록이 되고,
그 빈 목록이 그대로 콤보가 된다.

토큰 값은 어떤 경로로도 로그/디버그에 실려서는 안 된다 (DESIGN §10).
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import unittest
from unittest import mock

_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_PACK_ROOT))
_PACK_NAME = os.path.basename(_PACK_ROOT)

config_mod = importlib.import_module(f"{_PACK_NAME}.utils.config")
lmstudio_mod = importlib.import_module(f"{_PACK_NAME}.backends.lmstudio")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mock_lmstudio import MockLMStudio  # noqa: E402

TOKEN = "secret-token-do-not-log-12345"


def _reset_model_cache():
    lmstudio_mod._MODEL_CACHE["at"] = 0.0
    lmstudio_mod._MODEL_CACHE["ids"] = []
    # 경고는 한 번만 나가므로, 경고를 검사하는 테스트끼리 서로를 가리지 않게 비운다.
    lmstudio_mod._WARNED.clear()


class TestTokenResolution(unittest.TestCase):
    def setUp(self):
        _reset_model_cache()
        self.addCleanup(_reset_model_cache)
        # 이 테스트가 실제 사용자 환경변수를 물려받지 않게 지운다.
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("LM_STUDIO_API_KEY", None)

    def test_token_from_env_var_reaches_the_model_list(self):
        """config.json 이 비어 있어도 LM_STUDIO_API_KEY 로 목록을 받아야 한다."""
        with MockLMStudio(require_token=TOKEN, models=["a-model", "b-model"]) as server:
            os.environ["LM_STUDIO_API_KEY"] = TOKEN
            cfg = {"lmstudio": {"base_url": server.base_url, "api_token": ""}}
            with mock.patch.object(lmstudio_mod, "load_config", return_value=cfg):
                ids = lmstudio_mod.list_model_ids(timeout_s=3)
        self.assertEqual(ids, ["a-model", "b-model"])

    def test_token_from_token_file_reaches_the_model_list(self):
        """lm_studio_token.txt 로도 읽혀야 한다(이 설치의 기존 관례)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "lm_studio_token.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(TOKEN + "\n")  # 줄바꿈은 잘려야 한다
            with MockLMStudio(require_token=TOKEN, models=["only-model"]) as server:
                cfg = {"lmstudio": {"base_url": server.base_url, "api_token": ""}}
                with mock.patch.object(config_mod, "TOKEN_PATH", path), \
                        mock.patch.object(lmstudio_mod, "load_config", return_value=cfg):
                    ids = lmstudio_mod.list_model_ids(timeout_s=3)
        self.assertEqual(ids, ["only-model"])

    def test_config_token_still_wins(self):
        """기존 사용자의 config.json 설정이 계속 우선해야 한다."""
        with MockLMStudio(require_token=TOKEN, models=["cfg-model"]) as server:
            os.environ["LM_STUDIO_API_KEY"] = "wrong-token"
            cfg = {"lmstudio": {"base_url": server.base_url, "api_token": TOKEN}}
            with mock.patch.object(lmstudio_mod, "load_config", return_value=cfg):
                ids = lmstudio_mod.list_model_ids(timeout_s=3)
        self.assertEqual(ids, ["cfg-model"])

    def test_auth_failure_is_logged_so_it_is_diagnosable(self):
        """401 을 조용히 삼키면 안 된다. 이 증상을 찾는 데 라이브 probe 가 필요했다."""
        with MockLMStudio(require_token=TOKEN) as server:
            cfg = {"lmstudio": {"base_url": server.base_url, "api_token": ""}}
            with mock.patch.object(lmstudio_mod, "load_config", return_value=cfg):
                with self.assertLogs(level="WARNING") as caught:
                    ids = lmstudio_mod.list_model_ids(timeout_s=3)
        self.assertEqual(ids, [])
        self.assertTrue(
            any("401" in line for line in caught.output),
            f"401 이 로그에 없다: {caught.output}",
        )

    def test_token_value_never_appears_in_the_log(self):
        with MockLMStudio(require_token=TOKEN) as server:
            os.environ["LM_STUDIO_API_KEY"] = "a-token-that-is-wrong"
            cfg = {"lmstudio": {"base_url": server.base_url, "api_token": ""}}
            with mock.patch.object(lmstudio_mod, "load_config", return_value=cfg):
                with self.assertLogs(level="WARNING") as caught:
                    lmstudio_mod.list_model_ids(timeout_s=3)
        joined = "\n".join(caught.output)
        self.assertNotIn("a-token-that-is-wrong", joined)

    def test_embedding_models_stay_out_of_the_dropdown(self):
        """v0 이 걸러낸 임베딩 모델을 v1 병합이 도로 넣어서는 안 된다.

        임베딩 모델은 채팅을 못 하므로 드롭다운에 뜨면 고를 수 있는 함정이 된다.
        _ids_from_v0 는 이미 제외하는데, 두 엔드포인트 결과를 합치면서 되살아났다.
        """
        with MockLMStudio(models=["chat-model"]) as server:
            server.v0_models = [
                {"id": "chat-model", "type": "vlm"},
                {"id": "nomic-embed", "type": "embeddings"},
            ]
            server.models = ["chat-model", "nomic-embed"]  # /v1 은 종류를 안 알려준다
            cfg = {"lmstudio": {"base_url": server.base_url, "api_token": ""}}
            with mock.patch.object(lmstudio_mod, "load_config", return_value=cfg):
                ids = lmstudio_mod.list_model_ids(timeout_s=3)
        self.assertIn("chat-model", ids)
        self.assertNotIn("nomic-embed", ids)

    def test_backend_generate_also_uses_the_resolved_token(self):
        """드롭다운만 고치면 생성이 401 로 죽는다. 같은 토큰을 써야 한다."""
        LLMRequest = importlib.import_module(f"{_PACK_NAME}.backends.base").LLMRequest
        with MockLMStudio(script=[{"content": "안녕"}], require_token=TOKEN) as server:
            os.environ["LM_STUDIO_API_KEY"] = TOKEN
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url, "api_token": "",
                                     "unload_after": False}}
            )
            response = backend.generate(
                LLMRequest("lmstudio", "mock-model-a", "", "안녕")
            )
        self.assertEqual(response.text, "안녕")


if __name__ == "__main__":
    unittest.main()
