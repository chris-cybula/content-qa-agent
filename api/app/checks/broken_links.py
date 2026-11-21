# Broken link check
# Links to error pages or dead servers; each target is requested once, internal links only by default.

import asyncio
from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import urldefrag

import httpx

from app.checks.finding import FindingData, describe_link
from app.pages import Link, PageData


@dataclass
class LinkStatus:
    status_code: int | None

    error: str | None = None

    @property
    def broken(self) -> bool:
        return self.status_code is None or self.status_code >= 400

    def evidence(self) -> str:
        if self.status_code is None:
            return f"Could not connect: {self.error}"
        try:
            return f"Returns {self.status_code} {HTTPStatus(self.status_code).phrase}"
        except ValueError:
            return f"Returns {self.status_code}"


def target(href: str) -> str:
    return urldefrag(href).url


def links_to_check(page: PageData, include_external: bool) -> list[Link]:
    kinds = {"internal", "external"} if include_external else {"internal"}
    return [link for link in page.links if link.kind in kinds]


def known_statuses(pages: list[PageData]) -> dict[str, LinkStatus]:
    known = {}
    for page in pages:
        if page.status_code is None:
            continue
        for url in (page.url, page.final_url):
            if url:
                known[target(url)] = LinkStatus(status_code=page.status_code)
    return known


async def request_status(client: httpx.AsyncClient, url: str) -> LinkStatus:
    try:
        response = await client.head(url)
        if response.status_code >= 400:
            async with client.stream("GET", url) as confirmed:
                return LinkStatus(status_code=confirmed.status_code)
        return LinkStatus(status_code=response.status_code)
    except httpx.HTTPError as error:
        return LinkStatus(status_code=None, error=str(error) or type(error).__name__)


async def check_broken_links(
    client: httpx.AsyncClient,
    pages: list[PageData],
    concurrency: int,
    include_external: bool = False,
) -> list[FindingData]:
    statuses = known_statuses(pages)

    targets = {
        target(link.href) for page in pages for link in links_to_check(page, include_external)
    }
    to_request = sorted(targets - statuses.keys())

    semaphore = asyncio.Semaphore(concurrency)

    async def request_one(url: str) -> tuple[str, LinkStatus]:
        async with semaphore:
            return url, await request_status(client, url)

    for url, status in await asyncio.gather(*(request_one(url) for url in to_request)):
        statuses[url] = status

    return [
        FindingData(
            page_url=page.url,
            rule="broken-link",
            severity="high",
            element=describe_link(link),
            evidence=statuses[target(link.href)].evidence(),
            suggested_fix="Point the link at a page that exists, or remove it.",
        )
        for page in pages
        for link in links_to_check(page, include_external)
        if statuses[target(link.href)].broken
    ]
