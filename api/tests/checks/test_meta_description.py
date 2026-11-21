# Tests: meta description check

import pytest

from app.checks.meta_description import MAX_LENGTH, MIN_LENGTH, check_meta_description
from app.pages import PageData

PAGE_URL = "https://shop.test/contact.html"


def page_with(description: str | None) -> PageData:
    return PageData(url=PAGE_URL, status_code=200, meta_description=description)


def rules(description: str | None) -> list[tuple[str, str]]:
    return [
        (finding.rule, finding.severity)
        for finding in check_meta_description(page_with(description))
    ]


def test_missing_meta_description_is_flagged() -> None:
    findings = check_meta_description(page_with(None))

    assert [(finding.rule, finding.severity) for finding in findings] == [
        ("missing-meta-description", "medium")
    ]
    assert findings[0].evidence == "No meta description"
    assert findings[0].element == "meta description"


def test_empty_meta_description_counts_as_missing() -> None:
    findings = check_meta_description(page_with(""))

    assert findings[0].rule == "missing-meta-description"
    assert findings[0].evidence == "Meta description is empty"


def test_short_meta_description_is_flagged_with_its_length() -> None:
    findings = check_meta_description(page_with("Contact us."))

    assert [(finding.rule, finding.severity) for finding in findings] == [("meta-too-short", "low")]
    assert findings[0].evidence == '"Contact us." is 11 characters (minimum 50)'


def test_long_meta_description_is_flagged_with_its_length() -> None:
    findings = check_meta_description(page_with("a" * 191))

    assert [(finding.rule, finding.severity) for finding in findings] == [("meta-too-long", "low")]
    assert findings[0].evidence.startswith("191 characters (maximum 160)")
    assert len(findings[0].evidence) < 120


@pytest.mark.parametrize("length", [MIN_LENGTH, 100, MAX_LENGTH])
def test_lengths_inside_the_range_pass(length: int) -> None:
    assert rules("a" * length) == []


@pytest.mark.parametrize(
    ("length", "rule"),
    [(MIN_LENGTH - 1, "meta-too-short"), (MAX_LENGTH + 1, "meta-too-long")],
)
def test_one_character_outside_the_range_is_flagged(length: int, rule: str) -> None:
    assert [found for found, _ in rules("a" * length)] == [rule]
