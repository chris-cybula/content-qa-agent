# Tests: eval clean set
# The code checks find nothing on any of its 20 pages.

from pathlib import Path
from urllib.parse import urljoin, urlparse

import pytest
from defusedxml import ElementTree

from app.checks import run_code_checks, run_page_checks
from app.pages import PageData, parse_html
from tests.fakes import fake_client

PUBLIC = Path(__file__).resolve().parents[3] / "demo-site" / "public"
CLEAN_SET = PUBLIC / "evals" / "clean"

SITE = "http://localhost:8080/"

PAGE_COUNT = 20

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def sitemap_urls() -> list[str]:
    root = ElementTree.parse(CLEAN_SET / "sitemap.xml").getroot()
    return [loc.text or "" for loc in root.iter(f"{SITEMAP_NS}loc")]


def file_for(url: str) -> Path:
    path = urlparse(url).path.lstrip("/") or "index.html"
    return PUBLIC / path


def clean_pages() -> list[PageData]:
    return [parse_html(file_for(url).read_bytes(), url=url) for url in sitemap_urls()]


def test_sitemap_lists_every_page_once() -> None:
    urls = sitemap_urls()
    files = sorted(path.name for path in CLEAN_SET.glob("*.html"))

    assert len(urls) == PAGE_COUNT
    assert len(set(urls)) == PAGE_COUNT
    assert sorted(file_for(url).name for url in urls) == files


@pytest.mark.parametrize("page", clean_pages(), ids=lambda page: Path(page.url).name)
def test_page_checks_find_nothing(page: PageData) -> None:
    assert run_page_checks(page) == []


@pytest.mark.anyio
async def test_no_link_on_any_page_is_broken() -> None:
    routes = {
        urljoin(SITE, path.relative_to(PUBLIC).as_posix()): (200, path.read_bytes())
        for path in PUBLIC.rglob("*.html")
    }
    routes[SITE] = routes[urljoin(SITE, "index.html")]
    client = fake_client(routes)

    findings = await run_code_checks(client, clean_pages(), concurrency=4)
    await client.aclose()

    assert findings == []


@pytest.mark.parametrize("page", clean_pages(), ids=lambda page: Path(page.url).name)
def test_every_image_exists(page: PageData) -> None:
    assert all(file_for(image.src).is_file() for image in page.images)


def test_set_has_pages_with_images_and_without() -> None:
    with_images = [page for page in clean_pages() if page.images]

    assert 0 < len(with_images) < PAGE_COUNT
