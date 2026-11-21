# Tests: saving crawl runs and reading them back

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.http_client import get_http_client
from app.main import app
from app.pages import Heading, Image, Link, PageData
from app.runs import page_row
from app.tables import Page, Run
from tests.fakes import fake_client

pytestmark = pytest.mark.anyio

SITEMAP = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://shop.test/</loc></url>
  <url><loc>https://shop.test/gone.html</loc></url>
</urlset>"""
HOME = b"""<html lang="en-GB"><head><title>Home</title><meta name="description" content="Wooden toys."></head>
<body><h1>Welcome</h1><a href="/shop.html">Shop</a><img src="/a.png"></body></html>"""


@pytest.fixture
async def fake_shop() -> AsyncIterator[None]:
    fake = fake_client(
        {
            "https://shop.test/sitemap.xml": (200, SITEMAP, {"content-type": "application/xml"}),
            "https://shop.test/": (200, HOME),
        }
    )
    app.dependency_overrides[get_http_client] = lambda: fake
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, max_pages=10)
    yield
    app.dependency_overrides.pop(get_http_client, None)
    app.dependency_overrides.pop(get_settings, None)
    await fake.aclose()


async def test_crawl_saves_the_run_and_its_pages(
    client: httpx.AsyncClient,
    fake_shop: None,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    response = await client.post("/crawl", json={"sitemap_url": "https://shop.test/sitemap.xml"})
    assert response.status_code == 200
    run_id = response.json()["run_id"]

    async with test_sessions() as session:
        run = await session.get(Run, run_id)
        pages = (
            await session.scalars(select(Page).where(Page.run_id == run_id).order_by(Page.position))
        ).all()

    assert run is not None
    assert run.status == "finished"
    assert (run.page_count, run.failed_count) == (2, 1)
    assert run.finished_at is not None
    assert [page.url for page in pages] == ["https://shop.test/", "https://shop.test/gone.html"]
    assert pages[0].headings == [{"level": 1, "text": "Welcome"}]
    assert pages[0].images == [{"src": "https://shop.test/a.png", "alt": None}]
    assert pages[1].error == "HTTP 404"


async def test_saved_run_reads_back_with_every_page_detail(
    client: httpx.AsyncClient, fake_shop: None
) -> None:
    crawl = (
        await client.post("/crawl", json={"sitemap_url": "https://shop.test/sitemap.xml"})
    ).json()

    response = await client.get(f"/runs/{crawl['run_id']}")
    saved = response.json()

    assert response.status_code == 200
    assert saved["status"] == "finished"
    assert saved["pages"] == crawl["pages"]


async def test_failed_sitemap_is_saved_as_a_failed_run(
    client: httpx.AsyncClient, fake_shop: None
) -> None:
    response = await client.post("/crawl", json={"sitemap_url": "https://shop.test/missing.xml"})
    runs = (await client.get("/runs")).json()

    assert response.status_code == 502
    assert runs[0]["status"] == "failed"
    assert "HTTP 404" in runs[0]["error"]
    assert runs[0]["page_count"] == 0


async def test_runs_are_listed_newest_first(client: httpx.AsyncClient, fake_shop: None) -> None:
    first = (
        await client.post("/crawl", json={"sitemap_url": "https://shop.test/sitemap.xml"})
    ).json()
    second = (
        await client.post("/crawl", json={"sitemap_url": "https://shop.test/sitemap.xml"})
    ).json()

    runs = (await client.get("/runs")).json()

    assert [run["id"] for run in runs] == [second["run_id"], first["run_id"]]


async def test_unknown_run_is_a_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/runs/999")

    assert response.status_code == 404
    assert response.json()["detail"] == "No run with id 999"


async def test_deleting_a_run_deletes_its_pages(
    client: httpx.AsyncClient,
    fake_shop: None,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    run_id = (
        await client.post("/crawl", json={"sitemap_url": "https://shop.test/sitemap.xml"})
    ).json()["run_id"]

    async with test_sessions() as session:
        run = await session.get(Run, run_id)
        await session.delete(run)
        await session.commit()
        remaining = await session.scalar(select(func.count()).select_from(Page))

    assert remaining == 0


async def test_page_row_turns_nested_models_into_json() -> None:
    data = PageData(
        url="https://shop.test/",
        status_code=200,
        headings=[Heading(level=2, text="Hi")],
        links=[Link(href="https://shop.test/a", text="A", kind="internal")],
        images=[Image(src="https://shop.test/b.png", alt="")],
    )

    row = page_row(run_id=7, position=3, data=data)

    assert (row.run_id, row.position, row.status_code) == (7, 3, 200)
    assert row.headings == [{"level": 2, "text": "Hi"}]
    assert row.links == [{"href": "https://shop.test/a", "text": "A", "kind": "internal"}]
    assert row.images == [{"src": "https://shop.test/b.png", "alt": ""}]
