# Report export
# Exports a run's findings as a CSV file or a standalone HTML page. CSV cells that a spreadsheet would read as a
# formula get a leading apostrophe, because evidence and fixes can quote text from the crawled website.

import csv
import html
import io
from typing import Literal

from app.findings import SavedFinding
from app.runs import RunSummary

ReportFormat = Literal["csv", "html"]

COLUMNS = ("page_url", "rule", "severity", "element", "evidence", "suggested_fix", "status")

HEADINGS = ("Page", "Rule", "Severity", "Element", "Evidence", "Suggested fix", "Status")

FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r", "＝", "＋", "－", "＠")

STYLES = """
.report { margin: 0; padding: 2rem 1rem; background: #f7f6f2; color: #1c1c1a;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; font-size: 1rem; line-height: 1.5; }
.report__title { margin: 0 0 0.25rem; font-size: 1.75rem; }
.report__meta { margin: 0 0 1.5rem; color: #5c5b55; font-size: 0.875rem; }
.report__table { width: 100%; border-collapse: collapse; background: #ffffff; font-size: 0.875rem; }
.report__heading { padding: 0.5rem; border-bottom: 2px solid #e2dfd6; text-align: left; }
.report__cell { padding: 0.5rem; border-bottom: 1px solid #e2dfd6; vertical-align: top; }
.report__cell--high { color: #b42318; font-weight: 600; }
.report__cell--medium { color: #8a5a00; font-weight: 600; }
.report__empty { color: #5c5b55; }
"""


def report_filename(run_id: int, report_format: ReportFormat) -> str:
    return f"run-{run_id}-findings.{report_format}"


def spreadsheet_safe(value: str) -> str:
    return f"'{value}" if value.startswith(FORMULA_STARTS) else value


def findings_csv(findings: list[SavedFinding]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS)
    for finding in findings:
        writer.writerow([spreadsheet_safe(str(getattr(finding, column))) for column in COLUMNS])
    return buffer.getvalue()


def findings_html(run: RunSummary, findings: list[SavedFinding]) -> str:
    heading_cells = "".join(f'<th class="report__heading">{heading}</th>' for heading in HEADINGS)
    rows = "".join(finding_row_html(finding) for finding in findings)
    if not findings:
        rows = f'<tr><td class="report__cell report__empty" colspan="{len(COLUMNS)}">No findings.</td></tr>'

    started = run.started_at.strftime("%d %b %Y %H:%M UTC")
    meta = f"{html.escape(run.sitemap_url)} · {started} · {run.page_count} pages · {len(findings)} findings"
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Content QA report: run {run.id}</title><style>{STYLES}</style></head>"
        f'<body class="report"><h1 class="report__title">Content QA report: run {run.id}</h1>'
        f'<p class="report__meta">{meta}</p>'
        f'<table class="report__table"><thead><tr>{heading_cells}</tr></thead><tbody>{rows}</tbody></table>'
        "</body></html>"
    )


def finding_row_html(finding: SavedFinding) -> str:
    cells = []
    for column in COLUMNS:
        value = html.escape(str(getattr(finding, column)))
        modifier = (
            f" report__cell--{finding.severity}"
            if column == "severity" and finding.severity != "low"
            else ""
        )
        cells.append(f'<td class="report__cell{modifier}">{value}</td>')
    return f"<tr>{''.join(cells)}</tr>"
