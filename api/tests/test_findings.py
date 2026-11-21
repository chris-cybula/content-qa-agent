# Tests: code checks during a crawl, saved findings

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.http_client import get_http_client
from app.main import app
from app.tables import Finding, Page, Run
from tests.fakes import fake_client

pytestmark = pytest.mark.anyio

SITEMAP = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://shop.test/</loc></url>
  <url><loc>https://shop.test/about.html</loc></url>
</urlset>"""
HOME = b"""<html lang="en-GB"><head><title>Home</title><meta name="description" content="Wooden toys."></head>
<body><h1>Welcome</h1><a href="/about.html">About us</a><a href="/sale.html">Sale</a><img src="/a.png"></body></html>"""
ABOUT = b"""<html lang="en-GB"><head><title>About</title>
<meta name="description" content="Meet the small team that designs and makes our wooden shapes by hand in Devon.">
</head><body><h1>About us</h1><a href="/">Home</a></body></html>"""

HOME_FINDINGS = [
    ("missing-alt", "img a.png"),
    ("meta-too-short", "meta description"),
    ("broken-link", "a to /sale.html"),
]


@pytest.fixture
async def fake_shop() -> AsyncIterator[None]:
    fake = fake_client(
        {
            "https://shop.test/sitemap.xml": (200, SITEMAP, {"content-type": "application/xml"}),
            "https://shop.test/": (200, HOME),
            "https://shop.test/about.html": (200, ABOUT),
        }
    )
    app.dependency_overrides[get_http_client] = lambda: fake
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, max_pages=10)
    yield
    app.dependency_overrides.pop(get_http_client, None)
    app.dependency_overrides.pop(get_settings, None)
    await fake.aclose()


async def crawl(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post("/crawl", json={"sitemap_url": "https://shop.test/sitemap.xml"})
    assert response.status_code == 200
    return response.json()


async def test_crawl_returns_the_findings(client: httpx.AsyncClient, fake_shop: None) -> None:
    body = await crawl(client)

    assert body["finding_count"] == 3
    assert [(finding["rule"], finding["element"]) for finding in body["findings"]] == HOME_FINDINGS
    assert {finding["page_url"] for finding in body["findings"]} == {"https://shop.test/"}


async def test_crawl_saves_each_finding_on_its_page(
    client: httpx.AsyncClient,
    fake_shop: None,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    run_id = (await crawl(client))["run_id"]

    async with test_sessions() as session:
        home = await session.scalar(select(Page).where(Page.run_id == run_id, Page.position == 0))
        rows = (
            await session.scalars(
                select(Finding).where(Finding.run_id == run_id).order_by(Finding.id)
            )
        ).all()

    assert home is not None
    assert [(row.rule, row.element) for row in rows] == HOME_FINDINGS
    assert {row.page_id for row in rows} == {home.id}
    assert rows[0].severity == "high"
    assert rows[0].evidence == "No alt attribute"
    assert rows[0].suggested_fix
    assert rows[0].created_at is not None


async def test_findings_endpoint_reads_them_back(
    client: httpx.AsyncClient, fake_shop: None
) -> None:
    body = await crawl(client)

    response = await client.get(f"/runs/{body['run_id']}/findings")
    saved = response.json()

    assert response.status_code == 200
    assert [(finding["rule"], finding["element"]) for finding in saved] == HOME_FINDINGS
    assert all(isinstance(finding["id"], int) for finding in saved)
    assert {finding["status"] for finding in saved} == {"open"}
    stored_only = {"id", "page_id", "status"}
    assert [
        {key: value for key, value in finding.items() if key not in stored_only}
        for finding in saved
    ] == body["findings"]


async def test_run_without_findings_gives_an_empty_list(
    client: httpx.AsyncClient, fake_shop: None
) -> None:
    await client.post("/crawl", json={"sitemap_url": "https://shop.test/missing.xml"})
    failed_run = (await client.get("/runs")).json()[0]

    response = await client.get(f"/runs/{failed_run['id']}/findings")

    assert response.status_code == 200
    assert response.json() == []


async def test_findings_of_an_unknown_run_is_a_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/runs/999/findings")

    assert response.status_code == 404
    assert response.json()["detail"] == "No run with id 999"


async def test_deleting_a_run_deletes_its_findings(
    client: httpx.AsyncClient,
    fake_shop: None,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    run_id = (await crawl(client))["run_id"]

    async with test_sessions() as session:
        await session.delete(await session.get(Run, run_id))
        await session.commit()
        remaining = await session.scalar(select(func.count()).select_from(Finding))

    assert remaining == 0
