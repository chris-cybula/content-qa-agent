# Tests: report export
# Text from a crawled page that a spreadsheet would run as a formula is written to the CSV as plain text.

import csv
import io

import pytest

from app.findings import SavedFinding
from app.report import findings_csv, spreadsheet_safe


@pytest.mark.parametrize(
    "value",
    [
        '=HYPERLINK("https://evil.example","Click")',
        "+1+1",
        "-2+3",
        "@SUM(A1:A2)",
        "\t=1+1",
        "\r=1+1",
        "＝1+1",
    ],
)
def test_formula_text_gets_a_leading_apostrophe(value: str) -> None:
    assert spreadsheet_safe(value) == f"'{value}"


@pytest.mark.parametrize("value", ['Link text is "Click here"', "https://shop.test/", "a = b", ""])
def test_ordinary_text_is_unchanged(value: str) -> None:
    assert spreadsheet_safe(value) == value


def test_csv_report_writes_quoted_page_text_as_plain_text() -> None:
    finding = SavedFinding(
        id=1,
        page_id=1,
        status="open",
        page_url="https://shop.test/",
        rule="tone",
        severity="medium",
        element="p",
        evidence='=HYPERLINK("https://evil.example","Click")',
        suggested_fix="-Rewrite this sentence.",
    )

    header, row = list(csv.reader(io.StringIO(findings_csv([finding]))))

    assert row[header.index("page_url")] == "https://shop.test/"
    assert row[header.index("evidence")] == '\'=HYPERLINK("https://evil.example","Click")'
    assert row[header.index("suggested_fix")] == "'-Rewrite this sentence."
