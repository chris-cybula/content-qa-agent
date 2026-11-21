# Claude call log
# Reads a run's logged Claude calls back: tokens, cost and time per call, and the run's totals.

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.budget import MICRODOLLARS_PER_DOLLAR
from app.tables import ClaudeCall, Run


class CallRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int

    purpose: str

    model: str

    status: str

    input_tokens: int | None

    cache_read_tokens: int | None

    cache_write_tokens: int | None

    output_tokens: int | None

    cost_usd: Decimal

    duration_ms: int | None

    error: str | None

    created_at: datetime


class CallTotals(BaseModel):
    calls: int

    input_tokens: int

    cache_read_tokens: int

    cache_write_tokens: int

    output_tokens: int

    cost_usd: Decimal

    duration_ms: int


class RunCalls(BaseModel):
    run_id: int

    totals: CallTotals

    calls: list[CallRecord]


def add_up(calls: list[ClaudeCall]) -> CallTotals:
    cost = sum(call.cost_microdollars for call in calls)
    return CallTotals(
        calls=len(calls),
        input_tokens=sum(call.input_tokens or 0 for call in calls),
        cache_read_tokens=sum(call.cache_read_tokens or 0 for call in calls),
        cache_write_tokens=sum(call.cache_write_tokens or 0 for call in calls),
        output_tokens=sum(call.output_tokens or 0 for call in calls),
        cost_usd=Decimal(cost) / MICRODOLLARS_PER_DOLLAR,
        duration_ms=sum(call.duration_ms or 0 for call in calls),
    )


async def list_run_calls(session: AsyncSession, run_id: int) -> RunCalls | None:
    if await session.get(Run, run_id) is None:
        return None

    rows = list(
        await session.scalars(
            select(ClaudeCall).where(ClaudeCall.run_id == run_id).order_by(ClaudeCall.id)
        )
    )
    return RunCalls(
        run_id=run_id,
        totals=add_up(rows),
        calls=[CallRecord.model_validate(row) for row in rows],
    )
