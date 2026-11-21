# Tests: link text check

import pytest

from app.checks.link_text import check_link_text, normalise
from app.pages import Link, PageData

PAGE_URL = "https://shop.test/contact.html"


def page_with(*links: Link) -> PageData:
    return PageData(url=PAGE_URL, status_code=200, links=list(links))


def internal(text: str, path: str = "/delivery.html") -> Link:
    return Link(href=f"https://shop.test{path}", text=text, kind="internal")


def test_click_here_is_flagged_with_where_the_link_goes() -> None:
    findings = check_link_text(page_with(internal("click here")))

    assert len(findings) == 1
    finding = findings[0]
    assert (finding.rule, finding.severity) == ("vague-link-text", "low")
    assert finding.element == "a to /delivery.html"
    assert finding.evidence == 'Link text is "click here"'


@pytest.mark.parametrize(
    "text", ["Read more", "READ MORE »", "Click here!", "Learn more →", "here", "More…"]
)
def test_vague_text_is_found_whatever_the_case_or_punctuation(text: str) -> None:
    assert [finding.rule for finding in check_link_text(page_with(internal(text)))] == [
        "vague-link-text"
    ]


@pytest.mark.parametrize(
    "text", ["Delivery and returns", "Read more about delivery", "Contact our team", "Shop"]
)
def test_descriptive_text_passes(text: str) -> None:
    assert check_link_text(page_with(internal(text))) == []


def test_link_with_no_text_is_flagged_as_empty() -> None:
    findings = check_link_text(page_with(internal("", "/basket")))

    assert [(finding.rule, finding.severity, finding.element) for finding in findings] == [
        ("empty-link-text", "high", "a to /basket"),
    ]


def test_external_links_show_their_full_address() -> None:
    link = Link(href="https://elsewhere.test/page?id=3", text="Read more", kind="external")

    assert check_link_text(page_with(link))[0].element == "a to https://elsewhere.test/page?id=3"


def test_internal_links_keep_their_query_string() -> None:
    findings = check_link_text(page_with(internal("more", "/shop.html?colour=red")))

    assert findings[0].element == "a to /shop.html?colour=red"


def test_normalise_drops_case_punctuation_and_extra_spaces() -> None:
    assert normalise("  Read   More » ") == "read more"
