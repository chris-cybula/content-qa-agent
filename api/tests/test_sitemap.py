# Tests: sitemap reader
# Uses a fake website instead of the network.

from collections.abc import AsyncIterator

import httpx
import pytest

from app.config import Settings, get_settings
from app.http_client import get_http_client
from app.main import app
from app.sitemap import SitemapError, read_sitemap
from tests.fakes import fake_client

pytestmark = pytest.mark.anyio

URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://shop.test/</loc></url>
  <url><loc>https://shop.test/about.html</loc></url>
  <url><loc>https://shop.test/about.html</loc></url>
  <url><loc>https://elsewhere.test/page.html</loc></url>
  <url><loc>mailto:hello@shop.test</loc></url>
  <url><loc>https://shop.test/contact.html</loc></url>
</urlset>"""

INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://shop.test/a.xml</loc></sitemap>
  <sitemap><loc>https://shop.test/b.xml</loc></sitemap>
  <sitemap><loc>https://elsewhere.test/c.xml</loc></sitemap>
</sitemapindex>"""

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">]>
<urlset><url><loc>&lol2;</loc></url></urlset>"""


def urlset(*urls: str) -> bytes:
    entries = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
    return (
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>'.encode()
    )


async def test_reads_pages_in_order_without_duplicates_or_other_sites() -> None:
    async with fake_client({"https://shop.test/sitemap.xml": (200, URLSET)}) as client:
        pages = await read_sitemap(client, "https://shop.test/sitemap.xml", max_pages=50)

    assert pages == [
        "https://shop.test/",
        "https://shop.test/about.html",
        "https://shop.test/contact.html",
    ]


async def test_stops_at_the_page_limit() -> None:
    async with fake_client({"https://shop.test/sitemap.xml": (200, URLSET)}) as client:
        pages = await read_sitemap(client, "https://shop.test/sitemap.xml", max_pages=2)

    assert pages == ["https://shop.test/", "https://shop.test/about.html"]


async def test_follows_a_sitemap_index_on_the_same_site() -> None:
    site = {
        "https://shop.test/sitemap.xml": (200, INDEX),
        "https://shop.test/a.xml": (200, urlset("https://shop.test/one.html")),
        "https://shop.test/b.xml": (200, urlset("https://shop.test/two.html")),
    }
    async with fake_client(site) as client:
        pages = await read_sitemap(client, "https://shop.test/sitemap.xml", max_pages=50)

    assert pages == ["https://shop.test/one.html", "https://shop.test/two.html"]


async def test_reads_a_sitemap_without_a_namespace() -> None:
    body = b"<urlset><url><loc> https://shop.test/x.html </loc></url></urlset>"
    async with fake_client({"https://shop.test/sitemap.xml": (200, body)}) as client:
        pages = await read_sitemap(client, "https://shop.test/sitemap.xml", max_pages=50)

    assert pages == ["https://shop.test/x.html"]


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (404, b"", "HTTP 404"),
        (200, b"<html>not closed", "Not valid sitemap XML"),
        (200, b"<rss></rss>", "Expected <urlset> or <sitemapindex>"),
        (200, BILLION_LAUGHS, "Not valid sitemap XML"),
    ],
)
async def test_bad_sitemaps_raise_a_clear_error(status: int, body: bytes, message: str) -> None:
    async with fake_client({"https://shop.test/sitemap.xml": (status, body)}) as client:
        with pytest.raises(SitemapError, match=message):
            await read_sitemap(client, "https://shop.test/sitemap.xml", max_pages=50)


async def test_refuses_a_sitemap_that_is_too_large() -> None:
    body = urlset(*[f"https://shop.test/{n}.html" for n in range(100)])
    async with fake_client({"https://shop.test/sitemap.xml": (200, body)}) as client:
        with pytest.raises(SitemapError, match="larger than"):
            await read_sitemap(client, "https://shop.test/sitemap.xml", max_pages=50, max_bytes=500)


async def test_network_failure_raises_a_clear_error() -> None:

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as client:
        with pytest.raises(SitemapError, match="Could not download"):
            await read_sitemap(client, "https://shop.test/sitemap.xml", max_pages=50)


@pytest.fixture
async def fake_site() -> AsyncIterator[None]:
    fake = fake_client({"https://shop.test/sitemap.xml": (200, URLSET)})
    app.dependency_overrides[get_http_client] = lambda: fake
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, max_pages=2)
    yield
    app.dependency_overrides.clear()
    await fake.aclose()


async def test_endpoint_lists_pages(client: httpx.AsyncClient, fake_site: None) -> None:
    response = await client.get(
        "/sitemap/pages", params={"sitemap_url": "https://shop.test/sitemap.xml"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "sitemap_url": "https://shop.test/sitemap.xml",
        "count": 2,
        "max_pages": 2,
        "pages": ["https://shop.test/", "https://shop.test/about.html"],
    }


async def test_endpoint_reports_a_broken_sitemap_as_502(
    client: httpx.AsyncClient, fake_site: None
) -> None:
    response = await client.get(
        "/sitemap/pages", params={"sitemap_url": "https://shop.test/missing.xml"}
    )

    assert response.status_code == 502
    assert "HTTP 404" in response.json()["detail"]


async def test_endpoint_rejects_an_invalid_url(client: httpx.AsyncClient, fake_site: None) -> None:
    response = await client.get("/sitemap/pages", params={"sitemap_url": "not-a-url"})

    assert response.status_code == 422
