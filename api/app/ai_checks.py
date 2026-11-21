# AI checks runner
# Runs the AI page check on each page of a saved run, then the vision check on each image, and saves findings.

from collections.abc import Collection
from typing import Literal

import anthropic
import httpx
from opentelemetry.util.types import AnyValue
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.budget import BudgetExceededError
from app.checks.ai_findings import AiAnswerError
from app.checks.finding import FindingData
from app.checks.page_check import CURRENT_PROMPT, PAGE_RULES, PromptVersion, check_page
from app.checks.vision_check import VISION_RULES, ImageError, check_image, images_to_check
from app.claude import DEFAULT_MODEL, AiClient, api_error_text
from app.findings import replace_findings
from app.ollama import OllamaError
from app.pages import PageData
from app.tables import Page, Run

AiCheck = Literal["page", "vision"]

ALL_AI_CHECKS: tuple[AiCheck, ...] = ("page", "vision")

SavedPage = tuple[int, PageData]


class PageCheckError(BaseModel):
    page_url: str

    error: str


class AiCheckResult(BaseModel):
    run_id: int

    pages_checked: int = 0

    images_checked: int = 0

    stopped: str | None = None

    errors: list[PageCheckError] = Field(default_factory=list)

    findings: list[FindingData] = Field(default_factory=list)


def call_error_text(error: Exception) -> str:
    if isinstance(error, anthropic.APIStatusError):
        return api_error_text(error)
    return str(error)


def stop(result: AiCheckResult, page_url: str, error: Exception) -> None:
    result.stopped = call_error_text(error)
    result.errors.append(PageCheckError(page_url=page_url, error=result.stopped))


async def run_page_checks(
    claude: AiClient,
    pages: list[SavedPage],
    brand_guide: str | None,
    run_id: int,
    result: AiCheckResult,
    prompt: PromptVersion = CURRENT_PROMPT,
    code_first: bool = True,
    model: str = DEFAULT_MODEL,
) -> dict[int, list[FindingData]]:
    findings_by_page: dict[int, list[FindingData]] = {}
    for page_id, page in pages:
        try:
            span_attributes = {"page.url": page.url, "page_check.prompt": prompt}
            with claude.tracer.start_as_current_span(
                f"page-check {page.url}", attributes=span_attributes
            ):
                findings_by_page[page_id] = await check_page(
                    claude, page, brand_guide, run_id, prompt, code_first, model
                )
        except AiAnswerError as error:
            result.errors.append(PageCheckError(page_url=page.url, error=str(error)))
        except (BudgetExceededError, anthropic.APIError, OllamaError) as error:
            stop(result, page.url, error)
            break
    result.pages_checked = len(findings_by_page)
    return findings_by_page


async def run_vision_checks(
    claude: AiClient,
    client: httpx.AsyncClient,
    pages: list[SavedPage],
    run_id: int,
    result: AiCheckResult,
    code_first: bool = True,
    model: str = DEFAULT_MODEL,
) -> dict[int, list[FindingData]]:
    answers: dict[tuple[str, str | None], list[FindingData] | None] = {}
    findings_by_page: dict[int, list[FindingData]] = {}

    for page_id, page in pages:
        page_findings: list[FindingData] = []
        complete = True
        images = images_to_check(page.images) if code_first else page.images
        for image in images:
            key = (image.src, image.alt)
            if key not in answers:
                try:
                    span_attributes = {"page.url": page.url, "image.src": image.src}
                    with claude.tracer.start_as_current_span(
                        f"vision-check {image.src}", attributes=span_attributes
                    ):
                        answers[key] = await check_image(
                            claude, client, image, page.url, run_id, model
                        )
                except (ImageError, AiAnswerError) as error:
                    answers[key] = None
                    result.errors.append(
                        PageCheckError(page_url=page.url, error=f"Image {image.src}: {error}")
                    )
                except (BudgetExceededError, anthropic.APIError, OllamaError) as error:
                    stop(result, page.url, error)
                    break
            found = answers[key]
            if found is None:
                complete = False
            else:
                page_findings += [
                    finding.model_copy(update={"page_url": page.url}) for finding in found
                ]

        if result.stopped is not None:
            break
        if complete:
            findings_by_page[page_id] = page_findings

    result.images_checked = sum(1 for found in answers.values() if found is not None)
    return findings_by_page


async def run_ai_checks(
    session: AsyncSession,
    claude: AiClient,
    client: httpx.AsyncClient,
    run_id: int,
    brand_guide: str | None,
    checks: Collection[AiCheck] = ALL_AI_CHECKS,
    prompt: PromptVersion = CURRENT_PROMPT,
    code_first: bool = True,
    model: str = DEFAULT_MODEL,
) -> AiCheckResult | None:
    if await session.get(Run, run_id) is None:
        return None

    rows = await session.scalars(
        select(Page).where(Page.run_id == run_id, Page.error.is_(None)).order_by(Page.position)
    )
    pages = [(row.id, PageData.model_validate(row, from_attributes=True)) for row in rows]

    await session.commit()

    result = AiCheckResult(run_id=run_id)

    root_attributes: dict[str, AnyValue] = {
        "run.id": run_id,
        "ai_checks.checks": list(checks),
        "ai_checks.pages": len(pages),
        "ai_checks.prompt": prompt,
        "ai_checks.code_first": code_first,
        "ai_checks.model": model,
    }
    with claude.tracer.start_as_current_span(
        f"ai-checks run {run_id}", attributes=root_attributes
    ) as span:
        if "page" in checks:
            page_findings = await run_page_checks(
                claude, pages, brand_guide, run_id, result, prompt, code_first, model
            )
            await replace_findings(session, run_id, page_findings, PAGE_RULES)
            result.findings += [
                finding for findings in page_findings.values() for finding in findings
            ]

        if "vision" in checks and result.stopped is None:
            image_findings = await run_vision_checks(
                claude, client, pages, run_id, result, code_first, model
            )
            await replace_findings(session, run_id, image_findings, VISION_RULES)
            result.findings += [
                finding for findings in image_findings.values() for finding in findings
            ]

        span.set_attributes(
            {
                "ai_checks.pages_checked": result.pages_checked,
                "ai_checks.images_checked": result.images_checked,
                "ai_checks.findings": len(result.findings),
                "ai_checks.errors": len(result.errors),
            }
        )
        if result.stopped is not None:
            span.set_attribute("ai_checks.stopped", result.stopped)

    return result
