# API entry point
# FastAPI app and every endpoint: crawl, runs, pages, findings, AI checks, chat, proposals, reports.
# Requests from other websites or other host names are refused before they reach an endpoint.

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, HttpUrl
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import RequestResponseEndpoint

from app.agent import ChatRequest, ChatResponse, run_agent
from app.ai_checks import ALL_AI_CHECKS, AiCheck, AiCheckResult, run_ai_checks
from app.calls import RunCalls, list_run_calls
from app.checks import FindingData, run_code_checks
from app.claude import GuardedClaude, get_guarded_claude
from app.config import Settings, get_settings
from app.db import get_session
from app.findings import FindingStatus, SavedFinding, list_findings, set_finding_status
from app.http_client import get_http_client
from app.pages import PageData, fetch_pages
from app.proposals import ProposalStatus, SavedProposal, list_proposals, set_proposal_status
from app.report import ReportFormat, findings_csv, findings_html, report_filename
from app.request_guard import refusal_reason
from app.runs import (
    PageSummary,
    RunDetail,
    RunSummary,
    fail_run,
    finish_run,
    get_run_detail,
    list_page_summaries,
    list_run_summaries,
    start_run,
)
from app.sitemap import SitemapError, read_sitemap
from app.tracing import shutdown_tracing


def show_app_logs() -> None:
    app_logger = logging.getLogger("app")
    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)-9s %(message)s"))
        app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)


show_app_logs()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    shutdown_tracing()


app = FastAPI(title="Content QA Agent API", lifespan=lifespan)

logger = logging.getLogger(__name__)


@app.middleware("http")
async def refuse_other_websites(request: Request, call_next: RequestResponseEndpoint) -> Response:
    reason = refusal_reason(request.headers, get_settings().api_allowed_hosts)
    if reason is not None:
        logger.warning("Refused %s %s: %s", request.method, request.url.path, reason)
        return JSONResponse(status_code=403, content={"detail": reason})
    return await call_next(request)


@app.exception_handler(ConnectionRefusedError)
@app.exception_handler(OperationalError)
async def database_unreachable(_request: Request, _error: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": "The database isn't reachable. Is Postgres running? Try: docker compose up -d"
        },
    )


class HealthResponse(BaseModel):
    status: str


class SitemapPagesResponse(BaseModel):
    sitemap_url: str

    count: int

    max_pages: int

    pages: list[str]


class CrawlRequest(BaseModel):
    sitemap_url: HttpUrl


class CrawlResponse(BaseModel):
    run_id: int

    sitemap_url: str

    count: int

    failed: int

    finding_count: int

    max_pages: int

    pages: list[PageData]

    findings: list[FindingData]


@app.get("/health")
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/sitemap/pages")
async def sitemap_pages(
    sitemap_url: HttpUrl,
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SitemapPagesResponse:
    try:
        pages = await read_sitemap(client, str(sitemap_url), settings.max_pages)
    except SitemapError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return SitemapPagesResponse(
        sitemap_url=str(sitemap_url),
        count=len(pages),
        max_pages=settings.max_pages,
        pages=pages,
    )


@app.post("/crawl")
async def crawl(
    body: CrawlRequest,
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CrawlResponse:
    run = await start_run(session, str(body.sitemap_url))

    try:
        urls = await read_sitemap(client, str(body.sitemap_url), settings.max_pages)
    except SitemapError as error:
        await fail_run(session, run, str(error))
        raise HTTPException(status_code=502, detail=str(error)) from error

    pages = await fetch_pages(client, urls, settings.crawl_concurrency)

    findings = await run_code_checks(
        client, pages, settings.crawl_concurrency, settings.check_external_links
    )

    await finish_run(session, run, pages, findings)

    return CrawlResponse(
        run_id=run.id,
        sitemap_url=str(body.sitemap_url),
        count=run.page_count,
        failed=run.failed_count,
        finding_count=len(findings),
        max_pages=settings.max_pages,
        pages=pages,
        findings=findings,
    )


@app.get("/runs")
async def runs(session: Annotated[AsyncSession, Depends(get_session)]) -> list[RunSummary]:
    return await list_run_summaries(session)


@app.get("/runs/{run_id}")
async def run_detail(
    run_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> RunDetail:
    detail = await get_run_detail(session, run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"No run with id {run_id}")
    return detail


@app.post("/runs/{run_id}/ai-checks")
async def ai_checks(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    claude: Annotated[GuardedClaude, Depends(get_guarded_claude)],
    checks: Annotated[
        list[AiCheck], Query(description="Which AI checks to run; both when left out.")
    ] = list(ALL_AI_CHECKS),  # noqa: B006
) -> AiCheckResult:
    try:
        brand_guide = settings.read_brand_guide()
    except OSError as error:
        raise HTTPException(
            status_code=500, detail=f"Can't read BRAND_GUIDE_FILE: {error}"
        ) from error

    result = await run_ai_checks(session, claude, client, run_id, brand_guide, checks)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No run with id {run_id}")
    return result


@app.get("/runs/{run_id}/calls")
async def run_calls(
    run_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> RunCalls:
    calls = await list_run_calls(session, run_id)
    if calls is None:
        raise HTTPException(status_code=404, detail=f"No run with id {run_id}")
    return calls


@app.get("/runs/{run_id}/findings")
async def run_findings(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    page_id: int | None = None,
    status: FindingStatus | None = None,
) -> list[SavedFinding]:
    findings = await list_findings(session, run_id, page_id, status)
    if findings is None:
        raise HTTPException(status_code=404, detail=f"No run with id {run_id}")
    return findings


@app.get("/runs/{run_id}/pages")
async def run_pages(
    run_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> list[PageSummary]:
    pages = await list_page_summaries(session, run_id)
    if pages is None:
        raise HTTPException(status_code=404, detail=f"No run with id {run_id}")
    return pages


class ReviewRequest(BaseModel):
    status: FindingStatus


@app.patch("/findings/{finding_id}")
async def review_finding(
    finding_id: int,
    body: ReviewRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SavedFinding:
    finding = await set_finding_status(session, finding_id, body.status)
    if finding is None:
        raise HTTPException(status_code=404, detail=f"No finding with id {finding_id}")
    return finding


@app.post("/runs/{run_id}/chat")
async def chat(
    run_id: int,
    body: ChatRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    claude: Annotated[GuardedClaude, Depends(get_guarded_claude)],
) -> ChatResponse:
    result = await run_agent(session, claude, run_id, body.messages)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No run with id {run_id}")
    return result


@app.get("/runs/{run_id}/proposals")
async def run_proposals(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    status: ProposalStatus | None = None,
) -> list[SavedProposal]:
    proposals = await list_proposals(session, run_id, status)
    if proposals is None:
        raise HTTPException(status_code=404, detail=f"No run with id {run_id}")
    return proposals


class ProposalReviewRequest(BaseModel):
    status: ProposalStatus


@app.patch("/proposals/{proposal_id}")
async def review_proposal(
    proposal_id: int,
    body: ProposalReviewRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SavedProposal:
    proposal = await set_proposal_status(session, proposal_id, body.status)
    if proposal is None:
        raise HTTPException(status_code=404, detail=f"No proposal with id {proposal_id}")
    return proposal


@app.get("/runs/{run_id}/report")
async def run_report(
    run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    report_format: Annotated[ReportFormat, Query(alias="format")] = "csv",
    include_rejected: bool = False,
) -> Response:
    detail = await get_run_detail(session, run_id)
    findings = await list_findings(session, run_id)
    if detail is None or findings is None:
        raise HTTPException(status_code=404, detail=f"No run with id {run_id}")

    if not include_rejected:
        findings = [finding for finding in findings if finding.status != "rejected"]

    headers = {
        "Content-Disposition": f'attachment; filename="{report_filename(run_id, report_format)}"'
    }
    if report_format == "html":
        return Response(
            findings_html(detail, findings), media_type="text/html; charset=utf-8", headers=headers
        )
    return Response(findings_csv(findings), media_type="text/csv; charset=utf-8", headers=headers)
