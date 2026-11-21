# Tests: broken link check

import httpx
import pytest

from app.checks.broken_links import check_broken_links
from app.pages import Link, PageData

pytestmark = pytest.mark.anyio


class FakeSite:
    def __init__(
        self,
        statuses: dict[str, int],
        head_statuses: dict[str, int] | None = None,
        unreachable: set[str] | None = None,
    ) -> None:
        self.statuses = statuses

        self.head_statuses = head_statuses or {}

        self.unreachable = unreachable or set()

        self.requests: list[tuple[str, str]] = []

        self.client = httpx.AsyncClient(
            transport=httpx.MockTransport(self.answer), follow_redirects=True
        )

    def answer(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append((request.method, url))
        if url in self.unreachable:
            raise httpx.ConnectError("Connection refused", request=request)
        if request.method == "HEAD" and url in self.head_statuses:
            return httpx.Response(self.head_statuses[url])
        return httpx.Response(self.statuses.get(url, 404))


def page(url: str, *links: Link, status_code: int = 200) -> PageData:
    return PageData(url=url, final_url=url, status_code=status_code, links=list(links))


def link(href: str, kind: str = "internal", text: str = "Link") -> Link:
    return Link(href=href, text=text, kind=kind)


async def test_link_to_a_missing_page_is_flagged() -> None:
    site = FakeSite({})
    pages = [page("https://shop.test/contact.html", link("https://shop.test/opening-times.html"))]

    findings = await check_broken_links(site.client, pages, concurrency=2)

    assert len(findings) == 1
    finding = findings[0]
    assert (finding.rule, finding.severity) == ("broken-link", "high")
    assert finding.page_url == "https://shop.test/contact.html"
    assert finding.element == "a to /opening-times.html"
    assert finding.evidence == "Returns 404 Not Found"


async def test_working_link_is_not_flagged() -> None:
    site = FakeSite({"https://shop.test/blog": 200})
    pages = [page("https://shop.test/", link("https://shop.test/blog"))]

    assert await check_broken_links(site.client, pages, concurrency=2) == []
    assert site.requests == [("HEAD", "https://shop.test/blog")]


async def test_links_to_crawled_pages_need_no_request() -> None:
    site = FakeSite({})
    pages = [
        page("https://shop.test/", link("https://shop.test/shop.html")),
        page("https://shop.test/shop.html", link("https://shop.test/")),
    ]

    assert await check_broken_links(site.client, pages, concurrency=2) == []
    assert site.requests == []


async def test_link_to_a_crawled_page_that_failed_is_flagged_without_a_request() -> None:
    site = FakeSite({})
    pages = [
        page("https://shop.test/", link("https://shop.test/gone.html")),
        page("https://shop.test/gone.html", status_code=404),
    ]

    findings = await check_broken_links(site.client, pages, concurrency=2)

    assert [finding.evidence for finding in findings] == ["Returns 404 Not Found"]
    assert site.requests == []


async def test_each_target_is_requested_once_but_flagged_on_every_page() -> None:
    site = FakeSite({})
    dead = "https://shop.test/old-offer"
    pages = [
        page("https://shop.test/", link(dead)),
        page("https://shop.test/news.html", link(dead)),
    ]

    findings = await check_broken_links(site.client, pages, concurrency=2)

    assert [finding.page_url for finding in findings] == [
        "https://shop.test/",
        "https://shop.test/news.html",
    ]
    assert site.requests == [("HEAD", dead), ("GET", dead)]


async def test_head_error_is_confirmed_with_get() -> None:
    url = "https://shop.test/picky"
    site = FakeSite({url: 200}, head_statuses={url: 405})
    pages = [page("https://shop.test/", link(url))]

    assert await check_broken_links(site.client, pages, concurrency=2) == []
    assert site.requests == [("HEAD", url), ("GET", url)]


async def test_server_that_does_not_answer_is_flagged() -> None:
    url = "https://shop.test/down"
    site = FakeSite({}, unreachable={url})
    pages = [page("https://shop.test/", link(url))]

    findings = await check_broken_links(site.client, pages, concurrency=2)

    assert [finding.evidence for finding in findings] == ["Could not connect: Connection refused"]


async def test_fragment_is_dropped_before_checking() -> None:
    site = FakeSite({})
    pages = [
        page("https://shop.test/", link("https://shop.test/shop.html#sizes")),
        page("https://shop.test/shop.html"),
    ]

    assert await check_broken_links(site.client, pages, concurrency=2) == []
    assert site.requests == []


async def test_external_links_are_skipped_unless_turned_on() -> None:
    site = FakeSite({})
    pages = [page("https://shop.test/", link("https://elsewhere.test/gone", kind="external"))]

    skipped = await check_broken_links(site.client, pages, concurrency=2)
    assert skipped == []
    assert site.requests == []

    checked = await check_broken_links(site.client, pages, concurrency=2, include_external=True)
    assert [finding.element for finding in checked] == ["a to https://elsewhere.test/gone"]


async def test_mailto_and_tel_links_are_never_requested() -> None:
    site = FakeSite({})
    pages = [
        page(
            "https://shop.test/",
            link("mailto:hi@shop.test", kind="other"),
            link("tel:+441234", kind="other"),
        )
    ]

    assert await check_broken_links(site.client, pages, concurrency=2, include_external=True) == []
    assert site.requests == []
