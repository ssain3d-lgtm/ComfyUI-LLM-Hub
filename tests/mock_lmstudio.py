# -*- coding: utf-8 -*-
"""LM Studio 를 흉내내는 최소 OpenAI 호환 서버 (표준 라이브러리만 사용).

실제 LM Studio 없이 LMStudioBackend 의 요청/툴 루프 처리를 검증하기 위한
테스트 전용 서버다. 노드 런타임에는 관여하지 않는다.

사용:
    from tests.mock_lmstudio import MockLMStudio
    with MockLMStudio(script=[...]) as server:
        ...  # server.base_url 로 요청
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class MockLMStudio:
    """script 에 담긴 응답을 순서대로 돌려주는 가짜 LM Studio.

    script 항목은 아래 둘 중 하나다.
      {"content": "..."}                      -> 일반 텍스트 응답
      {"tool_calls": [{"name": ..., "path": ...}]}  -> 툴 호출 요청
    """

    def __init__(self, script=None, models=None, fail_without_model=False, sse=False,
                 require_token=""):
        self.sse = sse
        self.script = list(script or [{"content": "OK"}])
        self.models = models or ["mock-model-a", "mock-model-b"]
        self.fail_without_model = fail_without_model
        # 빈 문자열이면 인증을 요구하지 않는다(기존 테스트 동작 그대로).
        # LM Studio 는 "Enable API key" 를 켜면 /v1/models 까지 401 로 막는다.
        self.require_token = require_token
        # /api/v0/models 용. None 이면 이 엔드포인트를 404 로 둔다(= /v1 만 있는 서버).
        self.v0_models = None
        self.requests = []  # 서버가 받은 payload 기록 (검증용)
        self._index = 0
        self._server = None
        self._thread = None

    # -- 서버 수명주기 -------------------------------------------------------

    def __enter__(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # 테스트 출력 오염 방지
                pass

            def _send_json(self, code, payload):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self):
                if not outer.require_token:
                    return True
                if self.headers.get("Authorization") == "Bearer " + outer.require_token:
                    return True
                self._send_json(401, {"error": "unauthorized"})
                return False

            def do_GET(self):
                if not self._authorized():
                    return
                if self.path.rstrip("/") == "/api/v0/models":
                    if outer.v0_models is None:
                        self._send_json(404, {"error": "not found"})
                    else:
                        self._send_json(200, {"data": outer.v0_models})
                    return
                if self.path.rstrip("/") == "/v1/models":
                    self._send_json(
                        200, {"data": [{"id": mid} for mid in outer.models]}
                    )
                else:
                    self._send_json(404, {"error": "not found"})

            def do_POST(self):
                if self.path.rstrip("/") != "/v1/chat/completions":
                    self._send_json(404, {"error": "not found"})
                    return

                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append(payload)

                if outer.fail_without_model and not payload.get("model"):
                    self._send_json(
                        400, {"error": {"message": "model field is required"}}
                    )
                    return

                step = outer.script[min(outer._index, len(outer.script) - 1)]
                outer._index += 1

                # 스트리밍 요청이면 SSE 로 토큰을 쪼개 보낸다.
                if outer.sse and payload.get("stream"):
                    content = step.get("content", "")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for char in content:
                        chunk = {
                            "model": outer.models[0],
                            "choices": [{"index": 0, "delta": {"content": char}}],
                        }
                        self.wfile.write(
                            ("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode("utf-8")
                        )
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return

                if "tool_calls" in step:
                    calls = [
                        {
                            "id": f"call_{i}",
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps({"path": call.get("path", "")}),
                            },
                        }
                        for i, call in enumerate(step["tool_calls"])
                    ]
                    message = {"role": "assistant", "content": None, "tool_calls": calls}
                else:
                    message = {"role": "assistant", "content": step.get("content", "")}

                self._send_json(
                    200,
                    {
                        "id": "chatcmpl-mock",
                        "object": "chat.completion",
                        "model": payload.get("model") or outer.models[0],
                        "choices": [
                            {"index": 0, "message": message, "finish_reason": "stop"}
                        ],
                    },
                )

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)
        return False

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def tool_results(self) -> list:
        """서버가 받은 role="tool" 메시지 내용들 (툴 루프 검증용)."""
        out = []
        for payload in self.requests:
            for message in payload.get("messages", []):
                if message.get("role") == "tool":
                    out.append(message.get("content", ""))
        return out
