# Tests: heading check

from app.checks.headings import check_headings
from app.pages import Heading, PageData

PAGE_URL = "https://shop.test/"


def page_with(*levels_and_texts: tuple[int, str]) -> PageData:
    headings = [Heading(level=level, text=text) for level, text in levels_and_texts]
    return PageData(url=PAGE_URL, status_code=200, headings=headings)


def test_well_ordered_headings_pass() -> None:
    page = page_with((1, "Shop"), (2, "Shapes"), (3, "Circles"), (2, "Delivery"))

    assert check_headings(page) == []


def test_skipped_level_is_flagged_on_the_heading_that_skips() -> None:
    findings = check_headings(
        page_with((1, "About Little Shapes"), (3, "Our workshop"), (2, "Our materials"))
    )

    assert len(findings) == 1
    finding = findings[0]
    assert (finding.rule, finding.severity) == ("heading-order", "medium")
    assert finding.element == 'h3 "Our workshop"'
    assert finding.evidence == "h1 is followed directly by h3, skipping h2"


def test_skipping_several_levels_names_each_one() -> None:
    findings = check_headings(page_with((1, "Shop"), (2, "Shapes"), (5, "Tiny")))

    assert findings[0].evidence == "h2 is followed directly by h5, skipping h3, h4"


def test_going_back_up_any_number_of_levels_is_fine() -> None:
    page = page_with((1, "Shop"), (2, "A"), (3, "B"), (4, "C"), (2, "D"))

    assert check_headings(page) == []


def test_every_extra_h1_is_flagged() -> None:
    findings = check_headings(
        page_with((1, "FAQ"), (2, "Ages"), (1, "Stuff"), (2, "Shipping"), (1, "More"))
    )

    assert [(finding.rule, finding.element) for finding in findings] == [
        ("multiple-h1", 'h1 "Stuff"'),
        ("multiple-h1", 'h1 "More"'),
    ]
    assert findings[0].evidence == "Page has 3 h1 headings"


def test_page_without_an_h1_is_flagged_once() -> None:
    findings = check_headings(page_with((2, "Shapes"), (3, "Circles")))

    assert [(finding.rule, finding.evidence) for finding in findings] == [
        ("missing-h1", "Headings start at h2")
    ]


def test_page_with_no_headings_at_all_is_missing_an_h1() -> None:
    findings = check_headings(page_with())

    assert [(finding.rule, finding.evidence) for finding in findings] == [
        ("missing-h1", "No h1 heading")
    ]


def test_long_heading_text_is_shortened_in_the_element() -> None:
    findings = check_headings(page_with((1, "Shop"), (3, "x" * 100)))

    assert len(findings[0].element) < 70
    assert findings[0].element.endswith('…"')
