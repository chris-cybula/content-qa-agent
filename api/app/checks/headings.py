# Heading check
# One h1 per page, and no skipped levels going down.

from itertools import pairwise

from app.checks.finding import FindingData, shorten
from app.pages import Heading, PageData


def describe_heading(heading: Heading) -> str:
    return f'h{heading.level} "{shorten(heading.text)}"'


def check_heading_order(page: PageData) -> list[FindingData]:
    findings = []
    for previous, heading in pairwise(page.headings):
        if heading.level > previous.level + 1:
            skipped = ", ".join(f"h{level}" for level in range(previous.level + 1, heading.level))
            findings.append(
                FindingData(
                    page_url=page.url,
                    rule="heading-order",
                    severity="medium",
                    element=describe_heading(heading),
                    evidence=f"h{previous.level} is followed directly by h{heading.level}, skipping {skipped}",
                    suggested_fix=f"Change it to h{previous.level + 1}, or add the missing level above it.",
                )
            )
    return findings


def check_h1_count(page: PageData) -> list[FindingData]:
    h1s = [heading for heading in page.headings if heading.level == 1]

    if not h1s:
        return [
            FindingData(
                page_url=page.url,
                rule="missing-h1",
                severity="medium",
                element="page",
                evidence="No h1 heading"
                if not page.headings
                else f"Headings start at h{page.headings[0].level}",
                suggested_fix="Add one h1 that says what the page is about.",
            )
        ]

    return [
        FindingData(
            page_url=page.url,
            rule="multiple-h1",
            severity="medium",
            element=describe_heading(heading),
            evidence=f"Page has {len(h1s)} h1 headings",
            suggested_fix="Keep one h1 for the page's main title and make the others h2.",
        )
        for heading in h1s[1:]
    ]


def check_headings(page: PageData) -> list[FindingData]:
    return check_h1_count(page) + check_heading_order(page)
