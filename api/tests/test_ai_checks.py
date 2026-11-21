# Tests: POST /runs/{run_id}/ai-checks with a fake Claude

import json
from collections.abc import AsyncIterator, Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.claude import GuardedClaude, get_guarded_claude
from app.config import Settings, get_settings
from app.http_client import get_http_client
from app.main import app
from app.tables import ClaudeCall, Finding
from app.tracing import open_telemetry_option
from tests.fakes import TINY_PNG, Route, claude_error, claude_reply, fake_claude, fake_client

pytestmark = pytest.mark.anyio

SITEMAP = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://shop.test/</loc></url>
  <url><loc>https://shop.test/about.html</loc></url>
  <url><loc>https://shop.test/gone.html</loc></url>
</urlset>"""
HOME = b"""<html lang="en-GB"><head><title>Home</title><meta name="description" content="Wooden toys."></head>
<body><h1>Welcome</h1><p>BUY NOW!!! Stock WON'T last.</p><a href="/about.html">About us</a>
<img src="/a.png"><img src="/star.png" alt="Yellow wooden star"></body></html>"""
ABOUT = b"""<html lang="en-GB"><head><title>About</title>
<meta name="description" content="Meet the small team that designs and makes our wooden shapes by hand in Devon.">
</head><body><h1>About us</h1><a href="/">Home</a><img src="/star.png" alt="Yellow wooden star"></body></html>"""

HOME_URL = "https://shop.test/"
ABOUT_URL = "https://shop.test/about.html"
STAR_URL = "https://shop.test/star.png"

SITE: dict[str, Route] = {
    "https://shop.test/sitemap.xml": (200, SITEMAP, {"content-type": "application/xml"}),
    HOME_URL: (200, HOME),
    ABOUT_URL: (200, ABOUT),
    STAR_URL: (200, TINY_PNG, {"content-type": "image/png"}),
}

GUIDE = "# Shop brand guide\n\nNever write in capitals for emphasis."

OFF_BRAND: dict[str, str] = {
    "rule": "off-brand-tone",
    "severity": "medium",
    "element": "first paragraph",
    "evidence": '"BUY NOW!!! Stock WON\'T last." shouts and pressures readers',
    "suggested_fix": "Our new shapes are in the shop now.",
}

MISMATCH: dict[str, str] = {
    "rule": "alt-mismatch",
    "severity": "high",
    "element": "the image",
    "evidence": 'The alt says "Yellow wooden star" but the image is a single pixel',
    "suggested_fix": "A small square",
}

Answer = Callable[[dict[str, Any]], httpx2.Response]

UseClaude = Callable[..., list[dict[str, Any]]]


def as_json(*findings: dict[str, str]) -> str:
    return json.dumps({"findings": list(findings)})


def is_vision(body: dict[str, Any]) -> bool:
    return isinstance(body["messages"][0]["content"], list)


def page_url_of(body: dict[str, Any]) -> str:
    message = body["messages"][0]["content"]
    return json.loads(message.split("<page>\n", 1)[1].rsplit("\n</page>", 1)[0])["url"]


def normal_answer(body: dict[str, Any]) -> httpx2.Response:
    if is_vision(body):
        return claude_reply(as_json(MISMATCH))
    return claude_reply(as_json(OFF_BRAND) if page_url_of(body) == HOME_URL else as_json())


def shop_settings(brand_guide_file: Path) -> Settings:
    return Settings(_env_file=None, max_pages=10, brand_guide_file=brand_guide_file)


def where(findings: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(finding["page_url"], finding["rule"]) for finding in findings]


@pytest.fixture
async def fake_shop(tmp_path: Path) -> AsyncIterator[Path]:
    guide = tmp_path / "brand-guide.md"
    guide.write_text(GUIDE, encoding="utf-8")
    fake = fake_client(SITE)
    app.dependency_overrides[get_http_client] = lambda: fake
    app.dependency_overrides[get_settings] = lambda: shop_settings(guide)
    yield guide
    app.dependency_overrides.pop(get_http_client, None)
    app.dependency_overrides.pop(get_settings, None)
    await fake.aclose()


@pytest.fixture
async def use_claude(test_sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[UseClaude]:
    guards: list[GuardedClaude] = []

    def use(
        answer: Answer = normal_answer,
        budget: str = "3",
        provider: TracerProvider | None = None,
    ) -> list[dict[str, Any]]:
        bodies: list[dict[str, Any]] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            body = json.loads(request.content)
            bodies.append(body)
            return answer(body)

        client = fake_claude(handler, open_telemetry_option(provider, record_content=True))
        tracer = provider.get_tracer("tests") if provider is not None else None
        guard = GuardedClaude(client, test_sessions, Decimal(budget), 8192, tracer)
        guards.append(guard)
        app.dependency_overrides[get_guarded_claude] = lambda: guard
        return bodies

    yield use
    app.dependency_overrides.pop(get_guarded_claude, None)
    for guard in guards:
        await guard.aclose()


async def crawl(client: httpx.AsyncClient) -> int:
    response = await client.post("/crawl", json={"sitemap_url": "https://shop.test/sitemap.xml"})
    assert response.status_code == 200
    return response.json()["run_id"]


async def ai_check(
    client: httpx.AsyncClient, run_id: int, status: int = 200, query: str = ""
) -> dict[str, Any]:
    response = await client.post(f"/runs/{run_id}/ai-checks{query}")
    assert response.status_code == status
    return response.json()


async def rule_counts(sessions: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    async with sessions() as session:
        rows = await session.execute(select(Finding.rule, func.count()).group_by(Finding.rule))
        return dict(rows.all())


async def test_findings_are_saved_beside_the_code_findings(
    client: httpx.AsyncClient,
    fake_shop: Path,
    use_claude: UseClaude,
) -> None:
    run_id = await crawl(client)
    use_claude()

    body = await ai_check(client, run_id)

    assert (body["run_id"], body["pages_checked"], body["images_checked"]) == (run_id, 2, 1)
    assert (body["stopped"], body["errors"]) == (None, [])
    assert where(body["findings"]) == [
        (HOME_URL, "off-brand-tone"),
        (HOME_URL, "alt-mismatch"),
        (ABOUT_URL, "alt-mismatch"),
    ]
    saved = (await client.get(f"/runs/{run_id}/findings")).json()
    home_rules = {finding["rule"] for finding in saved if finding["page_url"] == HOME_URL}
    assert {"off-brand-tone", "alt-mismatch", "missing-alt"} <= home_rules


async def test_each_readable_page_and_distinct_image_is_one_logged_call(
    client: httpx.AsyncClient,
    fake_shop: Path,
    use_claude: UseClaude,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await crawl(client)
    bodies = use_claude()

    await ai_check(client, run_id)

    page_bodies = [body for body in bodies if not is_vision(body)]
    assert [page_url_of(body) for body in page_bodies] == [HOME_URL, ABOUT_URL]
    assert all(
        body["system"][0]["text"].endswith(f"<brand_guide>\n{GUIDE}\n</brand_guide>")
        for body in page_bodies
    )
    assert sum(1 for body in bodies if is_vision(body)) == 1
    async with test_sessions() as session:
        calls = list(await session.scalars(select(ClaudeCall).order_by(ClaudeCall.id)))
    assert [(call.purpose, call.run_id, call.status) for call in calls] == [
        ("page-check", run_id, "answered"),
        ("page-check", run_id, "answered"),
        ("vision-check", run_id, "answered"),
    ]


async def test_a_shared_image_finding_lands_on_every_page_that_uses_it(
    client: httpx.AsyncClient,
    fake_shop: Path,
    use_claude: UseClaude,
) -> None:
    run_id = await crawl(client)
    use_claude()

    body = await ai_check(client, run_id, query="?checks=vision")

    mismatches = [finding for finding in body["findings"] if finding["rule"] == "alt-mismatch"]
    assert [(finding["page_url"], finding["element"]) for finding in mismatches] == [
        (HOME_URL, "img star.png"),
        (ABOUT_URL, "img star.png"),
    ]


async def test_checks_can_be_run_one_at_a_time(
    client: httpx.AsyncClient,
    fake_shop: Path,
    use_claude: UseClaude,
) -> None:
    run_id = await crawl(client)
    bodies = use_claude()

    body = await ai_check(client, run_id, query="?checks=vision")

    assert all(is_vision(request) for request in bodies)
    assert (body["pages_checked"], body["images_checked"]) == (0, 1)


async def test_an_unknown_check_name_is_refused(
    client: httpx.AsyncClient, fake_shop: Path, use_claude: UseClaude
) -> None:
    run_id = await crawl(client)
    bodies = use_claude()

    await ai_check(client, run_id, status=422, query="?checks=tone")

    assert bodies == []


async def test_running_again_replaces_the_earlier_ai_findings(
    client: httpx.AsyncClient,
    fake_shop: Path,
    use_claude: UseClaude,
    test_sessions: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await crawl(client)
    use_claude()
    before = await rule_counts(test_sessions)

    await ai_check(client, run_id)
    await ai_check(client, run_id)

    assert await rule_counts(test_sessions) == {**before, "off-brand-tone": 1, "alt-mismatch": 2}


async def test_an_unreadable_answer_is_reported_and_the_other_checks_go_on(
    client: httpx.AsyncClient,
    fake_shop: Path,
    use_claude: UseClaude,
) -> None:
    run_id = await crawl(client)

    def answer(body: dict[str, Any]) -> httpx2.Response:
        if not is_vision(body) and page_url_of(body) == HOME_URL:
            return claude_reply("Looks fine to me.")
        return normal_answer(body)

    use_claude(answer)

    body = await ai_check(client, run_id)

    assert (body["pages_checked"], body["images_checked"], body["stopped"]) == (1, 1, None)
    [error] = body["errors"]
    assert error["page_url"] == HOME_URL
    assert "doesn't match the findings schema" in error["error"]


async def test_an_image_that_cant_be_downloaded_is_reported_and_costs_nothing(
    client: httpx.AsyncClient,
    fake_shop: Path,
    use_claude: UseClaude,
) -> None:
    run_id = await crawl(client)
    bodies = use_claude()
    without_image = fake_client({url: route for url, route in SITE.items() if url != STAR_URL})
    app.dependency_overrides[get_http_client] = lambda: without_image

    body = await ai_check(client, run_id)
    await without_image.aclose()

    assert not any(is_vision(request) for request in bodies)
    assert (body["pages_checked"], body["images_checked"], body["stopped"]) == (2, 0, None)
    [error] = body["errors"]
    assert error["page_url"] == HOME_URL
    assert error["error"].startswith(f"Image {STAR_URL}:")
    assert "HTTP 404" in error["error"]


async def test_the_budget_limit_stops_the_checks_before_any_call(
    client: httpx.AsyncClient,
    fake_shop: Path,
    use_claude: UseClaude,
) -> None:
    run_id = await crawl(client)
    bodies = use_claude(budget="0.001")

    body = await ai_check(client, run_id)

    assert bodies == []
    assert (body["pages_checked"], body["images_checked"]) == (0, 0)
    assert body["stopped"].startswith("Claude budget reached")
    assert [error["page_url"] for error in body["errors"]] == [HOME_URL]


async def test_an_api_error_stops_the_checks(
    client: httpx.AsyncClient, fake_shop: Path, use_claude: UseClaude
) -> None:
    run_id = await crawl(client)
    bodies = use_claude(lambda body: claude_error(529, "Overloaded"))

    body = await ai_check(client, run_id)

    assert len(bodies) == 1
    assert (body["pages_checked"], body["images_checked"], body["stopped"]) == (
        0,
        0,
        "HTTP 529: Overloaded",
    )


async def test_an_api_error_in_the_vision_check_keeps_the_page_findings(
    client: httpx.AsyncClient,
    fake_shop: Path,
    use_claude: UseClaude,
) -> None:
    run_id = await crawl(client)
    use_claude(
        lambda body: claude_error(529, "Overloaded") if is_vision(body) else normal_answer(body)
    )

    body = await ai_check(client, run_id)

    assert (body["pages_checked"], body["images_checked"], body["stopped"]) == (
        2,
        0,
        "HTTP 529: Overloaded",
    )
    assert where(body["findings"]) == [(HOME_URL, "off-brand-tone")]


async def test_unknown_run_is_404(
    client: httpx.AsyncClient, fake_shop: Path, use_claude: UseClaude
) -> None:
    bodies = use_claude()

    body = await ai_check(client, 999, status=404)

    assert body == {"detail": "No run with id 999"}
    assert bodies == []


async def test_a_missing_brand_guide_file_fails_before_any_call(
    client: httpx.AsyncClient,
    fake_shop: Path,
    use_claude: UseClaude,
) -> None:
    run_id = await crawl(client)
    bodies = use_claude()
    app.dependency_overrides[get_settings] = lambda: shop_settings(
        fake_shop.with_name("missing.md")
    )

    body = await ai_check(client, run_id, status=500)

    assert body["detail"].startswith("Can't read BRAND_GUIDE_FILE")
    assert bodies == []


def test_brand_guide_paths_start_from_the_api_folder() -> None:
    guide = Settings(
        _env_file=None, brand_guide_file=Path("../demo-site/brand-guide.md")
    ).read_brand_guide()

    assert guide is not None
    assert guide.startswith("# Little Shapes brand guide")


@pytest.mark.parametrize("value", [None, ""])
def test_no_brand_guide_setting_means_no_guide(value: str | None) -> None:
    assert Settings(_env_file=None, brand_guide_file=value).read_brand_guide() is None


class BrokenExporter(SpanExporter):
    def export(self, spans: Any) -> SpanExportResult:
        raise ConnectionError("Langfuse is down")


def in_memory_provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def parent_name(span: ReadableSpan, spans: list[ReadableSpan]) -> str | None:
    if span.parent is None:
        return None
    return next(other.name for other in spans if other.context.span_id == span.parent.span_id)


async def test_an_ai_check_run_is_one_trace_with_each_call_under_its_page_or_image(
    client: httpx.AsyncClient,
    fake_shop: Path,
    use_claude: UseClaude,
) -> None:
    run_id = await crawl(client)
    provider, exporter = in_memory_provider()
    use_claude(provider=provider)

    await ai_check(client, run_id)

    spans = list(exporter.get_finished_spans())
    assert len({span.context.trace_id for span in spans}) == 1
    tree = [
        (span.name, parent_name(span, spans))
        for span in spans
        if span.name != "anthropic.messages.create"
    ]
    assert sorted(tree, key=str) == sorted(
        [
            (f"ai-checks run {run_id}", None),
            (f"page-check {HOME_URL}", f"ai-checks run {run_id}"),
            (f"page-check {ABOUT_URL}", f"ai-checks run {run_id}"),
            (f"vision-check {STAR_URL}", f"ai-checks run {run_id}"),
        ],
        key=str,
    )
    calls = [span for span in spans if span.name == "anthropic.messages.create"]
    assert sorted(parent_name(span, spans) or "" for span in calls) == sorted(
        [f"page-check {HOME_URL}", f"page-check {ABOUT_URL}", f"vision-check {STAR_URL}"]
    )


async def test_the_run_span_sums_up_the_outcome_and_calls_carry_their_prompts(
    client: httpx.AsyncClient,
    fake_shop: Path,
    use_claude: UseClaude,
) -> None:
    run_id = await crawl(client)
    provider, exporter = in_memory_provider()
    use_claude(provider=provider)

    await ai_check(client, run_id)

    spans = list(exporter.get_finished_spans())
    [root] = [span for span in spans if span.parent is None]
    assert root.attributes is not None
    assert (root.attributes["run.id"], root.attributes["ai_checks.pages_checked"]) == (run_id, 2)
    assert (root.attributes["ai_checks.images_checked"], root.attributes["ai_checks.findings"]) == (
        1,
        3,
    )
    call = next(span for span in spans if span.name == "anthropic.messages.create")
    assert call.attributes is not None
    assert "BUY NOW" in str(call.attributes.get("gen_ai.input.messages"))


async def test_a_trace_backend_that_is_down_doesnt_stop_the_checks(
    client: httpx.AsyncClient,
    fake_shop: Path,
    use_claude: UseClaude,
) -> None:
    run_id = await crawl(client)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(BrokenExporter()))
    use_claude(provider=provider)

    body = await ai_check(client, run_id)

    assert (body["pages_checked"], body["images_checked"], body["stopped"]) == (2, 1, None)
    assert len(body["findings"]) == 3
