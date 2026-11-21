# Test fakes
# A fake website and a fake Claude, so no test touches the network or costs money.

import base64
from collections.abc import Callable, Coroutine, Mapping
from typing import Any, Literal

import httpx
import httpx2
from anthropic import AsyncAnthropic, OpenTelemetryOptions

Route = tuple[int, bytes] | tuple[int, bytes, dict[str, str]]

FAKE_CLAUDE_URL = "https://claude.test"

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)

ClaudeHandler = (
    Callable[[httpx2.Request], httpx2.Response]
    | Callable[[httpx2.Request], Coroutine[None, None, httpx2.Response]]
)


class RealClaudeCallBlockedError(Exception):
    pass


def fake_client(routes: Mapping[str, Route]) -> httpx.AsyncClient:

    def handler(request: httpx.Request) -> httpx.Response:
        status, body, *rest = routes.get(str(request.url), (404, b"not found"))
        headers = {"content-type": "text/html; charset=utf-8", **(rest[0] if rest else {})}
        return httpx.Response(status, content=body, headers=headers)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def claude_reply(
    text: str = "ok",
    input_tokens: int = 100,
    output_tokens: int = 20,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> httpx2.Response:
    body: dict[str, Any] = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read_tokens,
            "cache_creation_input_tokens": cache_write_tokens,
        },
    }
    return httpx2.Response(200, json=body, headers={"request-id": "req_test"})


def claude_tool_reply(
    *calls: tuple[str, dict[str, Any]], input_tokens: int = 100, output_tokens: int = 20
) -> httpx2.Response:
    content = [
        {"type": "tool_use", "id": f"toolu_{name}_{position}", "name": name, "input": tool_input}
        for position, (name, tool_input) in enumerate(calls, start=1)
    ]
    body: dict[str, Any] = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": content,
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    }
    return httpx2.Response(200, json=body, headers={"request-id": "req_test"})


def claude_error(status: int, message: str) -> httpx2.Response:
    return httpx2.Response(
        status, json={"type": "error", "error": {"type": "api_error", "message": message}}
    )


def fake_claude(
    handler: ClaudeHandler, open_telemetry: OpenTelemetryOptions | Literal[False] = False
) -> AsyncAnthropic:
    return AsyncAnthropic(
        api_key="test-key",
        base_url=FAKE_CLAUDE_URL,
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        open_telemetry=open_telemetry,
    )
