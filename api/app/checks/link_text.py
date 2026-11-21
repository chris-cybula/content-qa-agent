# Link text check
# Links with no text, or vague text like “click here”.

import re

from app.checks.finding import FindingData, describe_link
from app.pages import Link, PageData

VAGUE_TEXTS = {
    "click",
    "click here",
    "continue",
    "continue reading",
    "details",
    "find out more",
    "go",
    "here",
    "info",
    "learn more",
    "link",
    "more",
    "more info",
    "more information",
    "read more",
    "see more",
    "this",
    "this link",
    "this page",
}

PUNCTUATION = re.compile(r"[^\w\s]")


def normalise(text: str) -> str:
    return " ".join(PUNCTUATION.sub(" ", text.lower()).split())


def check_link(page_url: str, link: Link) -> FindingData | None:
    if not link.text:
        return FindingData(
            page_url=page_url,
            rule="empty-link-text",
            severity="high",
            element=describe_link(link),
            evidence="Link has no text, aria-label or image alt",
            suggested_fix="Add link text, or an aria-label, that says where the link goes.",
        )

    if normalise(link.text) in VAGUE_TEXTS:
        return FindingData(
            page_url=page_url,
            rule="vague-link-text",
            severity="low",
            element=describe_link(link),
            evidence=f'Link text is "{link.text}"',
            suggested_fix="Use text that says where the link goes, e.g. the name of the page.",
        )

    return None


def check_link_text(page: PageData) -> list[FindingData]:
    findings = (check_link(page.url, link) for link in page.links)
    return [finding for finding in findings if finding is not None]
