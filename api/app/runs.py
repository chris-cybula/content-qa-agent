# Runs
# Saves crawl runs with their pages and findings, and reads runs back with their totals.

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.budget import MICRODOLLARS_PER_DOLLAR
from app.checks.finding import FindingData
from app.findings import finding_row
from app.pages import PageData
from app.tables import ClaudeCall, Finding, Page, Run, utc_now


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int

    sitemap_url: str

    status: str

    error: str | None

    started_at: datetime

    finished_at: datetime | None

    page_count: int

    failed_count: int

    finding_count: int = 0

    open_count: int = 0

    claude_calls: int = 0

    cost_usd: Decimal = Decimal(0)

    ai_duration_ms: int = 0


class PageSummary(BaseModel):
    id: int

    url: str

    status_code: int | None

    error: str | None

    title: str | None

    word_count: int

    finding_count: int

    open_count: int


class RunDetail(RunSummary):
    pages: list[PageData]


async def start_run(session: AsyncSession, sitemap_url: str) -> Run:
    run = Run(sitemap_url=sitemap_url, status="running")
    session.add(run)
    await session.commit()
    return run


async def fail_run(session: AsyncSession, run: Run, error: str) -> None:
    run.status = "failed"
    run.error = error
    run.finished_at = utc_now()
    await session.commit()


def page_row(run_id: int, position: int, data: PageData) -> Page:
    dumped = data.model_dump(mode="json")
    return Page(
        run_id=run_id,
        position=position,
        url=data.url,
        final_url=data.final_url,
        status_code=data.status_code,
        error=data.error,
        title=data.title,
        meta_description=data.meta_description,
        lang=data.lang,
        word_count=data.word_count,
        text=data.text,
        headings=dumped["headings"],
        links=dumped["links"],
        images=dumped["images"],
    )


async def finish_run(
    session: AsyncSession,
    run: Run,
    pages: list[PageData],
    findings: list[FindingData] | None = None,
) -> None:
    rows = [page_row(run.id, position, page) for position, page in enumerate(pages)]

    rows_by_url = {row.url: row for row in rows}
    for finding in findings or []:
        rows_by_url[finding.page_url].findings.append(finding_row(run.id, finding))

    session.add_all(rows)
    run.page_count = len(pages)
    run.failed_count = sum(1 for page in pages if page.error)
    run.status = "finished"
    run.finished_at = utc_now()
    await session.commit()


async def list_runs(session: AsyncSession, limit: int = 50) -> list[Run]:
    result = await session.scalars(select(Run).order_by(Run.id.desc()).limit(limit))
    return list(result)


async def run_totals(
    session: AsyncSession, run_ids: list[int]
) -> dict[int, dict[str, int | Decimal]]:
    totals: dict[int, dict[str, int | Decimal]] = {run_id: {} for run_id in run_ids}

    open_findings = func.sum(case((Finding.status == "open", 1), else_=0))
    finding_rows = await session.execute(
        select(Finding.run_id, func.count(), open_findings)
        .where(Finding.run_id.in_(run_ids))
        .group_by(Finding.run_id)
    )
    for run_id, count, still_open in finding_rows:
        totals[run_id].update(finding_count=count, open_count=int(still_open or 0))

    call_rows = await session.execute(
        select(
            ClaudeCall.run_id,
            func.count(),
            func.coalesce(func.sum(ClaudeCall.cost_microdollars), 0),
            func.coalesce(func.sum(ClaudeCall.duration_ms), 0),
        )
        .where(ClaudeCall.run_id.in_(run_ids))
        .group_by(ClaudeCall.run_id)
    )
    for run_id, calls, cost, waited in call_rows:
        if run_id is None:
            continue
        totals[run_id].update(
            claude_calls=calls,
            cost_usd=Decimal(int(cost)) / MICRODOLLARS_PER_DOLLAR,
            ai_duration_ms=int(waited or 0),
        )
    return totals


async def list_run_summaries(session: AsyncSession, limit: int = 50) -> list[RunSummary]:
    runs = await list_runs(session, limit)
    totals = await run_totals(session, [run.id for run in runs])
    return [RunSummary.model_validate(run).model_copy(update=totals[run.id]) for run in runs]


async def get_run_detail(session: AsyncSession, run_id: int) -> RunDetail | None:
    run = await session.scalar(select(Run).where(Run.id == run_id).options(selectinload(Run.pages)))
    if run is None:
        return None
    totals = await run_totals(session, [run.id])
    return RunDetail.model_validate(run, from_attributes=True).model_copy(update=totals[run.id])


async def list_page_summaries(session: AsyncSession, run_id: int) -> list[PageSummary] | None:
    if await session.get(Run, run_id) is None:
        return None

    open_findings = func.coalesce(func.sum(case((Finding.status == "open", 1), else_=0)), 0)
    rows = await session.execute(
        select(Page, func.count(Finding.id), open_findings)
        .outerjoin(Finding, Finding.page_id == Page.id)
        .where(Page.run_id == run_id)
        .group_by(Page.id)
        .order_by(Page.position)
    )
    return [
        PageSummary(
            id=page.id,
            url=page.url,
            status_code=page.status_code,
            error=page.error,
            title=page.title,
            word_count=page.word_count,
            finding_count=count,
            open_count=int(still_open),
        )
        for page, count, still_open in rows
    ]


async def get_saved_page(session: AsyncSession, run_id: int, page_id: int) -> PageData | None:
    page = await session.scalar(select(Page).where(Page.id == page_id, Page.run_id == run_id))
    if page is None:
        return None
    return PageData.model_validate(page, from_attributes=True)
