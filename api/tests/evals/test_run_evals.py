# Tests: eval runner
# Real eval pages, fake website, fake Claude; nothing is paid for.

import json
from collections.abc import AsyncIterator, Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.checks.page_check import CURRENT_PROMPT, INSTRUCTIONS, PAGE_RULES
from app.claude import GuardedClaude
from app.ollama import OllamaClient
from app.tables import ClaudeCall, Run
from evals.answer_key import load_answer_key
from evals.run_evals import (
    EvalReport,
    evaluate,
    in_scope,
    page_name,
    parse_args,
    passes,
    report_text,
    save_report,
)
from evals.scoring import RuleScore
from tests.fakes import Route, claude_error, claude_reply, fake_claude, fake_client

pytestmark = pytest.mark.anyio

PUBLIC = Path(__file__).resolve().parents[3] / "demo-site" / "public"

SITE = "http://localhost:8080"

TRUE_ALT = {
    "red-circle.png": "A red circle on a cream background",
    "blue-square.png": "A blue square on a cream background",
    "green-triangle.png": "A green triangle on a cream background",
    "purple-stripes.png": "A rectangle with purple and lilac stripes on a cream background",
    "orange-squares.png": "Nine orange squares arranged in a three-by-three grid",
}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".xml": "application/xml",
    ".png": "image/png",
    ".css": "text/css",
}

Answer = Callable[[dict[str, Any]], httpx2.Response]


def finding(rule: str) -> dict[str, str]:
    return {
        "rule": rule,
        "severity": "low",
        "element": "page",
        "evidence": "Seen on the page",
        "suggested_fix": "Fix it",
    }


def is_vision(body: dict[str, Any]) -> bool:
    return isinstance(body["messages"][0]["content"], list)


def between(text: str, tag: str) -> dict[str, Any]:
    return json.loads(text.split(f"<{tag}>\n", 1)[1].rsplit(f"\n</{tag}>", 1)[0])


def perfect_findings(text: str, vision: bool) -> str:
    if vision:
        details = between(text, "image_details")
        wrong = details["alt"] != TRUE_ALT[page_name(details["src"])]
        return json.dumps({"findings": [finding("alt-mismatch")] if wrong else []})

    url = between(text, "page")["url"]
    set_name = url.split("/evals/", 1)[1].split("/", 1)[0]
    expected = load_answer_key()[set_name][page_name(url)]
    return json.dumps(
        {"findings": [finding(rule) for rule in sorted(expected) if rule in PAGE_RULES]}
    )


def perfect_answer(body: dict[str, Any]) -> httpx2.Response:
    if is_vision(body):
        return claude_reply(
            perfect_findings(body["messages"][0]["content"][1]["text"], vision=True)
        )
    return claude_reply(perfect_findings(body["messages"][0]["content"], vision=False))


def perfect_ollama(request: httpx.Request) -> httpx.Response:
    user = json.loads(request.content)["messages"][-1]
    content = perfect_findings(user["content"], vision="images" in user)
    return httpx.Response(
        200,
        json={
            "model": "gemma3:4b",
            "message": {"role": "assistant", "content": content},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 900,
            "eval_count": 40,
        },
    )


def ollama(
    handler: Callable[[httpx.Request], httpx.Response], sessions: async_sessionmaker[AsyncSession]
) -> OllamaClient:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OllamaClient(client, sessions, "http://ollama.test", 8192)


def site_routes() -> dict[str, Route]:
    routes: dict[str, Route] = {}
    for path in PUBLIC.rglob("*"):
        if path.is_file() and path.suffix in CONTENT_TYPES:
            url = f"{SITE}/{path.relative_to(PUBLIC).as_posix()}"
            routes[url] = (200, path.read_bytes(), {"content-type": CONTENT_TYPES[path.suffix]})
    routes[f"{SITE}/"] = routes[f"{SITE}/index.html"]
    return routes


@pytest.fixture
async def demo_site() -> AsyncIterator[httpx.AsyncClient]:
    client = fake_client(site_routes())
    yield client
    await client.aclose()


@pytest.fixture
async def make_claude(
    test_sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[Callable[[Answer], GuardedClaude]]:
    guards: list[GuardedClaude] = []

    def make(answer: Answer) -> GuardedClaude:
        guard = GuardedClaude(
            fake_claude(lambda request: answer(json.loads(request.content))),
            test_sessions,
            Decimal("3"),
            8192,
        )
        guards.append(guard)
        return guard

    yield make
    for guard in guards:
        await guard.aclose()


async def test_code_checks_score_100_percent_on_the_code_rules(
    demo_site: httpx.AsyncClient,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    report = await evaluate(demo_site, test_sessions, None, None, "test")

    assert report.errors == []
    assert (report.total.precision, report.total.recall) == (1.0, 1.0)
    assert {name: score.total.found_wrong for name, score in report.sets.items()} == {
        "clean": 0,
        "seeded": 0,
        "injection": 0,
    }
    assert (report.claude_calls, report.cost_usd, report.ai) == (0, Decimal(0), False)


async def test_without_ai_the_ai_rules_are_left_out_of_the_score(
    demo_site: httpx.AsyncClient,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    report = await evaluate(demo_site, test_sessions, None, None, "test")

    assert not {"off-brand-tone", "weak-meta-description", "unclear-heading", "alt-mismatch"} & set(
        report.rules
    )
    assert in_scope({"a.html": frozenset({"alt-mismatch", "missing-alt"})}, ai=False) == {
        "a.html": frozenset({"missing-alt"})
    }


async def test_a_perfect_claude_scores_100_percent_on_every_rule(
    demo_site: httpx.AsyncClient,
    test_sessions: async_sessionmaker[AsyncSession],
    make_claude: Callable[[Answer], GuardedClaude],
) -> None:
    report = await evaluate(demo_site, test_sessions, make_claude(perfect_answer), None, "test")

    assert report.errors == []
    assert (report.total.precision, report.total.recall) == (1.0, 1.0)
    assert {"off-brand-tone", "weak-meta-description", "unclear-heading", "alt-mismatch"} <= set(
        report.rules
    )
    assert all(score.pages_exact == score.pages for score in report.sets.values())
    assert report.claude_calls > 0
    assert report.cost_usd > 0


async def test_a_claude_that_invents_problems_loses_precision_on_the_clean_set(
    demo_site: httpx.AsyncClient,
    test_sessions: async_sessionmaker[AsyncSession],
    make_claude: Callable[[Answer], GuardedClaude],
) -> None:

    def overeager(body: dict[str, Any]) -> httpx2.Response:
        if is_vision(body):
            return perfect_answer(body)
        return claude_reply(json.dumps({"findings": [finding("unclear-heading")]}))

    report = await evaluate(
        demo_site, test_sessions, make_claude(overeager), None, "test", sets=("clean",)
    )

    clean = report.sets["clean"]
    assert clean.total.found_wrong == 20
    assert clean.pages_exact == 0
    assert report.rules["unclear-heading"].precision == 0.0


async def test_each_set_is_saved_as_a_run_with_its_calls(
    demo_site: httpx.AsyncClient,
    test_sessions: async_sessionmaker[AsyncSession],
    make_claude: Callable[[Answer], GuardedClaude],
) -> None:
    report = await evaluate(
        demo_site, test_sessions, make_claude(perfect_answer), None, "test", sets=("injection",)
    )

    async with test_sessions() as session:
        run = await session.get(Run, report.run_ids["injection"])
        assert run is not None
        calls = await session.scalar(select(func.count()).where(ClaudeCall.run_id == run.id))

    assert run.sitemap_url == f"{SITE}/evals/injection/sitemap.xml"
    assert run.page_count == 6
    assert calls == report.claude_calls > 0


async def test_report_text_and_results_file(
    demo_site: httpx.AsyncClient,
    test_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    report = await evaluate(demo_site, test_sessions, None, None, "code-only", sets=("seeded",))

    text = report_text(report)
    saved = json.loads(save_report(report, tmp_path).read_text(encoding="utf-8"))

    assert "missing-alt" in text
    assert "seeded" in text
    assert "code checks only" in text
    assert saved["label"] == "code-only"
    assert saved["sets"]["seeded"]["pages"] == 30
    assert saved["total"]["precision"] == 1.0


def test_floors_decide_pass_or_fail() -> None:
    report = EvalReport(
        label="x",
        ai=True,
        sets={},
        rules={"rule": RuleScore(found_right=8, found_wrong=2, missed=2)},
        run_ids={},
        errors=[],
        claude_calls=0,
        cost_usd=Decimal(0),
        seconds=0.0,
        pages=0,
    )

    assert passes(report, 0.0, 0.0)
    assert passes(report, 0.8, 0.8)
    assert not passes(report, 0.9, 0.0)
    assert not passes(report, 0.0, 0.9)


def test_the_free_run_is_the_default() -> None:
    args = parse_args([])

    assert args.ai is False
    assert args.sets == ["clean", "seeded", "injection"]
    assert args.prompt == CURRENT_PROMPT
    assert args.code_first is True
    assert args.save is True


def test_saving_the_results_file_can_be_switched_off() -> None:
    assert parse_args(["--no-save"]).save is False


def test_code_first_can_be_switched_off() -> None:
    assert parse_args(["--ai", "--no-code-first"]).code_first is False


def test_the_model_can_be_picked_from_the_priced_ones() -> None:
    assert parse_args([]).model == "claude-haiku-5-5"
    assert parse_args(["--ai", "--model", "claude-opus-5-5"]).model == "claude-opus-5-5"
    with pytest.raises(SystemExit):
        parse_args(["--model", "claude-sonnet-5-5"])


def test_ollama_runs_gemma_unless_another_local_model_is_named() -> None:
    assert parse_args(["--ai", "--provider", "ollama"]).model == "gemma3:4b"
    assert (
        parse_args(["--ai", "--provider", "ollama", "--model", "qwen3-vl:4b"]).model
        == "qwen3-vl:4b"
    )


async def test_an_ollama_eval_costs_nothing_and_says_so(
    demo_site: httpx.AsyncClient,
    test_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    client = ollama(perfect_ollama, test_sessions)
    try:
        report = await evaluate(
            demo_site,
            test_sessions,
            client,
            None,
            "test",
            sets=("injection",),
            model="gemma3:4b",
            provider="ollama",
        )
    finally:
        await client.aclose()

    assert report.complete
    assert (report.total.precision, report.total.recall) == (1.0, 1.0)
    assert report.claude_calls > 0
    assert report.cost_usd == Decimal(0)
    assert "gemma3:4b (ollama)" in report_text(report)
    saved = json.loads(save_report(report, tmp_path).read_text(encoding="utf-8"))
    assert (saved["provider"], saved["model"]) == ("ollama", "gemma3:4b")


async def test_ollama_not_running_stops_the_eval_as_incomplete(
    demo_site: httpx.AsyncClient,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = ollama(down, test_sessions)
    try:
        report = await evaluate(
            demo_site,
            test_sessions,
            client,
            None,
            "test",
            sets=("injection",),
            model="gemma3:4b",
            provider="ollama",
        )
    finally:
        await client.aclose()

    assert not report.complete
    assert "is it running?" in report.stopped["injection"]
    assert report_text(report).splitlines()[2].startswith("INCOMPLETE")


async def test_the_model_reaches_every_call_and_the_results_file(
    demo_site: httpx.AsyncClient,
    test_sessions: async_sessionmaker[AsyncSession],
    make_claude: Callable[[Answer], GuardedClaude],
    tmp_path: Path,
) -> None:
    models: list[str] = []

    def recording(body: dict[str, Any]) -> httpx2.Response:
        models.append(body["model"])
        return perfect_answer(body)

    report = await evaluate(
        demo_site,
        test_sessions,
        make_claude(recording),
        None,
        "test",
        sets=("injection",),
        model="claude-haiku-5-5",
    )

    assert len(models) == report.claude_calls > 0
    assert set(models) == {"claude-haiku-5-5"}
    async with test_sessions() as session:
        logged = set(await session.scalars(select(ClaudeCall.model)))
    assert logged == {"claude-haiku-5-5"}
    assert "claude-haiku-5-5" in report_text(report)
    assert (
        json.loads(save_report(report, tmp_path).read_text(encoding="utf-8"))["model"]
        == "claude-haiku-5-5"
    )


def test_an_older_prompt_can_be_picked() -> None:
    assert parse_args(["--ai", "--prompt", "v1"]).prompt == "v1"
    with pytest.raises(SystemExit):
        parse_args(["--prompt", "v0"])


async def test_the_prompt_version_reaches_claude_and_the_results_file(
    demo_site: httpx.AsyncClient,
    test_sessions: async_sessionmaker[AsyncSession],
    make_claude: Callable[[Answer], GuardedClaude],
    tmp_path: Path,
) -> None:
    systems: list[str] = []

    def recording(body: dict[str, Any]) -> httpx2.Response:
        if not is_vision(body):
            systems.append(body["system"][0]["text"])
        return perfect_answer(body)

    report = await evaluate(
        demo_site,
        test_sessions,
        make_claude(recording),
        None,
        "test",
        sets=("injection",),
        prompt="v1",
    )

    assert len(systems) == 6
    assert all(system.startswith(INSTRUCTIONS["v1"]) for system in systems)
    assert "page prompt v1" in report_text(report)
    assert json.loads(save_report(report, tmp_path).read_text(encoding="utf-8"))["prompt"] == "v1"


async def test_a_free_run_has_no_prompt_version(
    demo_site: httpx.AsyncClient,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    report = await evaluate(demo_site, test_sessions, None, None, "test", sets=("injection",))

    assert (report.prompt, report.code_first) == (None, None)


async def test_without_code_first_claude_repeats_what_code_found(
    demo_site: httpx.AsyncClient,
    test_sessions: async_sessionmaker[AsyncSession],
    make_claude: Callable[[Answer], GuardedClaude],
    tmp_path: Path,
) -> None:
    alts: list[str | None] = []

    def recording(body: dict[str, Any]) -> httpx2.Response:
        if is_vision(body):
            alts.append(between(body["messages"][0]["content"][1]["text"], "image_details")["alt"])
        return perfect_answer(body)

    with_code = await evaluate(
        demo_site, test_sessions, make_claude(perfect_answer), None, "test", sets=("seeded",)
    )
    report = await evaluate(
        demo_site,
        test_sessions,
        make_claude(recording),
        None,
        "test",
        sets=("seeded",),
        code_first=False,
    )

    assert None in alts
    assert "IMG_2041.jpg" in alts
    assert "striped-block-final.png" in alts
    assert report.claude_calls == with_code.claude_calls + 4
    assert with_code.sets["seeded"].wrong == []
    assert report.sets["seeded"].wrong == [
        ("blue-square-no-alt.html", "alt-mismatch"),
        ("green-triangle-filename-alt.html", "alt-mismatch"),
        ("stacking-set-no-alt.html", "alt-mismatch"),
        ("striped-block-filename-alt.html", "alt-mismatch"),
    ]
    assert report.total.recall == 1.0
    assert "without the code checks' results" in report_text(report)
    assert (
        json.loads(save_report(report, tmp_path).read_text(encoding="utf-8"))["code_first"] is False
    )


async def test_a_run_stopped_by_the_rate_limit_is_flagged_incomplete(
    demo_site: httpx.AsyncClient,
    test_sessions: async_sessionmaker[AsyncSession],
    make_claude: Callable[[Answer], GuardedClaude],
    tmp_path: Path,
) -> None:
    calls = 0

    def rate_limited(body: dict[str, Any]) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return (
            perfect_answer(body)
            if calls <= 3
            else claude_error(429, "This request would exceed the rate limit")
        )

    report = await evaluate(
        demo_site, test_sessions, make_claude(rate_limited), None, "test", sets=("seeded",)
    )

    assert not report.complete
    assert report.stopped == {"seeded": "HTTP 429: This request would exceed the rate limit"}
    assert report_text(report).splitlines()[2].startswith("INCOMPLETE")
    assert not passes(report, 0.0, 0.0)
    assert (
        json.loads(save_report(report, tmp_path).read_text(encoding="utf-8"))["complete"] is False
    )


async def test_a_finished_run_is_complete(
    demo_site: httpx.AsyncClient,
    test_sessions: async_sessionmaker[AsyncSession],
    make_claude: Callable[[Answer], GuardedClaude],
) -> None:
    report = await evaluate(
        demo_site, test_sessions, make_claude(perfect_answer), None, "test", sets=("injection",)
    )

    assert report.complete
    assert "INCOMPLETE" not in report_text(report)
