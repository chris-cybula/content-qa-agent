# Tests: tracing setup
# Langfuse address and sign-in, and when tracing is on or off.

import base64
from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from app.claude import get_guarded_claude
from app.config import Settings
from app.tracing import (
    langfuse_endpoint_and_headers,
    open_telemetry_option,
    shutdown_tracing,
    tracer_provider,
)


@pytest.fixture(autouse=True)
def fresh_providers() -> Iterator[None]:
    shutdown_tracing()
    yield
    shutdown_tracing()


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)


def test_traces_go_to_langfuse_with_basic_auth() -> None:
    endpoint, headers = langfuse_endpoint_and_headers(
        "https://cloud.langfuse.com/api/public/otel/", "pk-lf-1", "sk-lf-2"
    )

    assert endpoint == "https://cloud.langfuse.com/api/public/otel/v1/traces"
    assert headers["authorization"] == "Basic " + base64.b64encode(b"pk-lf-1:sk-lf-2").decode()
    assert headers["x-langfuse-ingestion-version"] == "4"


@pytest.mark.parametrize(
    "keys",
    [
        {},
        {"langfuse_public_key": "pk-lf-1"},
        {"langfuse_secret_key": "sk-lf-2"},
        {"langfuse_public_key": "", "langfuse_secret_key": ""},
    ],
    ids=["none", "public-only", "secret-only", "empty"],
)
def test_tracing_is_off_without_both_keys(keys: dict[str, str]) -> None:
    assert tracer_provider(settings(**keys)) is None


def test_both_keys_give_one_shared_provider() -> None:
    keys = settings(langfuse_public_key="pk-lf-1", langfuse_secret_key="sk-lf-2")

    first = tracer_provider(keys)

    assert isinstance(first, TracerProvider)
    assert tracer_provider(keys) is first


def test_sdk_option_records_content_only_when_asked() -> None:
    provider = TracerProvider()

    assert open_telemetry_option(None, record_content=True) is False
    assert open_telemetry_option(provider, record_content=True) == {
        "tracer_provider": provider,
        "traces": {"content_mode": "content"},
    }
    metadata_only = open_telemetry_option(provider, record_content=False)
    assert metadata_only is not False
    assert metadata_only["traces"] == {"content_mode": "metadata_only"}


def test_without_langfuse_keys_the_guard_traces_nothing() -> None:
    guard = get_guarded_claude(settings(anthropic_api_key="test-key"))

    assert guard._client.open_telemetry["traces"]["enabled"] is False
    assert isinstance(guard.tracer, trace.NoOpTracer)


def test_with_langfuse_keys_the_guard_traces_full_content() -> None:
    keys = settings(
        anthropic_api_key="test-key", langfuse_public_key="pk-lf-1", langfuse_secret_key="sk-lf-2"
    )

    guard = get_guarded_claude(keys)

    option = guard._client.open_telemetry
    assert option["tracer_provider"] is tracer_provider(keys)
    assert (option["traces"]["enabled"], option["traces"]["content_mode"]) == (True, "content")
    assert not isinstance(guard.tracer, trace.NoOpTracer)
