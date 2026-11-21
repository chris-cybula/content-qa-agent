# Tests: page fetching and parsing
# Includes the demo site's planted issues.

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from app.config import Settings, get_settings
from app.http_client import get_http_client
from app.main import app
from app.pages import Heading, Image, Link, PageData, fetch_page, fetch_pages, parse_html
from tests.fakes import Route, fake_client

pytestmark = pytest.mark.anyio

DEMO_SITE = Path(__file__).resolve().parents[2] / "demo-site" / "public"

SAMPLE = b"""<!doctype html>
<html lang="en-GB">
<head>
  <meta charset="utf-8">
  <title>  Shop   all shapes </title>
  <meta name="Description" content="  Handmade wooden shapes. ">
  <style>.hidden-style { color: red }</style>
  <script>var hidden = "script text";</script>
</head>
<body>
  <h1>Shop</h1>
  <h3>Skipped a level</h3>
  <p>Hello <b>wooden</b>
     world.</p>
  <a href="/about.html">About us</a>
  <a href="https://elsewhere.test/x">Partner</a>
  <a href="mailto:hi@shop.test">Email</a>
  <a href="#top">Back to top</a>
  <a href="contact.html" aria-label="Contact the team"></a>
  <a href="delivery.html"><img src="truck.png" alt="Delivery"></a>
  <img src="/images/a.png">
  <img src="b.png" alt="">
  <noscript>Turn on JavaScript</noscript>
</body>
</html>"""


def demo(name: str) -> PageData:
    return parse_html((DEMO_SITE / name).read_bytes(), url=f"http://localhost:8080/{name}")


async def test_parses_title_meta_and_language() -> None:
    page = parse_html(SAMPLE, url="https://shop.test/products/index.html")

    assert page.title == "Shop all shapes"
    assert page.meta_description == "Handmade wooden shapes."
    assert page.lang == "en-GB"


async def test_parses_headings_in_order() -> None:
    page = parse_html(SAMPLE, url="https://shop.test/products/index.html")

    assert page.headings == [
        Heading(level=1, text="Shop"),
        Heading(level=3, text="Skipped a level"),
    ]


async def test_parses_links_with_kind_and_accessible_name() -> None:
    page = parse_html(SAMPLE, url="https://shop.test/products/index.html")

    assert page.links == [
        Link(href="https://shop.test/about.html", text="About us", kind="internal"),
        Link(href="https://elsewhere.test/x", text="Partner", kind="external"),
        Link(href="mailto:hi@shop.test", text="Email", kind="other"),
        Link(
            href="https://shop.test/products/contact.html", text="Contact the team", kind="internal"
        ),
        Link(href="https://shop.test/products/delivery.html", text="Delivery", kind="internal"),
    ]


async def test_parses_images_and_tells_missing_alt_from_empty_alt() -> None:
    page = parse_html(SAMPLE, url="https://shop.test/products/index.html")

    assert page.images == [
        Image(src="https://shop.test/products/truck.png", alt="Delivery"),
        Image(src="https://shop.test/images/a.png", alt=None),
        Image(src="https://shop.test/products/b.png", alt=""),
    ]


async def test_visible_text_leaves_out_scripts_and_styles() -> None:
    page = parse_html(SAMPLE, url="https://shop.test/products/index.html")

    assert "Hello wooden world." in page.text
    assert "script text" not in page.text
    assert "hidden-style" not in page.text
    assert "Turn on JavaScript" not in page.text
    assert page.word_count == len(page.text.split())


async def test_missing_title_and_meta_are_none() -> None:
    page = parse_html(b"<html><body><p>Hi</p></body></html>", url="https://shop.test/")

    assert page.title is None
    assert page.meta_description is None


async def test_uses_base_href_to_resolve_links() -> None:
    html = b'<html><head><base href="https://shop.test/en/"></head><body><a href="a.html">A</a></body></html>'
    page = parse_html(html, url="https://shop.test/")

    assert page.links[0].href == "https://shop.test/en/a.html"


async def test_reads_the_encoding_from_meta_charset() -> None:
    html = '<html><head><meta charset="iso-8859-1"><title>Café</title></head></html>'.encode(
        "iso-8859-1"
    )
    page = parse_html(html, url="https://shop.test/")

    assert page.title == "Café"


async def test_fetch_page_parses_html() -> None:
    async with fake_client({"https://shop.test/": (200, SAMPLE)}) as client:
        page = await fetch_page(client, "https://shop.test/")

    assert page.status_code == 200
    assert page.error is None
    assert page.title == "Shop all shapes"


async def test_fetch_page_follows_redirects() -> None:
    site: dict[str, Route] = {
        "https://shop.test/old": (301, b"", {"location": "https://shop.test/new/"}),
        "https://shop.test/new/": (200, b'<a href="next.html">Next</a>'),
    }
    async with fake_client(site) as client:
        page = await fetch_page(client, "https://shop.test/old")

    assert page.final_url == "https://shop.test/new/"
    assert page.links[0].href == "https://shop.test/new/next.html"


@pytest.mark.parametrize(
    ("route", "status", "error"),
    [
        ((404, b""), 404, "HTTP 404"),
        ((500, b""), 500, "HTTP 500"),
        (
            (200, b"%PDF-1.7", {"content-type": "application/pdf"}),
            200,
            "Not an HTML page (application/pdf)",
        ),
    ],
)
async def test_fetch_page_records_why_a_page_was_skipped(
    route: Route, status: int, error: str
) -> None:
    async with fake_client({"https://shop.test/x": route}) as client:
        page = await fetch_page(client, "https://shop.test/x")

    assert page.status_code == status
    assert page.error == error
    assert page.title is None


async def test_fetch_page_records_a_network_failure() -> None:

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as client:
        page = await fetch_page(client, "https://shop.test/")

    assert page.status_code is None
    assert page.error is not None
    assert page.error.startswith("Could not download")


async def test_fetch_pages_keeps_order_and_limits_concurrency() -> None:
    in_flight = 0
    most_at_once = 0

    async def slow_site(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, most_at_once
        in_flight += 1
        most_at_once = max(most_at_once, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(
            200,
            content=f"<title>{request.url.path}</title>".encode(),
            headers={"content-type": "text/html"},
        )

    urls = [f"https://shop.test/page-{n}" for n in range(6)]
    async with httpx.AsyncClient(transport=httpx.MockTransport(slow_site)) as client:
        pages = await fetch_pages(client, urls, concurrency=2)

    assert [page.title for page in pages] == [f"/page-{n}" for n in range(6)]
    assert most_at_once == 2


async def test_demo_about_page_shows_missing_alt_and_skipped_heading() -> None:
    page = demo("about.html")

    assert Image(src="http://localhost:8080/images/orange-squares.png", alt=None) in page.images
    assert [heading.level for heading in page.headings] == [1, 3, 2]


async def test_demo_shop_page_shows_missing_meta_and_bad_alt_text() -> None:
    page = demo("shop.html")
    alts = {Path(image.src).name: image.alt for image in page.images}

    assert page.meta_description is None
    assert alts["green-triangle.png"] == "Yellow wooden star"
    assert alts["purple-stripes.png"] == "IMG_0042.png"


async def test_demo_contact_page_shows_short_meta_broken_link_and_vague_text() -> None:
    page = demo("contact.html")
    links = {link.href: link.text for link in page.links}

    assert page.meta_description == "Contact us."
    assert "http://localhost:8080/opening-times.html" in links
    assert links["http://localhost:8080/delivery.html"] == "click here"


async def test_demo_faq_and_news_pages_show_their_planted_issues() -> None:
    faq = demo("faq.html")
    news = demo("news.html")

    assert faq.meta_description is not None
    assert len(faq.meta_description) > 160
    assert [heading.level for heading in faq.headings].count(1) == 2
    assert "Read more" in [link.text for link in news.links]


async def test_demo_clean_page_has_no_obvious_problems() -> None:
    page = demo("index.html")

    assert [heading.level for heading in page.headings].count(1) == 1
    assert all(image.alt for image in page.images)
    assert page.meta_description is not None
    assert 50 <= len(page.meta_description) <= 160


@pytest.fixture
async def fake_shop() -> AsyncIterator[None]:
    sitemap = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://shop.test/</loc></url>
      <url><loc>https://shop.test/gone.html</loc></url>
    </urlset>"""
    fake = fake_client(
        {
            "https://shop.test/sitemap.xml": (200, sitemap, {"content-type": "application/xml"}),
            "https://shop.test/": (200, SAMPLE),
        }
    )
    app.dependency_overrides[get_http_client] = lambda: fake
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, max_pages=10)
    yield
    app.dependency_overrides.clear()
    await fake.aclose()


async def test_crawl_endpoint_fetches_every_page(
    client: httpx.AsyncClient, fake_shop: None
) -> None:
    response = await client.post("/crawl", json={"sitemap_url": "https://shop.test/sitemap.xml"})
    data = response.json()

    assert response.status_code == 200
    assert data["count"] == 2
    assert data["failed"] == 1
    assert data["pages"][0]["title"] == "Shop all shapes"
    assert data["pages"][1]["error"] == "HTTP 404"


async def test_crawl_endpoint_reports_a_broken_sitemap_as_502(
    client: httpx.AsyncClient, fake_shop: None
) -> None:
    response = await client.post("/crawl", json={"sitemap_url": "https://shop.test/missing.xml"})

    assert response.status_code == 502
