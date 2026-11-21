# Fix proposals
# Saves the agent's proposed fixes as pending, lists them and records a person's approve or reject.

from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tables import Finding, FixProposal, Page, Run, utc_now

ProposalStatus = Literal["pending", "approved", "rejected"]


class SavedProposal(BaseModel):
    id: int

    run_id: int

    finding_id: int

    page_id: int

    page_url: str

    rule: str

    element: str

    evidence: str

    suggested_fix: str

    new_text: str

    reason: str

    status: ProposalStatus

    created_at: datetime


def saved_proposal(proposal: FixProposal, finding: Finding, page_url: str) -> SavedProposal:
    return SavedProposal(
        id=proposal.id,
        run_id=proposal.run_id,
        finding_id=finding.id,
        page_id=finding.page_id,
        page_url=page_url,
        rule=finding.rule,
        element=finding.element,
        evidence=finding.evidence,
        suggested_fix=finding.suggested_fix,
        new_text=proposal.new_text,
        reason=proposal.reason,
        status=proposal.status,
        created_at=proposal.created_at,
    )


async def save_proposal(
    session: AsyncSession, run_id: int, finding_id: int, new_text: str, reason: str
) -> SavedProposal | None:
    row = (
        await session.execute(
            select(Finding, Page.url)
            .join(Page, Finding.page_id == Page.id)
            .where(Finding.id == finding_id, Finding.run_id == run_id)
        )
    ).first()
    if row is None:
        return None

    finding, page_url = row
    proposal = FixProposal(run_id=run_id, finding_id=finding.id, new_text=new_text, reason=reason)
    session.add(proposal)
    await session.commit()
    return saved_proposal(proposal, finding, page_url)


async def list_proposals(
    session: AsyncSession, run_id: int, status: ProposalStatus | None = None
) -> list[SavedProposal] | None:
    if await session.get(Run, run_id) is None:
        return None

    query = (
        select(FixProposal, Finding, Page.url)
        .join(Finding, FixProposal.finding_id == Finding.id)
        .join(Page, Finding.page_id == Page.id)
        .where(FixProposal.run_id == run_id)
        .order_by(FixProposal.id.desc())
    )
    if status is not None:
        query = query.where(FixProposal.status == status)

    rows = await session.execute(query)
    return [saved_proposal(proposal, finding, page_url) for proposal, finding, page_url in rows]


async def set_proposal_status(
    session: AsyncSession, proposal_id: int, status: ProposalStatus
) -> SavedProposal | None:
    row = (
        await session.execute(
            select(FixProposal, Finding, Page.url)
            .join(Finding, FixProposal.finding_id == Finding.id)
            .join(Page, Finding.page_id == Page.id)
            .where(FixProposal.id == proposal_id)
        )
    ).first()
    if row is None:
        return None

    proposal, finding, page_url = row
    proposal.status = status
    proposal.reviewed_at = None if status == "pending" else utc_now()
    await session.commit()
    return saved_proposal(proposal, finding, page_url)
