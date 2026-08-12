# -*- coding: utf-8 -*-
"""스트리밍 요청이 200 이 아닐 때의 폴백 (실측 회귀).

실측 경로 (2026-08-12):
  unload_after 기본값이 True → 생성이 끝나면 모델이 VRAM 에서 내려간다
  → 다음 실행의 스트리밍 요청이 400 "No models loaded" 를 받는다
  → _stream_chat 이 2-튜플을 돌려주는데 호출부는 3개를 푼다
  → ValueError: not enough values to unpack (expected 3, got 2)

코드 주석은 "200 이 아니면 아래 비스트리밍 경로가 오류/모델 폴백을 처리한다" 고
적어놨지만, 튜플 길이가 어긋나 그 폴백에 도달하지도 못한다. 스트리밍은 기본값
(stream_view=plain)이라 이 경로는 일상적으로 밟힌다.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

_PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_PACK_ROOT))
_PACK_NAME = os.path.basename(_PACK_ROOT)

base = importlib.import_module(f"{_PACK_NAME}.backends.base")
lmstudio_mod = importlib.import_module(f"{_PACK_NAME}.backends.lmstudio")


class NoModelLoadedServer:
    """모델이 지정되지 않으면 400 을 주고, 지정되면 정상 응답하는 서버.

    LM Studio 가 모델을 내린 뒤 보이는 실제 동작을 그대로 흉내낸다.
    """

    BODY_400 = {"error": {"message": "No models loaded. Please load a model in the "
                                     "developer page or use the 'lms load' command.",
                          "type": "invalid_request_error", "param": "model"}}

    def __enter__(self):
        outer = self
        self.saw_model = []
        self.streamed = []   # 각 요청이 stream=True 였는지

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _json(self, code, payload):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._json(200, {"data": [{"id": "fallback-model"}]})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                outer.saw_model.append(payload.get("model"))
                outer.streamed.append(bool(payload.get("stream")))
                if not payload.get("model"):
                    self._json(400, outer.BODY_400)
                    return
                if payload.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for piece in ("안", "녕"):
                        chunk = {"model": payload["model"],
                                 "choices": [{"delta": {"content": piece}}]}
                        self.wfile.write(
                            ("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n")
                            .encode("utf-8"))
                    self.wfile.write(b"data: [DONE]\n\n")
                    return
                self._json(200, {
                    "model": payload["model"],
                    "choices": [{"finish_reason": "stop",
                                 "message": {"role": "assistant", "content": "안녕"}}],
                })

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = "http://127.0.0.1:%d" % self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_a):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class Emitter:
    enabled = True

    def __init__(self):
        self.text = ""

    def append(self, delta):
        self.text += delta or ""

    def set_status(self, status):
        pass

    def reset_text(self, text=""):
        self.text = text

    def finish(self, status="", text=None):
        pass


class TestStreamErrorFallback(unittest.TestCase):
    def _run(self):
        with NoModelLoadedServer() as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url, "unload_after": False}}
            )
            req = base.LLMRequest("lmstudio", "", "", "안녕")   # 모델 미지정
            req.emitter = Emitter()
            req.max_tokens = 2048
            return backend.generate(req), server.saw_model

    def test_streaming_error_does_not_crash(self):
        response, _ = self._run()
        self.assertNotIn("ValueError", response.status,
                         f"스트리밍 오류 경로가 예외로 죽는다: {response.status}")

    def test_streaming_error_falls_back_to_a_model(self):
        """주석이 약속한 대로 /v1/models 의 첫 모델로 재시도해야 한다."""
        response, saw_model = self._run()
        self.assertEqual(response.status, "ok", response.status)
        self.assertEqual(response.text, "안녕")
        self.assertIn("fallback-model", saw_model,
                      f"모델 폴백이 일어나지 않았다: {saw_model}")

    def test_retry_after_the_fallback_still_streams(self):
        """폴백했다고 실시간 출력까지 포기하면 안 된다.

        unload_after 기본값이 True 라 모델은 매 실행 뒤 내려간다. 그래서 이
        400 → 폴백 경로가 일상적으로 밟히는데, 여기서 스트리밍을 영구히 꺼버리면
        모니터 창은 사실상 늘 비어 있게 된다.
        """
        with NoModelLoadedServer() as server:
            backend = lmstudio_mod.LMStudioBackend(
                config={"lmstudio": {"base_url": server.base_url, "unload_after": False}}
            )
            req = base.LLMRequest("lmstudio", "", "", "안녕")
            req.emitter = Emitter()
            req.max_tokens = 2048
            response = backend.generate(req)
            saw_model, streamed = list(server.saw_model), list(server.streamed)
        # 첫 요청도 stream=True 라 "하나라도 True" 로는 아무것도 못 가른다.
        # 갈라야 하는 건 폴백 뒤의 재시도, 즉 마지막 요청이다.
        self.assertEqual(len(streamed), 2, f"요청이 2번이 아니다: {saw_model}")
        self.assertEqual(saw_model, [None, "fallback-model"])
        self.assertTrue(
            streamed[1],
            "폴백 재시도가 stream=False 로 나갔다 — 모니터 창이 빈 채로 끝난다")
        self.assertEqual(response.text, "안녕")


if __name__ == "__main__":
    unittest.main()
