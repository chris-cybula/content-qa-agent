# Tests: budget guard
# Pricing, reservations, the monthly limit and one call at a time; no real API calls.

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types import MessageParam, Usage
from anthropic.types.cache_creation import CacheCreation
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.budget import (
    IMAGE_TOKEN_LIMIT,
    BudgetExceededError,
    estimate_input_tokens,
    month_spend,
    real_cost,
    uses_prompt_cache,
    worst_case_cost,
)
from app.claude import DEFAULT_MODEL, GuardedClaude, get_guarded_claude
from app.config import Settings
from app.tables import ClaudeCall, Run
from tests.fakes import (
    ClaudeHandler,
    RealClaudeCallBlockedError,
    claude_error,
    claude_reply,
    fake_claude,
)

pytestmark = pytest.mark.anyio

MODEL = "claude-opus-5-5"

HAIKU = "claude-haiku-5-5"

HELLO: list[MessageParam] = [{"role": "user", "content": "Hello"}]

MakeGuard = Callable[..., GuardedClaude]


@pytest.fixture
async def make_guard(test_sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[MakeGuard]:
    made: list[GuardedClaude] = []

    def make(
        handler: ClaudeHandler, budget: str = "3", max_tokens_limit: int = 8192
    ) -> GuardedClaude:
        guard = GuardedClaude(
            fake_claude(handler), test_sessions, Decimal(budget), max_tokens_limit
        )
        made.append(guard)
        return guard

    yield make
    for guard in made:
        await guard.aclose()


async def saved_calls(sessions: async_sessionmaker[AsyncSession]) -> list[ClaudeCall]:
    async with sessions() as session:
        return list(await session.scalars(select(ClaudeCall).order_by(ClaudeCall.id)))


def logged_call(cost: int, created_at: datetime) -> ClaudeCall:
    return ClaudeCall(
        purpose="earlier",
        model=MODEL,
        status="answered",
        max_tokens=100,
        reserved_microdollars=cost,
        cost_microdollars=cost,
        created_at=created_at,
    )


async def test_worst_case_counts_every_input_token_and_all_of_max_tokens() -> None:
    assert worst_case_cost(MODEL, input_tokens=1_000, max_tokens=500, cached=False) == 14_000


async def test_worst_case_uses_the_cache_write_rate_when_caching_is_asked_for() -> None:
    assert worst_case_cost(MODEL, input_tokens=1_000, max_tokens=500, cached=True) == 18_000


async def test_estimate_counts_bytes_so_it_never_undercounts() -> None:
    text = "żółw " * 100
    request = {"messages": [{"role": "user", "content": text}]}

    assert estimate_input_tokens(request) > len(text.encode("utf-8")) > len(text)


async def test_an_image_counts_its_token_limit_not_its_base64_size() -> None:
    image = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "A" * 1_000_000},
    }
    with_image = {
        "messages": [{"role": "user", "content": [image, {"type": "text", "text": "Alt?"}]}]
    }
    without_image = {
        "messages": [{"role": "user", "content": [None, {"type": "text", "text": "Alt?"}]}]
    }

    assert (
        estimate_input_tokens(with_image)
        == estimate_input_tokens(without_image) + IMAGE_TOKEN_LIMIT
    )


async def test_cache_control_is_found_at_any_depth() -> None:
    cached = {
        "system": [{"type": "text", "text": "Brand guide", "cache_control": {"type": "ephemeral"}}]
    }
    plain = {"system": [{"type": "text", "text": "Brand guide"}], "messages": HELLO}

    assert uses_prompt_cache(cached) is True
    assert uses_prompt_cache(plain) is False


async def test_real_cost_adds_every_kind_of_token() -> None:
    usage = Usage(
        input_tokens=100,
        output_tokens=50,
        cache_read_input_tokens=1_000,
        cache_creation_input_tokens=300,
        cache_creation=CacheCreation(ephemeral_5m_input_tokens=200, ephemeral_1h_input_tokens=100),
    )

    assert real_cost(MODEL, usage) == 400 + 1_000 + 200 + 1_000 + 800


async def test_real_cost_rounds_up_to_whole_microdollars() -> None:
    usage = Usage(input_tokens=0, output_tokens=0, cache_read_input_tokens=1)

    assert real_cost(MODEL, usage) == 1


async def test_haiku_short_prompts_use_its_cheap_prices() -> None:
    usage = Usage(input_tokens=1_000, output_tokens=1_000)

    assert real_cost(HAIKU, usage) == 100 + 500
    assert worst_case_cost(HAIKU, input_tokens=1_000, max_tokens=500, cached=False) == 100 + 250


async def test_haiku_prompts_over_100k_tokens_cost_more_cache_reads_included() -> None:
    usage = Usage(input_tokens=50_000, output_tokens=1_000, cache_read_input_tokens=60_000)

    assert real_cost(HAIKU, usage) == 25_000 + 3_000 + 2_500


async def test_haiku_at_exactly_100k_tokens_still_uses_the_cheap_prices() -> None:
    usage = Usage(input_tokens=100_000, output_tokens=0)

    assert real_cost(HAIKU, usage) == 10_000


async def test_haiku_worst_case_uses_the_dearer_prices_for_a_long_estimate() -> None:
    assert (
        worst_case_cost(HAIKU, input_tokens=200_000, max_tokens=500, cached=False)
        == 100_000 + 1_250
    )


async def test_a_call_sends_the_model_it_asks_for(
    make_guard: MakeGuard, test_sessions: async_sessionmaker[AsyncSession]
) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return claude_reply("Hi")

    await make_guard(handler).create(purpose="test", messages=HELLO, max_tokens=100, model=HAIKU)

    assert bodies[0]["model"] == HAIKU
    async with test_sessions() as session:
        [call] = list(await session.scalars(select(ClaudeCall)))
    assert call.model == HAIKU


async def test_month_spend_counts_only_this_month(
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessions() as session:
        session.add_all(
            [
                logged_call(1_000, datetime(2026, 10, 31, 23, 59, tzinfo=UTC)),
                logged_call(200, datetime(2026, 11, 1, 0, 0, tzinfo=UTC)),
                logged_call(30, datetime(2026, 11, 16, 12, 0, tzinfo=UTC)),
            ]
        )
        await session.commit()

        spent = await month_spend(session, now=datetime(2026, 11, 20, tzinfo=UTC))

    assert spent == 230


async def test_answered_call_is_logged_with_its_real_cost(
    make_guard: MakeGuard,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    guard = make_guard(lambda request: claude_reply("Hi there", input_tokens=100, output_tokens=20))

    response = await guard.create(model=MODEL, purpose="test", messages=HELLO, max_tokens=300)
    [call] = await saved_calls(test_sessions)

    assert response.content[0].type == "text"
    assert response.content[0].text == "Hi there"
    assert call.status == "answered"
    assert call.cost_microdollars == 800
    assert call.cost_usd == Decimal("0.0008")
    assert call.reserved_microdollars > call.cost_microdollars
    assert (call.input_tokens, call.output_tokens) == (100, 20)
    assert call.request_id == "req_test"
    assert call.duration_ms is not None
    assert call.finished_at is not None


async def test_every_call_sends_its_max_tokens_cap_and_effort(make_guard: MakeGuard) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return claude_reply()

    await make_guard(handler).create(purpose="test", messages=HELLO, max_tokens=300)

    assert bodies[0]["model"] == DEFAULT_MODEL == HAIKU
    assert bodies[0]["max_tokens"] == 300
    assert bodies[0]["output_config"] == {"effort": "low"}


def recording_handler(sent: list[httpx2.Request]) -> Callable[[httpx2.Request], httpx2.Response]:

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return claude_reply()

    return handler


@pytest.mark.parametrize(
    ("bad_option", "message"),
    [
        ({"max_tokens": 9_000}, "max_tokens must be between"),
        ({"max_tokens": 0}, "max_tokens must be between"),
        ({"effort": "high"}, "effort must be low or medium"),
        ({"model": "claude-fable-5-2"}, "No price for claude-fable-5-2"),
    ],
)
async def test_calls_outside_the_caps_are_refused_before_sending(
    make_guard: MakeGuard,
    test_sessions: async_sessionmaker[AsyncSession],
    bad_option: dict[str, Any],
    message: str,
) -> None:
    sent: list[httpx2.Request] = []
    guard = make_guard(recording_handler(sent))

    with pytest.raises(ValueError, match=message):
        await guard.create(purpose="test", messages=HELLO, **{"max_tokens": 300, **bad_option})

    assert sent == []
    assert await saved_calls(test_sessions) == []


async def test_call_whose_worst_case_could_pass_the_limit_is_refused_before_sending(
    make_guard: MakeGuard,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    sent: list[httpx2.Request] = []
    guard = make_guard(recording_handler(sent), budget="0.01")

    with pytest.raises(BudgetExceededError):
        await guard.create(model=MODEL, purpose="test", messages=HELLO, max_tokens=1_000)

    assert sent == []
    assert await saved_calls(test_sessions) == []


async def test_earlier_spend_this_month_counts_toward_the_limit(
    make_guard: MakeGuard,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessions() as session:
        session.add(logged_call(2_990_000, datetime.now(UTC)))
        await session.commit()
    guard = make_guard(lambda request: claude_reply())

    with pytest.raises(BudgetExceededError) as refused:
        await guard.create(model=MODEL, purpose="test", messages=HELLO, max_tokens=1_000)

    assert refused.value.spent == 2_990_000
    assert "$3.0000 monthly limit" in str(refused.value)


async def test_api_error_releases_the_reservation(
    make_guard: MakeGuard,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    guard = make_guard(lambda request: claude_error(400, "Your credit balance is too low"))

    with pytest.raises(anthropic.BadRequestError):
        await guard.create(model=MODEL, purpose="test", messages=HELLO, max_tokens=300)
    [call] = await saved_calls(test_sessions)

    assert call.status == "failed"
    assert call.cost_microdollars == 0
    assert call.error == "HTTP 400: Your credit balance is too low"


async def test_timeout_keeps_the_reservation(
    make_guard: MakeGuard,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("Claude took too long", request=request)

    with pytest.raises(anthropic.APITimeoutError):
        await make_guard(handler).create(
            model=MODEL, purpose="test", messages=HELLO, max_tokens=300
        )
    [call] = await saved_calls(test_sessions)

    assert call.status == "unknown"
    assert call.cost_microdollars == call.reserved_microdollars > 0


async def test_kept_reservation_still_counts_toward_the_limit(
    make_guard: MakeGuard,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:

    def timeout(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("Claude took too long", request=request)

    with pytest.raises(anthropic.APITimeoutError):
        await make_guard(timeout).create(
            model=MODEL, purpose="test", messages=HELLO, max_tokens=300
        )
    [first] = await saved_calls(test_sessions)

    budget = str(Decimal(first.reserved_microdollars) * Decimal("1.5") / 1_000_000)
    with pytest.raises(BudgetExceededError):
        await make_guard(lambda request: claude_reply(), budget=budget).create(
            model=MODEL, purpose="test", messages=HELLO, max_tokens=300
        )


async def test_calls_run_one_at_a_time(
    make_guard: MakeGuard, test_sessions: async_sessionmaker[AsyncSession]
) -> None:
    in_flight = 0
    most_at_once = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal in_flight, most_at_once
        in_flight += 1
        most_at_once = max(most_at_once, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return claude_reply()

    guard = make_guard(handler)
    await asyncio.gather(
        *(
            guard.create(model=MODEL, purpose="test", messages=HELLO, max_tokens=300)
            for _ in range(3)
        )
    )

    assert most_at_once == 1
    assert [call.status for call in await saved_calls(test_sessions)] == ["answered"] * 3


async def test_deleting_a_run_keeps_its_claude_spend(
    make_guard: MakeGuard,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessions() as session:
        run = Run(sitemap_url="https://shop.test/sitemap.xml")
        session.add(run)
        await session.commit()
    guard = make_guard(lambda request: claude_reply())
    await guard.create(model=MODEL, purpose="test", messages=HELLO, max_tokens=300, run_id=run.id)
    before = await guard.spent_this_month()

    async with test_sessions() as session:
        await session.delete(await session.get(Run, run.id))
        await session.commit()

    assert len(await saved_calls(test_sessions)) == 1
    assert await guard.spent_this_month() == before > 0


async def test_pytest_cannot_reach_the_real_claude_api() -> None:
    client = anthropic.AsyncAnthropic(
        api_key="test-key", base_url="https://api.anthropic.com", max_retries=0
    )

    with pytest.raises(anthropic.APIConnectionError) as failed:
        await client.messages.create(model=MODEL, max_tokens=10, messages=HELLO)
    await client.close()

    assert isinstance(failed.value.__cause__, RealClaudeCallBlockedError)


async def test_an_answered_call_prints_its_tokens_cost_and_time(
    make_guard: MakeGuard,
    caplog: pytest.LogCaptureFixture,
) -> None:
    guard = make_guard(
        lambda request: claude_reply(input_tokens=379, output_tokens=10, cache_read_tokens=1_601)
    )

    with caplog.at_level(logging.INFO, logger="app.claude"):
        await guard.create(model=MODEL, purpose="page-check", messages=HELLO, max_tokens=300)

    [record] = caplog.records
    assert record.levelno == logging.INFO
    assert record.getMessage().startswith("Claude page-check answered in ")
    assert record.getMessage().endswith(
        ": 379 in, 1,601 cache read, 0 cache write, 10 out, $0.0020"
    )


async def test_a_refused_call_prints_a_warning(
    make_guard: MakeGuard, caplog: pytest.LogCaptureFixture
) -> None:
    guard = make_guard(lambda request: claude_error(529, "Overloaded"))

    with (
        caplog.at_level(logging.INFO, logger="app.claude"),
        pytest.raises(anthropic.APIStatusError),
    ):
        await guard.create(model=MODEL, purpose="vision-check", messages=HELLO, max_tokens=300)

    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert "failed after" in record.getMessage()
    assert record.getMessage().endswith("not charged: HTTP 529: Overloaded")


async def test_a_call_over_the_budget_prints_why_it_wasnt_sent(
    make_guard: MakeGuard,
    caplog: pytest.LogCaptureFixture,
) -> None:
    guard = make_guard(lambda request: claude_reply(), budget="0.01")

    with caplog.at_level(logging.INFO, logger="app.claude"), pytest.raises(BudgetExceededError):
        await guard.create(model=MODEL, purpose="page-check", messages=HELLO, max_tokens=1_000)

    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert record.getMessage().startswith("Claude page-check not sent: Claude budget reached")


async def call_start_gaps(
    test_sessions: async_sessionmaker[AsyncSession], requests_per_minute: int | None
) -> list[float]:
    starts: list[float] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        starts.append(time.perf_counter())
        return claude_reply()

    guard = GuardedClaude(
        fake_claude(handler),
        test_sessions,
        Decimal("3"),
        8192,
        requests_per_minute=requests_per_minute,
    )
    for _ in range(3):
        await guard.create(model=MODEL, purpose="test", messages=HELLO, max_tokens=300)
    await guard.aclose()
    return [later - earlier for earlier, later in pairwise(starts)]


async def test_calls_are_spaced_out_to_stay_under_the_rate_limit(
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    gaps = await call_start_gaps(test_sessions, requests_per_minute=600)

    assert len(gaps) == 2
    assert all(gap >= 0.095 for gap in gaps)


async def test_without_a_rate_limit_calls_are_not_delayed(
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    gaps = await call_start_gaps(test_sessions, requests_per_minute=None)

    assert all(gap < 0.09 for gap in gaps)


async def test_the_rate_limit_setting_reaches_the_guard() -> None:
    guard = get_guarded_claude(Settings(_env_file=None, anthropic_api_key="test-key"))

    assert guard._min_interval == 6.0
