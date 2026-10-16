"""OpenAIClient transport policy against a local chat-completions server (no real API)."""

import json
import socket
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from causaldeploy.agent.llm import LlmUnavailableError, OpenAIClient, backoff_delay

OK = {
    "id": "x",
    "object": "chat.completion",
    "created": 0,
    "model": "m",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
}

Responder = Callable[[int, dict[str, Any]], JSONResponse]


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[Responder], list[Any]]]:
    """Start a chat-completions server whose n-th response is chosen by a responder."""
    servers: list[tuple[uvicorn.Server, threading.Thread]] = []

    def start(responder: Responder) -> list[dict[str, Any]]:
        app = FastAPI()
        seen: list[dict[str, Any]] = []

        @app.post("/v1/chat/completions")
        async def completions(req: Request) -> JSONResponse:
            body = await req.json()
            seen.append(body)
            return responder(len(seen), body)

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="error"))
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        while not server.started:
            time.sleep(0.05)
        servers.append((server, t))
        monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{port}/v1")
        return seen

    yield start
    for server, t in servers:
        server.should_exit = True
        t.join(5)


def _client(**kw: Any) -> tuple[OpenAIClient, list[float]]:
    slept: list[float] = []

    async def sleep(d: float) -> None:
        slept.append(d)

    return OpenAIClient(api_key="test-key", model="m", sleep=sleep, **kw), slept


async def test_429_then_200_is_retried_honouring_retry_after(fake_openai: Any) -> None:
    def responder(n: int, body: dict[str, Any]) -> JSONResponse:
        if n == 1:
            return JSONResponse(
                {"error": {"message": "rate limited", "type": "rate_limit"}},
                status_code=429,
                headers={"retry-after": "1.5"},
            )
        return JSONResponse(OK)

    seen = fake_openai(responder)
    llm, slept = _client()
    reply = await llm.chat([{"role": "user", "content": "hi"}], [])
    assert len(seen) == 2  # one retry by our loop, none by the SDK
    assert slept == [1.5]  # Retry-After honoured
    assert reply.attempts == 2 and reply.usage == {"input_tokens": 10, "output_tokens": 2}


async def test_5xx_retries_with_jitter_then_gives_up(fake_openai: Any) -> None:
    seen = fake_openai(lambda n, b: JSONResponse({"error": {"message": "boom"}}, status_code=503))
    llm, slept = _client(max_retries=2)
    with pytest.raises(LlmUnavailableError, match="after 3 attempt"):
        await llm.chat([{"role": "user", "content": "hi"}], [])
    assert len(seen) == 3 and len(slept) == 2
    assert all(0 <= d <= 20 for d in slept)


async def test_timeout_is_explicit_and_sdk_retries_are_disabled() -> None:
    llm, _ = _client(timeout=7.5)
    assert llm._client.timeout == 7.5 and llm._client.max_retries == 0


async def test_structured_output_falls_back_when_unsupported(fake_openai: Any) -> None:
    def responder(n: int, body: dict[str, Any]) -> JSONResponse:
        if "response_format" in body:
            return JSONResponse(
                {"error": {"message": "Invalid parameter: response_format json_schema"}},
                status_code=400,
            )
        return JSONResponse(OK)

    seen = fake_openai(responder)
    llm, _ = _client()
    reply = await llm.chat([{"role": "user", "content": "hi"}], [])
    assert json.loads(reply.content or "") == {}
    assert "response_format" in seen[0] and "response_format" not in seen[1]
    assert llm.structured_output is False


def test_backoff_is_full_jitter_and_capped() -> None:
    assert backoff_delay(3, None, 0.5, 20, rand=lambda lo, hi: hi) == 4.0
    assert backoff_delay(10, None, 0.5, 20, rand=lambda lo, hi: hi) == 20
    assert backoff_delay(0, None, 0.5, 20, rand=lambda lo, hi: lo) == 0.0
    assert backoff_delay(0, 90.0, 0.5, 20) == 20  # Retry-After is capped too
