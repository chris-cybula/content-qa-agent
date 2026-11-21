# Findings
# Saves findings, replaces one check's findings on re-runs, records reviews and reads them back.

from collections.abc import Collection
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.finding import FindingData
from app.tables import Finding, Page, Run

FindingStatus = Literal["open", "approved", "rejected"]


class SavedFinding(FindingData):
    id: int

    page_id: int

    status: FindingStatus


def finding_row(run_id: int, data: FindingData) -> Finding:
    return Finding(
        run_id=run_id,
        rule=data.rule,
        severity=data.severity,
        element=data.element,
        evidence=data.evidence,
        suggested_fix=data.suggested_fix,
    )


def saved_finding(finding: Finding, page_url: str) -> SavedFinding:
    return SavedFinding(
        id=finding.id,
        page_id=finding.page_id,
        page_url=page_url,
        rule=finding.rule,
        severity=finding.severity,
        element=finding.element,
        evidence=finding.evidence,
        suggested_fix=finding.suggested_fix,
        status=finding.status,
    )


async def replace_findings(
    session: AsyncSession,
    run_id: int,
    findings_by_page: dict[int, list[FindingData]],
    rules: Collection[str],
) -> None:
    if findings_by_page:
        await session.execute(
            delete(Finding).where(
                Finding.page_id.in_(findings_by_page), Finding.rule.in_(list(rules))
            )
        )

    for page_id, findings in findings_by_page.items():
        for finding in findings:
            row = finding_row(run_id, finding)
            row.page_id = page_id
            session.add(row)
    await session.commit()


async def list_findings(
    session: AsyncSession,
    run_id: int,
    page_id: int | None = None,
    status: FindingStatus | None = None,
) -> list[SavedFinding] | None:
    if await session.get(Run, run_id) is None:
        return None

    query = (
        select(Finding, Page.url)
        .join(Page, Finding.page_id == Page.id)
        .where(Finding.run_id == run_id)
        .order_by(Page.position, Finding.id)
    )
    if page_id is not None:
        query = query.where(Finding.page_id == page_id)
    if status is not None:
        query = query.where(Finding.status == status)

    rows = await session.execute(query)
    return [saved_finding(finding, page_url) for finding, page_url in rows]


async def set_finding_status(
    session: AsyncSession, finding_id: int, status: FindingStatus
) -> SavedFinding | None:
    row = (
        await session.execute(
            select(Finding, Page.url)
            .join(Page, Finding.page_id == Page.id)
            .where(Finding.id == finding_id)
        )
    ).first()
    if row is None:
        return None

    finding, page_url = row
    finding.status = status
    await session.commit()
    return saved_finding(finding, page_url)
