# Tests: AI findings schema and reading Claude's answer

import json
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from anthropic.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.checks.ai_findings import FINDINGS_FORMAT, AiAnswerError, findings_format, read_findings
from app.claude import GuardedClaude
from tests.fakes import claude_reply, fake_claude

PAGE_URL = "https://shop.test/faq.html"

UNCLEAR_HEADING: dict[str, str] = {
    "rule": "unclear-heading",
    "severity": "low",
    "element": 'h1 "Stuff"',
    "evidence": 'The heading "Stuff" says nothing about the questions below it',
    "suggested_fix": 'Rename it to "Questions about orders and delivery"',
}


def answer(text: str, stop_reason: str = "end_turn", thinking: bool = False) -> Message:
    content: list[dict[str, Any]] = (
        [{"type": "thinking", "thinking": "", "signature": "sig"}] if thinking else []
    )
    content.append({"type": "text", "text": text})
    return Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }
    )


def as_json(*findings: dict[str, str]) -> str:
    return json.dumps({"findings": list(findings)})


def objects_in(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return [schema, *schema.get("$defs", {}).values()]


def test_format_asks_for_json_schema_output() -> None:
    assert FINDINGS_FORMAT["type"] == "json_schema"
    assert FINDINGS_FORMAT["schema"]["required"] == ["findings"]


def test_every_object_is_closed_and_requires_every_field() -> None:
    for schema in objects_in(FINDINGS_FORMAT["schema"]):
        assert schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])


def test_rules_and_severities_are_limited_to_known_values() -> None:
    finding = FINDINGS_FORMAT["schema"]["$defs"]["AiFinding"]["properties"]

    assert finding["rule"]["enum"] == [
        "off-brand-tone",
        "weak-meta-description",
        "unclear-heading",
        "alt-mismatch",
    ]
    assert finding["severity"]["enum"] == ["high", "medium", "low"]


def test_a_check_can_narrow_the_rules_without_changing_the_shared_format() -> None:
    narrowed = findings_format(["unclear-heading", "off-brand-tone"])

    rule = narrowed["schema"]["$defs"]["AiFinding"]["properties"]["rule"]
    assert rule["enum"] == ["off-brand-tone", "unclear-heading"]
    assert len(FINDINGS_FORMAT["schema"]["$defs"]["AiFinding"]["properties"]["rule"]["enum"]) == 4


def test_rules_outside_the_check_are_errors() -> None:
    with pytest.raises(AiAnswerError, match="doesn't cover: unclear-heading"):
        read_findings(answer(as_json(UNCLEAR_HEADING)), PAGE_URL, rules=["off-brand-tone"])


def test_every_field_tells_claude_what_to_write() -> None:
    finding = FINDINGS_FORMAT["schema"]["$defs"]["AiFinding"]["properties"]

    assert all(field["description"] for field in finding.values())


def test_answer_becomes_findings_for_the_page() -> None:
    [finding] = read_findings(answer(as_json(UNCLEAR_HEADING)), PAGE_URL)

    assert finding.page_url == PAGE_URL
    assert finding.model_dump(exclude={"page_url"}) == UNCLEAR_HEADING


def test_empty_list_means_a_clean_page() -> None:
    assert read_findings(answer(as_json()), PAGE_URL) == []


def test_thinking_blocks_are_skipped() -> None:
    findings = read_findings(answer(as_json(UNCLEAR_HEADING), thinking=True), PAGE_URL)

    assert [finding.rule for finding in findings] == ["unclear-heading"]


@pytest.mark.parametrize(
    ("stop_reason", "reason"),
    [("refusal", "declined"), ("max_tokens", "cut off")],
)
def test_declined_or_cut_off_answers_are_errors(stop_reason: str, reason: str) -> None:
    with pytest.raises(AiAnswerError, match=reason):
        read_findings(answer(as_json(), stop_reason=stop_reason), PAGE_URL)


@pytest.mark.parametrize(
    "text",
    [
        "The page looks fine.",
        as_json({**UNCLEAR_HEADING, "rule": "missing-alt"}),
        as_json({**UNCLEAR_HEADING, "severity": "critical"}),
        as_json({key: value for key, value in UNCLEAR_HEADING.items() if key != "suggested_fix"}),
        as_json({**UNCLEAR_HEADING, "confidence": "high"}),
        json.dumps({"issues": [UNCLEAR_HEADING]}),
    ],
    ids=[
        "not-json",
        "code-rule",
        "unknown-severity",
        "missing-field",
        "extra-field",
        "wrong-top-key",
    ],
)
def test_answers_that_break_the_schema_are_errors(text: str) -> None:
    with pytest.raises(AiAnswerError, match="doesn't match the findings schema"):
        read_findings(answer(text), PAGE_URL)


@pytest.mark.anyio
async def test_guarded_call_sends_the_schema_and_reads_the_answer(
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return claude_reply(as_json(UNCLEAR_HEADING))

    guard = GuardedClaude(fake_claude(handler), test_sessions, Decimal("3"), 8192)
    try:
        response = await guard.create(
            purpose="test",
            messages=[{"role": "user", "content": "Check this page."}],
            max_tokens=2000,
            output_format=FINDINGS_FORMAT,
        )
    finally:
        await guard.aclose()

    assert bodies[0]["output_config"] == {"effort": "low", "format": FINDINGS_FORMAT}
    assert [finding.rule for finding in read_findings(response, PAGE_URL)] == ["unclear-heading"]
