# Tests: Ollama client
# Request, answer parsing, call log and errors; no real Ollama.

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.checks.ai_findings import AiAnswerError, findings_format
from app.checks.page_check import check_page
from app.claude import cached_system
from app.ollama import OllamaClient, OllamaError, inline_refs, ollama_request
from app.pages import Heading, PageData
from app.tables import ClaudeCall

pytestmark = pytest.mark.anyio

OLLAMA_URL = "http://ollama.test"

PAGE = PageData(
    url="https://shop.test/news.html",
    final_url="https://shop.test/news.html",
    status_code=200,
    title="Our new autumn range",
    meta_description="Three new wooden shapes join the collection this autumn: a crescent, a hexagon and an arch.",
    lang="en-GB",
    headings=[Heading(level=1, text="New autumn range")],
    text="BUY NOW!!! Stock WON'T last.",
)

OFF_BRAND: dict[str, str] = {
    "rule": "off-brand-tone",
    "severity": "medium",
    "element": "first paragraph",
    "evidence": '"BUY NOW!!!" shouts',
    "suggested_fix": "Our new shapes are in the shop now.",
}


def answer(content: str, done_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "gemma3:4b",
            "created_at": "2026-11-18T12:00:00Z",
            "message": {"role": "assistant", "content": content},
            "done": True,
            "done_reason": done_reason,
            "prompt_eval_count": 812,
            "eval_count": 64,
        },
    )


def make_client(
    handler: Callable[[httpx.Request], httpx.Response], sessions: async_sessionmaker[AsyncSession]
) -> OllamaClient:
    return OllamaClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)), sessions, OLLAMA_URL, 8192
    )


async def logged_calls(sessions: async_sessionmaker[AsyncSession]) -> list[ClaudeCall]:
    async with sessions() as session:
        return list(await session.scalars(select(ClaudeCall).order_by(ClaudeCall.id)))


def has_ref(value: Any) -> bool:
    if isinstance(value, dict):
        return "$ref" in value or "$defs" in value or any(has_ref(item) for item in value.values())
    if isinstance(value, list):
        return any(has_ref(item) for item in value)
    return False


def test_the_findings_schema_is_sent_without_references() -> None:
    schema = findings_format(["off-brand-tone", "unclear-heading"])["schema"]
    assert has_ref(schema)

    flat = inline_refs(schema)

    assert not has_ref(flat)
    assert flat["properties"]["findings"]["items"]["properties"]["rule"]["enum"] == [
        "off-brand-tone",
        "unclear-heading",
    ]


def test_the_request_has_system_text_images_limits_and_schema() -> None:
    request = ollama_request(
        model="gemma3:4b",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": "aGVsbG8="},
                    },
                    {"type": "text", "text": "Does this alt text describe the image?"},
                ],
            }
        ],
        max_tokens=2000,
        system=cached_system("You check alt text."),
        output_format=findings_format(["alt-mismatch"]),
        context_tokens=8192,
    )

    assert request["model"] == "gemma3:4b"
    assert request["stream"] is False
    assert request["messages"] == [
        {"role": "system", "content": "You check alt text."},
        {
            "role": "user",
            "content": "Does this alt text describe the image?",
            "images": ["aGVsbG8="],
        },
    ]
    assert request["options"] == {"num_ctx": 8192, "num_predict": 2000, "temperature": 0}
    assert not has_ref(request["format"])


async def test_a_page_check_on_ollama_gives_findings_and_logs_a_free_call(
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return answer(json.dumps({"findings": [OFF_BRAND]}))

    client = make_client(handler, test_sessions)
    try:
        findings = await check_page(client, PAGE, None, model="gemma3:4b")
    finally:
        await client.aclose()

    assert [(finding.page_url, finding.rule) for finding in findings] == [
        (PAGE.url, "off-brand-tone")
    ]
    assert sent[0]["model"] == "gemma3:4b"
    [call] = await logged_calls(test_sessions)
    assert (call.purpose, call.model, call.status, call.cost_microdollars) == (
        "page-check",
        "gemma3:4b",
        "answered",
        0,
    )
    assert (call.input_tokens, call.output_tokens) == (812, 64)
    assert call.duration_ms is not None


async def test_an_answer_cut_off_by_the_token_limit_is_an_answer_error(
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    client = make_client(
        lambda request: answer('{"findings": [', done_reason="length"), test_sessions
    )
    try:
        with pytest.raises(AiAnswerError, match="cut off"):
            await check_page(client, PAGE, None, model="gemma3:4b")
    finally:
        await client.aclose()


async def test_a_model_that_isnt_pulled_is_a_clear_error(
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    missing = 'model "gemma3:4b" not found, try pulling it first'
    client = make_client(
        lambda request: httpx.Response(404, json={"error": missing}), test_sessions
    )
    try:
        with pytest.raises(OllamaError, match="try pulling it first"):
            await check_page(client, PAGE, None, model="gemma3:4b")
    finally:
        await client.aclose()

    [call] = await logged_calls(test_sessions)
    assert (call.status, call.cost_microdollars) == ("failed", 0)
    assert call.error == f"HTTP 404: {missing}"


async def test_a_model_stuck_repeating_itself_fails_only_that_page(
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    stuck = "prediction aborted, token repeat limit reached"
    client = make_client(lambda request: httpx.Response(500, json={"error": stuck}), test_sessions)
    try:
        with pytest.raises(AiAnswerError, match="got stuck on this page"):
            await check_page(client, PAGE, None, model="gemma3:4b")
    finally:
        await client.aclose()

    [call] = await logged_calls(test_sessions)
    assert (call.status, call.cost_microdollars) == ("failed", 0)


async def test_ollama_not_running_is_a_clear_error(
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(down, test_sessions)
    try:
        with pytest.raises(OllamaError, match="is it running"):
            await check_page(client, PAGE, None, model="gemma3:4b")
    finally:
        await client.aclose()
