# Tracing
# Sends a span for every Claude call to Langfuse over OpenTelemetry; off when the Langfuse keys are empty.

import base64
import logging
from functools import lru_cache
from typing import Literal

from anthropic import OpenTelemetryOptions
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import Settings

SERVICE_NAME = "content-qa-agent"

EXPORT_TIMEOUT_SECONDS = 10

logger = logging.getLogger(__name__)

_providers: list[TracerProvider] = []


def langfuse_endpoint_and_headers(
    otel_url: str, public_key: str, secret_key: str
) -> tuple[str, dict[str, str]]:
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")
    headers = {"authorization": f"Basic {token}", "x-langfuse-ingestion-version": "4"}
    return f"{otel_url.rstrip('/')}/v1/traces", headers


@lru_cache
def langfuse_tracer_provider(otel_url: str, public_key: str, secret_key: str) -> TracerProvider:
    endpoint, headers = langfuse_endpoint_and_headers(otel_url, public_key, secret_key)
    exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers, timeout=EXPORT_TIMEOUT_SECONDS)

    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    _providers.append(provider)
    logger.info("Tracing Claude calls to %s", otel_url)
    return provider


def tracer_provider(settings: Settings) -> TracerProvider | None:
    secret = settings.langfuse_secret_key.get_secret_value() if settings.langfuse_secret_key else ""
    if not settings.langfuse_public_key or not secret:
        return None
    return langfuse_tracer_provider(
        settings.langfuse_otel_url, settings.langfuse_public_key, secret
    )


def open_telemetry_option(
    provider: TracerProvider | None, record_content: bool
) -> OpenTelemetryOptions | Literal[False]:
    if provider is None:
        return False
    content_mode: Literal["content", "metadata_only"] = (
        "content" if record_content else "metadata_only"
    )
    return {"tracer_provider": provider, "traces": {"content_mode": content_mode}}


def shutdown_tracing() -> None:
    for provider in _providers:
        provider.shutdown()
    _providers.clear()
    langfuse_tracer_provider.cache_clear()
