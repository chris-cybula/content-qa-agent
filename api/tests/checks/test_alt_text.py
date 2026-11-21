# Tests: alt text check

import pytest

from app.checks.alt_text import check_alt_text, looks_like_file_name
from app.pages import Image, PageData

PAGE_URL = "https://shop.test/"


def page_with(*images: Image) -> PageData:
    return PageData(url=PAGE_URL, status_code=200, images=list(images))


def test_image_without_alt_is_flagged() -> None:
    findings = check_alt_text(
        page_with(Image(src="https://shop.test/images/orange-squares.png", alt=None))
    )

    assert len(findings) == 1
    finding = findings[0]
    assert (finding.rule, finding.severity) == ("missing-alt", "high")
    assert finding.element == "img orange-squares.png"
    assert finding.evidence == "No alt attribute"
    assert finding.page_url == PAGE_URL


def test_empty_alt_marks_a_decorative_image_and_is_fine() -> None:
    assert check_alt_text(page_with(Image(src="https://shop.test/line.png", alt=""))) == []


def test_descriptive_alt_is_fine() -> None:
    assert (
        check_alt_text(
            page_with(Image(src="https://shop.test/red-circle.png", alt="Red wooden circle"))
        )
        == []
    )


def test_alt_that_is_a_file_name_is_flagged() -> None:
    findings = check_alt_text(
        page_with(Image(src="https://shop.test/purple-stripes.png", alt="IMG_0042.png"))
    )

    assert [(finding.rule, finding.severity) for finding in findings] == [
        ("alt-is-filename", "medium")
    ]
    assert findings[0].evidence == 'Alt is "IMG_0042.png"'


@pytest.mark.parametrize(
    ("alt", "src"),
    [
        ("IMG_0042.png", "https://shop.test/a.png"),
        ("hero.JPG", "https://shop.test/a.png"),
        ("DSC01234", "https://shop.test/a.png"),
        ("PXL_20241102_093000", "https://shop.test/a.png"),
        ("purple-stripes", "https://shop.test/images/purple-stripes.png"),
        ("Hero_Banner", "https://shop.test/hero_banner.webp"),
    ],
)
def test_recognises_file_names(alt: str, src: str) -> None:
    assert looks_like_file_name(alt, src)


@pytest.mark.parametrize(
    ("alt", "src"),
    [
        ("Logo", "https://shop.test/logo.png"),
        ("Red wooden circle", "https://shop.test/red-circle.png"),
        ("Image of a blue square", "https://shop.test/a.png"),
        ("Version 2.png release notes", "https://shop.test/a.png"),
    ],
)
def test_real_descriptions_are_not_file_names(alt: str, src: str) -> None:
    assert not looks_like_file_name(alt, src)


def test_every_bad_image_gets_its_own_finding_in_page_order() -> None:
    findings = check_alt_text(
        page_with(
            Image(src="https://shop.test/a.png", alt=None),
            Image(src="https://shop.test/b.png", alt="Fine"),
            Image(src="https://shop.test/c.png", alt="c.png"),
        )
    )

    assert [(finding.rule, finding.element) for finding in findings] == [
        ("missing-alt", "img a.png"),
        ("alt-is-filename", "img c.png"),
    ]
