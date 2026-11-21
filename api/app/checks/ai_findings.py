# AI findings schema
# The JSON schema Claude must answer in (structured output) and how its answer becomes findings.
# Field descriptions are read by Claude: changing them changes the prompt.

import copy
from collections.abc import Collection
from typing import Any, Literal, get_args

from anthropic import transform_schema
from anthropic.types import Message
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.checks.finding import FindingData, Severity

AiRule = Literal["off-brand-tone", "weak-meta-description", "unclear-heading", "alt-mismatch"]

AI_RULES: tuple[AiRule, ...] = get_args(AiRule)


class AiFinding(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "description": "One problem Claude found; the page isn't asked for, since every call checks one known page."
        },
    )

    rule: AiRule = Field(description="Which rule the problem breaks.")
    severity: Severity = Field(
        description="high if it misleads or excludes readers, medium if it hurts trust or search, low if it is polish."
    )
    element: str = Field(
        description="The part of the page the problem is in, e.g. 'h1 \"Stuff\"', 'meta description' or 'first paragraph'."
    )
    evidence: str = Field(
        description="What exactly is wrong, quoting the page, so an editor can see it without opening the page."
    )
    suggested_fix: str = Field(
        description="What to change, with replacement text an editor could paste in."
    )


class AiFindings(BaseModel):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"description": "Claude's whole answer for one page."}
    )

    findings: list[AiFinding] = Field(
        description="Every problem found on the page; an empty list when the page has none."
    )


FINDINGS_SCHEMA: dict[str, Any] = transform_schema(AiFindings)


def findings_format(rules: Collection[AiRule] = AI_RULES) -> dict[str, Any]:
    schema = copy.deepcopy(FINDINGS_SCHEMA)
    schema["$defs"]["AiFinding"]["properties"]["rule"]["enum"] = [
        rule for rule in AI_RULES if rule in rules
    ]
    return {"type": "json_schema", "schema": schema}


FINDINGS_FORMAT: dict[str, Any] = findings_format()


class AiAnswerError(Exception):
    pass


def read_findings(
    response: Message, page_url: str, rules: Collection[AiRule] = AI_RULES
) -> list[FindingData]:
    if response.stop_reason == "refusal":
        raise AiAnswerError("Claude declined to check this page")
    if response.stop_reason == "max_tokens":
        raise AiAnswerError("Claude's answer was cut off at max_tokens, so its JSON is incomplete")

    text = "".join(block.text for block in response.content if block.type == "text")

    try:
        answer = AiFindings.model_validate_json(text)
    except ValidationError as error:
        raise AiAnswerError(
            f"Claude's answer doesn't match the findings schema: {error}"
        ) from error

    unexpected = sorted({finding.rule for finding in answer.findings} - set(rules))
    if unexpected:
        raise AiAnswerError(f"Claude named rules this check doesn't cover: {', '.join(unexpected)}")

    return [FindingData(page_url=page_url, **finding.model_dump()) for finding in answer.findings]
