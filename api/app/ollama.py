# Ollama client (evals only)
# Runs the AI checks on a free local model with the same request and answer shape as Claude; logged at $0.

import asyncio
import logging
import time
from typing import Any

import httpx
from anthropic.types import Message, MessageParam
from opentelemetry import trace
from opentelemetry.trace import Tracer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.budget import charge_call, release_call
from app.checks.ai_findings import AiAnswerError
from app.claude import Effort, elapsed_ms
from app.config import Settings
from app.db import get_sessionmaker
from app.tables import ClaudeCall

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_MODEL = "gemma3:4b"

TEMPERATURE = 0


class OllamaError(Exception):
    pass


def inline_refs(schema: Any, defs: dict[str, Any] | None = None) -> Any:
    if defs is None:
        defs = schema.get("$defs", {}) if isinstance(schema, dict) else {}

    if isinstance(schema, dict) and "$ref" in schema:
        name = schema["$ref"].removeprefix("#/$defs/")
        extra = {key: value for key, value in schema.items() if key != "$ref"}
        return {**inline_refs(defs[name], defs), **inline_refs(extra, defs)}

    if isinstance(schema, dict):
        return {key: inline_refs(value, defs) for key, value in schema.items() if key != "$defs"}
    if isinstance(schema, list):
        return [inline_refs(item, defs) for item in schema]
    return schema


def text_and_images(content: str | list[dict[str, Any]] | Any) -> tuple[str, list[str]]:
    if isinstance(content, str):
        return content, []
    texts = [block["text"] for block in content if block.get("type") == "text"]
    images = [block["source"]["data"] for block in content if block.get("type") == "image"]
    return "\n\n".join(texts), images


def ollama_request(
    *,
    model: str,
    messages: list[MessageParam],
    max_tokens: int,
    system: str | list[dict[str, Any]] | None,
    output_format: dict[str, Any] | None,
    context_tokens: int,
) -> dict[str, Any]:
    chat: list[dict[str, Any]] = []

    if system is not None:
        chat.append({"role": "system", "content": text_and_images(system)[0]})

    for message in messages:
        text, images = text_and_images(message["content"])
        entry: dict[str, Any] = {"role": message["role"], "content": text}
        if images:
            entry["images"] = images
        chat.append(entry)

    request: dict[str, Any] = {
        "model": model,
        "messages": chat,
        "stream": False,
        "options": {
            "num_ctx": context_tokens,
            "num_predict": max_tokens,
            "temperature": TEMPERATURE,
        },
    }

    if output_format is not None:
        request["format"] = inline_refs(output_format["schema"])
    return request


def to_message(answer: dict[str, Any], model: str) -> Message:
    stop_reason = "max_tokens" if answer.get("done_reason") == "length" else "end_turn"
    text = (answer.get("message") or {}).get("content", "")
    return Message.model_validate(
        {
            "id": f"ollama-{answer.get('created_at', '')}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": answer.get("prompt_eval_count", 0),
                "output_tokens": answer.get("eval_count", 0),
            },
        }
    )


def error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    message = body.get("error") if isinstance(body, dict) else None
    return f"HTTP {response.status_code}: {message or response.text[:200]}"


class OllamaClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        sessions: async_sessionmaker[AsyncSession],
        base_url: str,
        context_tokens: int,
        tracer: Tracer | None = None,
    ) -> None:
        self._client = client
        self._sessions = sessions
        self._chat_url = f"{base_url.rstrip('/')}/api/chat"
        self._context_tokens = context_tokens
        self.tracer: Tracer = tracer or trace.NoOpTracer()
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        purpose: str,
        messages: list[MessageParam],
        max_tokens: int,
        system: str | list[dict[str, Any]] | None = None,
        effort: Effort = "low",  # noqa: ARG002
        output_format: dict[str, Any] | None = None,
        model: str = DEFAULT_OLLAMA_MODEL,
        run_id: int | None = None,
    ) -> Message:
        request = ollama_request(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            system=system,
            output_format=output_format,
            context_tokens=self._context_tokens,
        )

        async with self._lock, self._sessions() as session:
            call = ClaudeCall(
                run_id=run_id,
                purpose=purpose,
                model=model,
                status="reserved",
                max_tokens=max_tokens,
                reserved_microdollars=0,
                cost_microdollars=0,
            )
            session.add(call)
            await session.commit()

            started = time.perf_counter()
            try:
                response = await self._client.post(self._chat_url, json=request)
            except httpx.HTTPError as error:
                reason = f"Ollama didn't answer at {self._chat_url} ({type(error).__name__}); is it running?"
                await release_call(session, call, reason, elapsed_ms(started))
                logger.warning("Ollama %s failed: %s", purpose, reason)
                raise OllamaError(reason) from error

            if response.status_code >= 400:
                reason = error_text(response)
                await release_call(session, call, reason, elapsed_ms(started))
                logger.warning("Ollama %s failed: %s", purpose, reason)
                if "prediction aborted" in reason:
                    raise AiAnswerError(f"The local model got stuck on this page: {reason}")
                raise OllamaError(reason)

            message = to_message(response.json(), model)
            await charge_call(session, call, message, 0, elapsed_ms(started))
            logger.info(
                "Ollama %s (%s) answered in %s ms: %s in, %s out, $0",
                purpose,
                model,
                f"{call.duration_ms or 0:,}",
                f"{message.usage.input_tokens:,}",
                f"{message.usage.output_tokens:,}",
            )
        return message

    async def aclose(self) -> None:
        await self._client.aclose()


def get_ollama_client(settings: Settings) -> OllamaClient:
    return OllamaClient(
        httpx.AsyncClient(timeout=settings.ollama_timeout_seconds),
        get_sessionmaker(settings.database_url),
        settings.ollama_url,
        settings.ollama_context_tokens,
    )
