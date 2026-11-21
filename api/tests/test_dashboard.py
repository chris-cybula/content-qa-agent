# Tests: dashboard endpoints
# Run totals, pages list, reviews and report export.

import csv
import io
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.http_client import get_http_client
from app.main import app
from app.tables import ClaudeCall
from tests.fakes import fake_client

pytestmark = pytest.mark.anyio

SITEMAP = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://shop.test/</loc></url>
  <url><loc>https://shop.test/about.html</loc></url>
</urlset>"""
HOME = b"""<html lang="en-GB"><head><title>Home</title><meta name="description" content="Wooden toys."></head>
<body><h1>Welcome</h1><a href="/about.html">About us</a><a href="/sale.html">Sale</a>
<img src="/a.png"><img src="/b.png" alt="IMG_0042.png"></body></html>"""
ABOUT = b"""<html lang="en-GB"><head><title>About</title>
<meta name="description" content="Meet the small team that designs and makes our wooden shapes by hand in Devon.">
</head><body><h1>About us</h1><a href="/">Home</a></body></html>"""

HOME_URL = "https://shop.test/"
ABOUT_URL = "https://shop.test/about.html"


@pytest.fixture
async def fake_shop() -> AsyncIterator[None]:
    fake = fake_client(
        {
            "https://shop.test/sitemap.xml": (200, SITEMAP, {"content-type": "application/xml"}),
            HOME_URL: (200, HOME),
            ABOUT_URL: (200, ABOUT),
        }
    )
    app.dependency_overrides[get_http_client] = lambda: fake
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, max_pages=10)
    yield
    app.dependency_overrides.pop(get_http_client, None)
    app.dependency_overrides.pop(get_settings, None)
    await fake.aclose()


async def crawl(client: httpx.AsyncClient) -> int:
    response = await client.post("/crawl", json={"sitemap_url": "https://shop.test/sitemap.xml"})
    assert response.status_code == 200
    return response.json()["run_id"]


async def findings_of(
    client: httpx.AsyncClient, run_id: int, query: str = ""
) -> list[dict[str, Any]]:
    response = await client.get(f"/runs/{run_id}/findings{query}")
    assert response.status_code == 200
    return response.json()


async def review(client: httpx.AsyncClient, finding_id: int, status: str) -> httpx.Response:
    return await client.patch(f"/findings/{finding_id}", json={"status": status})


async def log_calls(sessions: async_sessionmaker[AsyncSession], run_id: int, *costs: int) -> None:
    async with sessions() as session:
        session.add_all(
            [
                ClaudeCall(
                    run_id=run_id,
                    purpose="page-check",
                    model="claude-opus-5-5",
                    status="answered",
                    max_tokens=4000,
                    reserved_microdollars=cost,
                    cost_microdollars=cost,
                    duration_ms=1_500,
                )
                for cost in costs
            ]
        )
        await session.commit()


async def test_runs_list_shows_findings_cost_and_claude_time(
    client: httpx.AsyncClient,
    fake_shop: None,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await crawl(client)
    await log_calls(test_sessions, run_id, 9_721, 1_985)

    [run] = (await client.get("/runs")).json()

    assert (run["id"], run["finding_count"], run["open_count"]) == (run_id, 4, 4)
    assert (run["claude_calls"], run["cost_usd"], run["ai_duration_ms"]) == (2, "0.011706", 3_000)


async def test_a_run_without_ai_checks_costs_nothing(
    client: httpx.AsyncClient, fake_shop: None
) -> None:
    await crawl(client)

    [run] = (await client.get("/runs")).json()

    assert (run["claude_calls"], run["cost_usd"], run["ai_duration_ms"]) == (0, "0", 0)


async def test_run_detail_carries_the_same_totals(
    client: httpx.AsyncClient,
    fake_shop: None,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await crawl(client)
    await log_calls(test_sessions, run_id, 2_000)

    detail = (await client.get(f"/runs/{run_id}")).json()

    assert (detail["finding_count"], detail["claude_calls"], detail["cost_usd"]) == (4, 1, "0.002")


async def test_pages_list_counts_findings_per_page(
    client: httpx.AsyncClient, fake_shop: None
) -> None:
    run_id = await crawl(client)

    pages = (await client.get(f"/runs/{run_id}/pages")).json()

    assert [
        (page["url"], page["title"], page["finding_count"], page["open_count"]) for page in pages
    ] == [
        (HOME_URL, "Home", 4, 4),
        (ABOUT_URL, "About", 0, 0),
    ]


async def test_pages_list_of_an_unknown_run_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/runs/999/pages")

    assert response.status_code == 404


async def test_findings_can_be_filtered_by_page_and_status(
    client: httpx.AsyncClient, fake_shop: None
) -> None:
    run_id = await crawl(client)
    pages = (await client.get(f"/runs/{run_id}/pages")).json()
    home_id, about_id = pages[0]["id"], pages[1]["id"]

    assert len(await findings_of(client, run_id, f"?page_id={home_id}")) == 4
    assert await findings_of(client, run_id, f"?page_id={about_id}") == []
    assert await findings_of(client, run_id, "?status=approved") == []


async def test_approving_and_rejecting_updates_the_finding_and_the_counts(
    client: httpx.AsyncClient,
    fake_shop: None,
) -> None:
    run_id = await crawl(client)
    first, second, *_ = await findings_of(client, run_id)

    approved = await review(client, first["id"], "approved")
    await review(client, second["id"], "rejected")

    assert approved.status_code == 200
    assert (approved.json()["id"], approved.json()["status"]) == (first["id"], "approved")
    assert [finding["id"] for finding in await findings_of(client, run_id, "?status=rejected")] == [
        second["id"]
    ]
    pages = (await client.get(f"/runs/{run_id}/pages")).json()
    assert (pages[0]["finding_count"], pages[0]["open_count"]) == (4, 2)
    assert (await client.get("/runs")).json()[0]["open_count"] == 2


async def test_a_review_can_be_undone(client: httpx.AsyncClient, fake_shop: None) -> None:
    run_id = await crawl(client)
    [finding, *_] = await findings_of(client, run_id)
    await review(client, finding["id"], "rejected")

    response = await review(client, finding["id"], "open")

    assert response.json()["status"] == "open"
    assert await findings_of(client, run_id, "?status=rejected") == []


async def test_reviewing_an_unknown_finding_is_404(client: httpx.AsyncClient) -> None:
    response = await review(client, 999, "approved")

    assert response.status_code == 404
    assert response.json() == {"detail": "No finding with id 999"}


async def test_only_the_three_review_states_are_accepted(
    client: httpx.AsyncClient, fake_shop: None
) -> None:
    run_id = await crawl(client)
    [finding, *_] = await findings_of(client, run_id)

    response = await review(client, finding["id"], "done")

    assert response.status_code == 422
    assert (await findings_of(client, run_id))[0]["status"] == "open"


async def test_csv_report_is_a_download_without_rejected_findings(
    client: httpx.AsyncClient, fake_shop: None
) -> None:
    run_id = await crawl(client)
    [finding, *_] = await findings_of(client, run_id)
    await review(client, finding["id"], "rejected")

    response = await client.get(f"/runs/{run_id}/report")

    assert response.headers["content-type"].startswith("text/csv")
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="run-{run_id}-findings.csv"'
    )
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == [
        "page_url",
        "rule",
        "severity",
        "element",
        "evidence",
        "suggested_fix",
        "status",
    ]
    assert len(rows) == 1 + 3
    assert all(row[6] == "open" for row in rows[1:])


async def test_rejected_findings_can_be_included(
    client: httpx.AsyncClient, fake_shop: None
) -> None:
    run_id = await crawl(client)
    [finding, *_] = await findings_of(client, run_id)
    await review(client, finding["id"], "rejected")

    response = await client.get(f"/runs/{run_id}/report?include_rejected=true")

    rows = list(csv.reader(io.StringIO(response.text)))
    assert sorted(row[6] for row in rows[1:]) == ["open", "open", "open", "rejected"]


async def test_html_report_is_a_standalone_page(client: httpx.AsyncClient, fake_shop: None) -> None:
    run_id = await crawl(client)

    response = await client.get(f"/runs/{run_id}/report?format=html")

    assert response.headers["content-type"].startswith("text/html")
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="run-{run_id}-findings.html"'
    )
    assert f"Content QA report: run {run_id}" in response.text
    assert response.text.count('<tr><td class="report__cell') == 4
    assert "Alt is &quot;IMG_0042.png&quot;" in response.text


async def test_report_of_an_unknown_run_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.get("/runs/999/report")).status_code == 404
    assert (await client.get("/runs/999/report?format=html")).status_code == 404
