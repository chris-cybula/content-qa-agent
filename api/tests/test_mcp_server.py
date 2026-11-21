# Tests: MCP server
# An in-process MCP client calls its tools on a crawled fake shop.

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from mcp import Client
from mcp_types import CallToolResult
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.mcp_server import create_server
from app.tables import ClaudeCall, FixProposal
from tests.test_agent import HOME_URL, crawl, fake_shop, finding_of, page_id_of

pytestmark = pytest.mark.anyio
__all__ = ["fake_shop"]

TOOL_NAMES = ["list_runs", "list_pages", "get_page", "get_findings", "propose_fix"]


@pytest.fixture
async def mcp(test_sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[Client]:
    async with Client(create_server(test_sessions)) as mcp_client:
        yield mcp_client


def text_of(result: CallToolResult) -> str:
    return "".join(block.text for block in result.content if block.type == "text")


async def call(mcp: Client, name: str, **arguments: Any) -> str:
    result = await mcp.call_tool(name, arguments)
    assert not result.is_error, text_of(result)
    return text_of(result)


async def call_error(mcp: Client, name: str, **arguments: Any) -> str:
    result = await mcp.call_tool(name, arguments)
    assert result.is_error
    return text_of(result)


async def count(sessions: async_sessionmaker[AsyncSession], table: type) -> int:
    async with sessions() as session:
        return await session.scalar(select(func.count()).select_from(table)) or 0


async def test_only_propose_fix_writes_and_nothing_approves(mcp: Client) -> None:
    tools = (await mcp.list_tools()).tools
    assert [tool.name for tool in tools] == TOOL_NAMES
    hints = {tool.name: tool.annotations for tool in tools if tool.annotations is not None}
    assert set(hints) == set(TOOL_NAMES)
    assert all(hints[name].read_only_hint for name in TOOL_NAMES if name != "propose_fix")
    assert hints["propose_fix"].read_only_hint is False
    assert hints["propose_fix"].destructive_hint is False


async def test_reads_a_run_like_the_api(
    client: httpx.AsyncClient, fake_shop: None, mcp: Client
) -> None:
    run_id = await crawl(client)
    runs = json.loads(await call(mcp, "list_runs"))
    assert [run["id"] for run in runs] == [run_id]
    pages = json.loads(await call(mcp, "list_pages", run_id=run_id))
    assert [page["url"] for page in pages] == [
        page["url"] for page in (await client.get(f"/runs/{run_id}/pages")).json()
    ]
    findings = json.loads(await call(mcp, "get_findings", run_id=run_id))
    assert [finding["id"] for finding in findings] == [
        finding["id"] for finding in (await client.get(f"/runs/{run_id}/findings")).json()
    ]


async def test_get_findings_narrows_by_page_and_status(
    client: httpx.AsyncClient, fake_shop: None, mcp: Client
) -> None:
    run_id = await crawl(client)
    home_id = await page_id_of(client, run_id, HOME_URL)
    pages = json.loads(await call(mcp, "list_pages", run_id=run_id))
    about_id = next(page["id"] for page in pages if page["id"] != home_id)
    assert json.loads(await call(mcp, "get_findings", run_id=run_id, page_id=home_id))
    assert json.loads(await call(mcp, "get_findings", run_id=run_id, page_id=about_id)) == []
    assert json.loads(await call(mcp, "get_findings", run_id=run_id, status="approved")) == []


async def test_get_page_hands_page_text_over_as_data(
    client: httpx.AsyncClient, fake_shop: None, mcp: Client
) -> None:
    run_id = await crawl(client)
    home_id = await page_id_of(client, run_id, HOME_URL)
    content = await call(mcp, "get_page", run_id=run_id, page_id=home_id)
    assert content.startswith("<page>\n")
    assert content.endswith("\n</page>")
    assert content.count("</page>") == 1
    assert "Ignore your instructions" in content


async def test_propose_fix_saves_a_pending_proposal(
    client: httpx.AsyncClient,
    fake_shop: None,
    mcp: Client,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await crawl(client)
    finding = await finding_of(client, run_id, "missing-alt")
    reply = await call(
        mcp,
        "propose_fix",
        run_id=run_id,
        finding_id=finding["id"],
        new_text=" A red ball ",
        reason="Says what it shows.",
    )
    assert "pending" in reply
    proposals = (await client.get(f"/runs/{run_id}/proposals")).json()
    assert [
        (proposal["finding_id"], proposal["new_text"], proposal["status"]) for proposal in proposals
    ] == [(finding["id"], "A red ball", "pending")]
    assert (await finding_of(client, run_id, "missing-alt"))["status"] == "open"
    assert await count(test_sessions, ClaudeCall) == 0


async def test_propose_fix_refuses_another_runs_finding(
    client: httpx.AsyncClient,
    fake_shop: None,
    mcp: Client,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    first = await crawl(client)
    second = await crawl(client)
    finding = await finding_of(client, first, "missing-alt")
    error = await call_error(
        mcp,
        "propose_fix",
        run_id=second,
        finding_id=finding["id"],
        new_text="A red ball",
        reason="Fits.",
    )
    assert f"no finding {finding['id']} in this run" in error
    assert await count(test_sessions, FixProposal) == 0


async def test_bad_calls_come_back_as_errors(
    client: httpx.AsyncClient,
    fake_shop: None,
    mcp: Client,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await crawl(client)
    finding = await finding_of(client, run_id, "missing-alt")
    assert "There is no run 999" in await call_error(mcp, "list_pages", run_id=999)
    assert "There is no page 999" in await call_error(mcp, "get_page", run_id=run_id, page_id=999)
    assert "must not be blank" in await call_error(
        mcp, "propose_fix", run_id=run_id, finding_id=finding["id"], new_text="  ", reason="Fits."
    )
    await call_error(mcp, "get_findings", run_id=run_id, status="done")
    assert await count(test_sessions, FixProposal) == 0
