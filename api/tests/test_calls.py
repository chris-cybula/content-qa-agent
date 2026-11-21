# Tests: the Claude calls endpoint for a run

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.tables import ClaudeCall, Run

pytestmark = pytest.mark.anyio


def call(run_id: int | None, purpose: str, cost: int, **counts: int | str | None) -> ClaudeCall:
    fields: dict[str, int | str | None] = {
        "status": "answered",
        "input_tokens": 300,
        "cache_read_tokens": 1_600,
        "cache_write_tokens": 0,
        "output_tokens": 10,
        "duration_ms": 1_800,
        **counts,
    }
    return ClaudeCall(
        run_id=run_id,
        purpose=purpose,
        model="claude-opus-5-5",
        max_tokens=4000,
        reserved_microdollars=108_000,
        cost_microdollars=cost,
        **fields,
    )


async def save_run_with_calls(sessions: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    async with sessions() as session:
        first, second = (
            Run(sitemap_url="https://shop.test/sitemap.xml"),
            Run(sitemap_url="https://other.test/sitemap.xml"),
        )
        session.add_all([first, second])
        await session.flush()
        session.add_all(
            [
                call(
                    first.id,
                    "page-check",
                    9_721,
                    input_tokens=379,
                    cache_read_tokens=0,
                    cache_write_tokens=1_601,
                ),
                call(first.id, "page-check", 1_985),
                call(
                    first.id,
                    "vision-check",
                    0,
                    status="failed",
                    input_tokens=None,
                    cache_read_tokens=None,
                    cache_write_tokens=None,
                    output_tokens=None,
                    duration_ms=120,
                    error="HTTP 529: Overloaded",
                ),
                call(second.id, "page-check", 50_000),
            ]
        )
        await session.commit()
        return first.id, second.id


async def test_a_runs_calls_come_back_oldest_first_with_dollar_costs(
    client: httpx.AsyncClient,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    run_id, _ = await save_run_with_calls(test_sessions)

    body = (await client.get(f"/runs/{run_id}/calls")).json()

    assert body["run_id"] == run_id
    assert [(row["purpose"], row["status"], row["cost_usd"]) for row in body["calls"]] == [
        ("page-check", "answered", "0.0097"),
        ("page-check", "answered", "0.0020"),
        ("vision-check", "failed", "0.0000"),
    ]
    assert body["calls"][0]["cache_write_tokens"] == 1_601
    assert body["calls"][2]["error"] == "HTTP 529: Overloaded"


async def test_totals_add_up_every_call_before_rounding(
    client: httpx.AsyncClient,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    run_id, _ = await save_run_with_calls(test_sessions)

    totals = (await client.get(f"/runs/{run_id}/calls")).json()["totals"]

    assert totals == {
        "calls": 3,
        "input_tokens": 379 + 300,
        "cache_read_tokens": 1_600,
        "cache_write_tokens": 1_601,
        "output_tokens": 20,
        "cost_usd": "0.011706",
        "duration_ms": 1_800 + 1_800 + 120,
    }


async def test_a_run_without_calls_has_zero_totals(
    client: httpx.AsyncClient,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessions() as session:
        run = Run(sitemap_url="https://shop.test/sitemap.xml")
        session.add(run)
        await session.commit()

    body = (await client.get(f"/runs/{run.id}/calls")).json()

    assert body["calls"] == []
    assert (body["totals"]["calls"], body["totals"]["cost_usd"]) == (0, "0")


async def test_unknown_run_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/runs/999/calls")

    assert response.status_code == 404
    assert response.json() == {"detail": "No run with id 999"}
