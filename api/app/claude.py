# Guarded Claude client
# The only way the app calls Claude: one call at a time, rate-limited, budget-checked and logged.

import asyncio
import logging
import time
from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Any, Literal, Protocol

import anthropic
from anthropic.types import Message, MessageParam
from fastapi import Depends
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Tracer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.budget import (
    PRICES,
    BudgetExceededError,
    charge_call,
    estimate_input_tokens,
    format_dollars,
    keep_reservation,
    month_spend,
    real_cost,
    release_call,
    reserve_call,
    to_microdollars,
    uses_prompt_cache,
    worst_case_cost,
)
from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.tables import ClaudeCall
from app.tracing import open_telemetry_option, tracer_provider

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-5-5"

Effort = Literal["low", "medium"]
ALLOWED_EFFORTS: tuple[str, ...] = ("low", "medium")


class AiClient(Protocol):
    tracer: Tracer

    async def create(
        self,
        *,
        purpose: str,
        messages: list[MessageParam],
        max_tokens: int,
        system: str | list[dict[str, Any]] | None = None,
        effort: Effort = "low",
        output_format: dict[str, Any] | None = None,
        model: str = DEFAULT_MODEL,
        run_id: int | None = None,
    ) -> Message: ...

    async def aclose(self) -> None: ...


def cached_system(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def call_summary(call: ClaudeCall) -> str:
    where = f"{call.purpose} (run {call.run_id})" if call.run_id is not None else call.purpose
    took = f"{call.duration_ms or 0:,} ms"
    cost = format_dollars(call.cost_microdollars)

    if call.status == "failed":
        return f"Claude {where} failed after {took}, not charged: {call.error}"
    if call.status == "unknown":
        return f"Claude {where} got no answer after {took}, {cost} worst case kept: {call.error}"
    return (
        f"Claude {where} answered in {took}: {call.input_tokens or 0:,} in, {call.cache_read_tokens or 0:,} cache read, "
        f"{call.cache_write_tokens or 0:,} cache write, {call.output_tokens or 0:,} out, {cost}"
    )


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def api_error_text(error: anthropic.APIStatusError) -> str:
    body = error.body
    details = body.get("error", body) if isinstance(body, dict) else None
    message = details.get("message") if isinstance(details, dict) else None
    return f"HTTP {error.status_code}: {message or error.message}"


class GuardedClaude:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        sessions: async_sessionmaker[AsyncSession],
        monthly_budget_usd: Decimal,
        max_tokens_limit: int,
        tracer: Tracer | None = None,
        requests_per_minute: int | None = None,
    ) -> None:
        self._client = client
        self._sessions = sessions
        self.tracer: Tracer = tracer or trace.NoOpTracer()
        self._limit = to_microdollars(monthly_budget_usd)
        self._max_tokens_limit = max_tokens_limit
        self._lock = asyncio.Lock()
        self._min_interval = 60 / requests_per_minute if requests_per_minute else 0.0
        self._last_start: float | None = None

    async def wait_for_rate_limit(self) -> None:
        if self._last_start is None or self._min_interval == 0:
            return
        while (wait := self._last_start + self._min_interval - time.perf_counter()) > 0:  # noqa: ASYNC110
            await asyncio.sleep(wait)

    @property
    def monthly_limit(self) -> int:
        return self._limit

    async def create(
        self,
        *,
        purpose: str,
        messages: list[MessageParam],
        max_tokens: int,
        system: str | list[dict[str, Any]] | None = None,
        effort: Effort = "low",
        output_format: dict[str, Any] | None = None,
        model: str = DEFAULT_MODEL,
        run_id: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        if model not in PRICES:
            raise ValueError(
                f"No price for {model} in app/budget.py, so the budget guard can't allow it"
            )
        if not 1 <= max_tokens <= self._max_tokens_limit:
            raise ValueError(
                f"max_tokens must be between 1 and {self._max_tokens_limit}, got {max_tokens}"
            )
        if effort not in ALLOWED_EFFORTS:
            raise ValueError(f"effort must be low or medium, got {effort}")

        output_config: dict[str, Any] = {"effort": effort}
        if output_format is not None:
            output_config["format"] = output_format
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "output_config": output_config,
        }
        if system is not None:
            request["system"] = system
        if tools is not None:
            request["tools"] = tools

        worst_case = worst_case_cost(
            model, estimate_input_tokens(request), max_tokens, uses_prompt_cache(request)
        )

        async with self._lock, self._sessions() as session:
            await self.wait_for_rate_limit()

            try:
                call = await reserve_call(
                    session,
                    model=model,
                    purpose=purpose,
                    max_tokens=max_tokens,
                    worst_case=worst_case,
                    limit=self._limit,
                    run_id=run_id,
                )
            except BudgetExceededError as error:
                logger.warning("Claude %s not sent: %s", purpose, error)
                raise

            started = time.perf_counter()
            self._last_start = time.perf_counter()
            try:
                response: Message = await self._client.messages.create(**request)
            except anthropic.APIStatusError as error:
                await release_call(session, call, api_error_text(error), elapsed_ms(started))
                logger.warning(call_summary(call))
                raise
            except anthropic.APIConnectionError as error:
                await keep_reservation(
                    session, call, f"{type(error).__name__}: {error}", elapsed_ms(started)
                )
                logger.warning(call_summary(call))
                raise

            await charge_call(
                session, call, response, real_cost(model, response.usage), elapsed_ms(started)
            )
            logger.info(call_summary(call))
        return response

    async def spent_this_month(self) -> int:
        async with self._sessions() as session:
            return await month_spend(session)

    async def aclose(self) -> None:
        await self._client.close()


@lru_cache
def build_guarded_claude(
    api_key: str,
    api_url: str,
    database_url: str,
    monthly_budget_usd: Decimal,
    max_tokens_limit: int,
    timeout_seconds: float,
    provider: TracerProvider | None = None,
    record_content: bool = True,
    requests_per_minute: int | None = None,
) -> GuardedClaude:
    client = anthropic.AsyncAnthropic(
        api_key=api_key,
        base_url=api_url,
        max_retries=0,
        timeout=timeout_seconds,
        open_telemetry=open_telemetry_option(provider, record_content),
    )
    tracer = provider.get_tracer(__name__) if provider is not None else None
    return GuardedClaude(
        client,
        get_sessionmaker(database_url),
        monthly_budget_usd,
        max_tokens_limit,
        tracer,
        requests_per_minute,
    )


def get_guarded_claude(settings: Annotated[Settings, Depends(get_settings)]) -> GuardedClaude:
    if settings.anthropic_api_key is None or not settings.anthropic_api_key.get_secret_value():
        raise RuntimeError(
            "ANTHROPIC_API_KEY is missing. Copy .env.example to .env and paste your key in."
        )
    return build_guarded_claude(
        settings.anthropic_api_key.get_secret_value(),
        settings.claude_api_url,
        settings.database_url,
        settings.claude_monthly_budget_usd,
        settings.claude_max_tokens_limit,
        settings.claude_timeout_seconds,
        tracer_provider(settings),
        settings.langfuse_trace_content,
        settings.claude_requests_per_minute,
    )
