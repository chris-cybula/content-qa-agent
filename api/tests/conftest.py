# Shared test fixtures
# Test database (SQLite, or Postgres via TEST_DATABASE_URL) and a block on every real Anthropic request.

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import httpx2
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

from app.db import get_session
from app.main import app
from app.tables import Base
from tests.fakes import RealClaudeCallBlockedError

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

CLAUDE_API_DOMAINS = ("anthropic.com", "claude.com")


def is_claude_api(host: str) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in CLAUDE_API_DOMAINS)


def refuse_claude_api(send: Callable[[Any, Any], Any]) -> Callable[[Any, Any], Any]:

    def guarded_send(transport: Any, request: Any) -> Any:
        if is_claude_api(request.url.host):
            raise RealClaudeCallBlockedError(
                f"A test tried to call the real Claude API: {request.url}"
            )
        return send(transport, request)

    return guarded_send


def refuse_claude_api_async(
    send: Callable[[Any, Any], Awaitable[Any]],
) -> Callable[[Any, Any], Awaitable[Any]]:

    async def guarded_send(transport: Any, request: Any) -> Any:
        if is_claude_api(request.url.host):
            raise RealClaudeCallBlockedError(
                f"A test tried to call the real Claude API: {request.url}"
            )
        return await send(transport, request)

    return guarded_send


@pytest.fixture(autouse=True)
def no_real_claude_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("ANTHROPIC_"):
            monkeypatch.delenv(name)

    for module in (httpx, httpx2):
        monkeypatch.setattr(
            module.HTTPTransport,
            "handle_request",
            refuse_claude_api(module.HTTPTransport.handle_request),
        )
        monkeypatch.setattr(
            module.AsyncHTTPTransport,
            "handle_async_request",
            refuse_claude_api_async(module.AsyncHTTPTransport.handle_async_request),
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def test_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    if TEST_DATABASE_URL.startswith("sqlite"):
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=StaticPool)
    else:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def test_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    yield sessions
    app.dependency_overrides.pop(get_session, None)
    await engine.dispose()


@pytest.fixture
async def client(test_sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://localhost") as async_client:
        yield async_client
