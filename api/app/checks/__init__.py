# Code checks
# Runs every no-AI check on a run's pages; the broken link check looks at all pages together.

from collections.abc import Callable

import httpx

from app.checks.alt_text import check_alt_text
from app.checks.broken_links import check_broken_links
from app.checks.finding import FindingData
from app.checks.headings import check_headings
from app.checks.link_text import check_link_text
from app.checks.meta_description import check_meta_description
from app.pages import PageData

PAGE_CHECKS: list[Callable[[PageData], list[FindingData]]] = [
    check_alt_text,
    check_headings,
    check_meta_description,
    check_link_text,
]


def run_page_checks(page: PageData) -> list[FindingData]:
    if page.error is not None:
        return []
    return [finding for check in PAGE_CHECKS for finding in check(page)]


async def run_code_checks(
    client: httpx.AsyncClient,
    pages: list[PageData],
    concurrency: int,
    include_external_links: bool = False,
) -> list[FindingData]:
    findings = [finding for page in pages for finding in run_page_checks(page)]
    findings += await check_broken_links(client, pages, concurrency, include_external_links)

    order = {page.url: position for position, page in enumerate(pages)}
    return sorted(findings, key=lambda finding: order[finding.page_url])


__all__ = ["PAGE_CHECKS", "FindingData", "run_code_checks", "run_page_checks"]
