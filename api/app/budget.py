# Budget guard
# Prices each Claude call, reserves its worst case first and refuses calls that would pass the monthly limit.

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from anthropic.types import Message, Usage
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tables import ClaudeCall, utc_now

MICRODOLLARS_PER_DOLLAR = 1_000_000

TOKENS_PER_PRICE_UNIT = 1_000_000

ESTIMATE_OVERHEAD_TOKENS = 2_000

IMAGE_TOKEN_LIMIT = 4_784

LONG_PROMPT_TOKENS = 100_000

BUDGET_LOCK_ID = 4_204_011


@dataclass(frozen=True)
class ModelPrices:
    input: Decimal

    output: Decimal

    cache_write_5m: Decimal

    cache_write_1h: Decimal

    cache_read: Decimal

    long_prompt: "ModelPrices | None" = None


PRICES: dict[str, ModelPrices] = {
    "claude-opus-5-5": ModelPrices(
        input=Decimal("4.00"),
        output=Decimal("20.00"),
        cache_write_5m=Decimal("5.00"),
        cache_write_1h=Decimal("8.00"),
        cache_read=Decimal("0.20"),
    ),
    "claude-haiku-5-5": ModelPrices(
        input=Decimal("0.10"),
        output=Decimal("0.50"),
        cache_write_5m=Decimal("0.125"),
        cache_write_1h=Decimal("0.20"),
        cache_read=Decimal("0.01"),
        long_prompt=ModelPrices(
            input=Decimal("0.50"),
            output=Decimal("2.50"),
            cache_write_5m=Decimal("0.625"),
            cache_write_1h=Decimal("1.00"),
            cache_read=Decimal("0.05"),
        ),
    ),
}


def prices_for(model: str, prompt_tokens: int) -> ModelPrices:
    prices = PRICES[model]
    if prices.long_prompt is not None and prompt_tokens > LONG_PROMPT_TOKENS:
        return prices.long_prompt
    return prices


def to_microdollars(dollars: Decimal) -> int:
    return math.ceil(dollars * MICRODOLLARS_PER_DOLLAR)


def format_dollars(microdollars: int) -> str:
    return f"${Decimal(microdollars) / MICRODOLLARS_PER_DOLLAR:.4f}"


class BudgetExceededError(Exception):
    spent: int

    needed: int

    limit: int

    def __init__(self, spent: int, needed: int, limit: int) -> None:
        super().__init__(
            f"Claude budget reached: {format_dollars(spent)} counted this month plus {format_dollars(needed)} "
            f"worst case for this call would pass the {format_dollars(limit)} monthly limit"
        )
        self.spent = spent
        self.needed = needed
        self.limit = limit


def split_images(value: Any) -> tuple[Any, int]:
    if isinstance(value, dict) and value.get("type") == "image":
        return None, 1
    if isinstance(value, dict):
        parts = {key: split_images(item) for key, item in value.items()}
        return {key: part for key, (part, _) in parts.items()}, sum(
            count for _, count in parts.values()
        )
    if isinstance(value, list):
        pieces = [split_images(item) for item in value]
        return [part for part, _ in pieces], sum(count for _, count in pieces)
    return value, 0


def estimate_input_tokens(request: dict[str, Any]) -> int:
    text_only, image_count = split_images(request)
    size = len(json.dumps(text_only, ensure_ascii=False, default=str).encode("utf-8"))
    return size + image_count * IMAGE_TOKEN_LIMIT + ESTIMATE_OVERHEAD_TOKENS


def uses_prompt_cache(value: Any) -> bool:
    if isinstance(value, dict):
        return "cache_control" in value or any(uses_prompt_cache(item) for item in value.values())
    if isinstance(value, list):
        return any(uses_prompt_cache(item) for item in value)
    return False


def worst_case_cost(model: str, input_tokens: int, max_tokens: int, cached: bool) -> int:
    prices = prices_for(model, input_tokens)
    input_rate = prices.cache_write_1h if cached else prices.input
    return to_microdollars(
        (input_tokens * input_rate + max_tokens * prices.output) / TOKENS_PER_PRICE_UNIT
    )


def real_cost(model: str, usage: Usage) -> int:
    cache_read = usage.cache_read_input_tokens or 0
    cache_write = usage.cache_creation_input_tokens or 0

    prices = prices_for(model, usage.input_tokens + cache_read + cache_write)

    if usage.cache_creation is not None:
        write_5m = usage.cache_creation.ephemeral_5m_input_tokens
        write_1h = usage.cache_creation.ephemeral_1h_input_tokens
    else:
        write_5m, write_1h = 0, cache_write

    dollars = (
        usage.input_tokens * prices.input
        + usage.output_tokens * prices.output
        + cache_read * prices.cache_read
        + write_5m * prices.cache_write_5m
        + write_1h * prices.cache_write_1h
    ) / TOKENS_PER_PRICE_UNIT
    return to_microdollars(dollars)


def month_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def month_spend(session: AsyncSession, now: datetime | None = None) -> int:
    start = month_start(now or utc_now())
    total = await session.scalar(
        select(func.coalesce(func.sum(ClaudeCall.cost_microdollars), 0)).where(
            ClaudeCall.created_at >= start
        )
    )
    return int(total or 0)


async def reserve_call(
    session: AsyncSession,
    *,
    model: str,
    purpose: str,
    max_tokens: int,
    worst_case: int,
    limit: int,
    run_id: int | None = None,
) -> ClaudeCall:
    async with session.begin():
        if session.get_bind().dialect.name == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": BUDGET_LOCK_ID}
            )

        spent = await month_spend(session)
        if spent + worst_case > limit:
            raise BudgetExceededError(spent=spent, needed=worst_case, limit=limit)

        call = ClaudeCall(
            run_id=run_id,
            purpose=purpose,
            model=model,
            status="reserved",
            max_tokens=max_tokens,
            reserved_microdollars=worst_case,
            cost_microdollars=worst_case,
        )
        session.add(call)
    return call


async def charge_call(
    session: AsyncSession, call: ClaudeCall, response: Message, cost: int, duration_ms: int
) -> None:
    usage = response.usage
    call.status = "answered"
    call.cost_microdollars = cost
    call.input_tokens = usage.input_tokens
    call.output_tokens = usage.output_tokens
    call.cache_read_tokens = usage.cache_read_input_tokens or 0
    call.cache_write_tokens = usage.cache_creation_input_tokens or 0
    call.request_id = getattr(response, "_request_id", None)
    call.duration_ms = duration_ms
    call.finished_at = utc_now()
    await session.commit()


async def release_call(
    session: AsyncSession, call: ClaudeCall, error: str, duration_ms: int
) -> None:
    call.status = "failed"
    call.cost_microdollars = 0
    call.error = error
    call.duration_ms = duration_ms
    call.finished_at = utc_now()
    await session.commit()


async def keep_reservation(
    session: AsyncSession, call: ClaudeCall, error: str, duration_ms: int
) -> None:
    call.status = "unknown"
    call.error = error
    call.duration_ms = duration_ms
    call.finished_at = utc_now()
    await session.commit()
