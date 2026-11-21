# Tests: demo site answer key
# The code checks find every planted code issue in demo-site/KNOWN_ISSUES.md and nothing else.

from pathlib import Path

import pytest

from app.checks import run_code_checks
from app.checks.finding import file_name
from app.pages import PageData, parse_html
from tests.fakes import fake_client

pytestmark = pytest.mark.anyio

DEMO_SITE = Path(__file__).resolve().parents[3] / "demo-site"
PUBLIC = DEMO_SITE / "public"
KNOWN_ISSUES = DEMO_SITE / "KNOWN_ISSUES.md"

BASE = "http://localhost:8080/"

SITEMAP_PAGES = [
    "index.html",
    "about.html",
    "shop.html",
    "news.html",
    "faq.html",
    "delivery.html",
    "contact.html",
]


def planted_code_issues() -> list[tuple[str, str, str]]:
    issues = []
    for line in KNOWN_ISSUES.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 7 and cells[0].isdigit() and cells[6] == "Code":
            issues.append((cells[1], cells[2], cells[3]))
    return sorted(issues)


def demo_pages() -> list[PageData]:
    return [parse_html((PUBLIC / name).read_bytes(), url=BASE + name) for name in SITEMAP_PAGES]


async def test_answer_key_has_the_ten_code_issues() -> None:
    assert len(planted_code_issues()) == 10


async def test_code_checks_find_exactly_the_planted_code_issues() -> None:
    routes = {BASE + name: (200, (PUBLIC / name).read_bytes()) for name in SITEMAP_PAGES}
    client = fake_client(routes)

    findings = await run_code_checks(client, demo_pages(), concurrency=4)
    await client.aclose()

    found = sorted(
        (file_name(finding.page_url), finding.rule, finding.severity) for finding in findings
    )
    assert found == planted_code_issues()


async def test_clean_demo_pages_have_no_findings() -> None:
    client = fake_client(
        {BASE + name: (200, (PUBLIC / name).read_bytes()) for name in SITEMAP_PAGES}
    )

    findings = await run_code_checks(client, demo_pages(), concurrency=4)
    await client.aclose()

    assert [
        finding
        for finding in findings
        if file_name(finding.page_url) in {"index.html", "delivery.html"}
    ] == []
