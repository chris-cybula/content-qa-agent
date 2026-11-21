# Claude key check
# Sends one tiny paid Claude call through the budget guard to prove the key in api/.env works.

import asyncio
import sys

import anthropic
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.budget import BudgetExceededError, format_dollars, real_cost
from app.claude import DEFAULT_MODEL, get_guarded_claude
from app.config import get_settings
from app.db import get_engine
from app.tracing import shutdown_tracing

CHECK_FAILURES = (
    BudgetExceededError,
    OperationalError,
    ConnectionRefusedError,
    OSError,
    ProgrammingError,
    anthropic.APIStatusError,
    anthropic.APIConnectionError,
)

FIXED_MESSAGES: list[tuple[type[Exception] | tuple[type[Exception], ...], str]] = [
    (
        (OperationalError, ConnectionRefusedError, OSError),
        "The database isn't reachable, and every Claude call is logged there. Try: docker compose up -d",
    ),
    (ProgrammingError, "The claude_calls table is missing. Run: alembic upgrade head"),
    (
        anthropic.AuthenticationError,
        "The key was rejected. Check it was copied in full into api/.env.",
    ),
    (anthropic.PermissionDeniedError, "The key is valid but not allowed to use this model."),
    (anthropic.RateLimitError, "Rate limited. Wait a minute and try again."),
    (
        anthropic.APIConnectionError,
        "Could not reach the Claude API. Check your internet connection.",
    ),
]


def describe_failure(error: Exception) -> str:
    if isinstance(error, BudgetExceededError):
        return str(error)
    for kinds, message in FIXED_MESSAGES:
        if isinstance(error, kinds):
            return message
    if isinstance(error, anthropic.BadRequestError):
        return f"Request rejected (often a missing credit balance): {error.message}"
    if isinstance(error, anthropic.APIStatusError) and error.status_code == 402:
        return "Out of credit or over your spend limit. Top up or raise the limit in the Console."
    if isinstance(error, anthropic.APIStatusError):
        return f"Claude API error {error.status_code}: {error.message}"
    return str(error)


async def check(database_url: str) -> int:
    try:
        claude = get_guarded_claude(get_settings())
    except RuntimeError as error:
        print(error)
        return 1

    try:
        response = await claude.create(
            purpose="key-check",
            messages=[{"role": "user", "content": "Reply with exactly: Claude API key works."}],
            max_tokens=1024,
            effort="low",
        )
        spent = await claude.spent_this_month()
    except CHECK_FAILURES as error:
        print(describe_failure(error))
        return 1
    finally:
        await claude.aclose()
        await get_engine(database_url).dispose()
        shutdown_tracing()

    reply = "".join(block.text for block in response.content if block.type == "text")
    print(f"Reply: {reply}")

    usage = response.usage
    print(f"Model: {response.model}")
    print(f"Tokens: {usage.input_tokens} in, {usage.output_tokens} out")
    print(f"Cost: {format_dollars(real_cost(DEFAULT_MODEL, usage))}")
    print(f"This month: {format_dollars(spent)} of {format_dollars(claude.monthly_limit)} budget")
    return 0


def main() -> int:
    try:
        settings = get_settings()
    except ValidationError as error:
        print(f"api/.env has a value that can't be read: {error}")
        return 1
    return asyncio.run(check(settings.database_url))


if __name__ == "__main__":
    sys.exit(main())
