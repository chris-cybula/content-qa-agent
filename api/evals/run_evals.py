# Eval runner
# Crawls the eval sets, runs the checks, scores them, prints a report and saves it to evals/results.
# Without --ai it is free; --ai makes paid Claude calls inside the monthly budget.

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, get_args
from urllib.parse import urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_checks import run_ai_checks
from app.budget import PRICES
from app.checks import FindingData, run_code_checks
from app.checks.ai_findings import AI_RULES
from app.checks.page_check import CURRENT_PROMPT, PROMPT_VERSIONS, PromptVersion
from app.claude import DEFAULT_MODEL, AiClient, get_guarded_claude
from app.config import get_settings
from app.db import get_engine, get_sessionmaker
from app.http_client import USER_AGENT
from app.ollama import DEFAULT_OLLAMA_MODEL, get_ollama_client
from app.pages import fetch_pages
from app.runs import finish_run, run_totals, start_run
from app.sitemap import read_sitemap
from app.tracing import shutdown_tracing
from evals.answer_key import EVAL_SETS, AnswerKey, load_answer_key
from evals.scoring import PageRule, RuleScore, SetScore, combine, score_set

DEMO_SITE = "http://localhost:8080"

RESULTS_DIR = Path(__file__).resolve().parent / "results"

MAX_EVAL_PAGES = 100

CONCURRENCY = 4

Provider = Literal["anthropic", "ollama"]
PROVIDERS: tuple[Provider, ...] = get_args(Provider)


@dataclass
class SetRun:
    name: str

    run_id: int

    found: set[PageRule]

    errors: list[str] = field(default_factory=list)

    stopped: str | None = None


@dataclass
class EvalReport:
    label: str

    ai: bool

    sets: dict[str, SetScore]

    rules: dict[str, RuleScore]

    run_ids: dict[str, int]

    errors: list[str]

    claude_calls: int

    cost_usd: Decimal

    seconds: float

    pages: int

    stopped: dict[str, str] = field(default_factory=dict)

    prompt: PromptVersion | None = None

    code_first: bool | None = None

    model: str | None = None

    provider: "Provider | None" = None

    @property
    def complete(self) -> bool:
        return not self.stopped

    @property
    def total(self) -> RuleScore:
        total = RuleScore()
        for rule_score in self.rules.values():
            total.add(rule_score)
        return total


def page_name(url: str) -> str:
    return urlparse(url).path.rsplit("/", 1)[-1]


def in_scope(expected: dict[str, frozenset[str]], ai: bool) -> dict[str, frozenset[str]]:
    if ai:
        return expected
    return {
        page: frozenset(rule for rule in rules if rule not in AI_RULES)
        for page, rules in expected.items()
    }


async def run_set(
    name: str,
    client: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    claude: AiClient | None,
    brand_guide: str | None,
    demo_site: str = DEMO_SITE,
    prompt: PromptVersion = CURRENT_PROMPT,
    code_first: bool = True,
    model: str = DEFAULT_MODEL,
) -> SetRun:
    sitemap_url = f"{demo_site}/evals/{name}/sitemap.xml"
    urls = await read_sitemap(client, sitemap_url, MAX_EVAL_PAGES)
    pages = await fetch_pages(client, urls, CONCURRENCY)
    findings: list[FindingData] = await run_code_checks(client, pages, CONCURRENCY)
    errors = [f"{name}/{page_name(page.url)}: {page.error}" for page in pages if page.error]

    stopped: str | None = None
    async with sessions() as session:
        run = await start_run(session, sitemap_url)
        await finish_run(session, run, pages, findings)
        run_id = run.id

        if claude is not None:
            result = await run_ai_checks(
                session,
                claude,
                client,
                run_id,
                brand_guide,
                prompt=prompt,
                code_first=code_first,
                model=model,
            )
            if result is not None:
                findings += result.findings
                stopped = result.stopped
                errors += [
                    f"{name}/{page_name(error.page_url)}: {error.error}"
                    for error in result.errors
                    if error.error != stopped
                ]

    found = {(page_name(finding.page_url), finding.rule) for finding in findings}
    return SetRun(name, run_id, found, errors, stopped)


async def evaluate(
    client: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    claude: AiClient | None,
    brand_guide: str | None,
    label: str,
    sets: tuple[str, ...] = EVAL_SETS,
    key: AnswerKey | None = None,
    demo_site: str = DEMO_SITE,
    prompt: PromptVersion = CURRENT_PROMPT,
    code_first: bool = True,
    model: str = DEFAULT_MODEL,
    provider: Provider = "anthropic",
) -> EvalReport:
    key = key if key is not None else load_answer_key()
    started = time.perf_counter()
    ai = claude is not None

    runs = [
        await run_set(
            name, client, sessions, claude, brand_guide, demo_site, prompt, code_first, model
        )
        for name in sets
    ]

    scores: dict[str, SetScore] = {}
    errors: list[str] = []
    for set_run in runs:
        expected = in_scope(key[set_run.name], ai)
        unknown = sorted({page for page, _ in set_run.found} - set(expected))
        errors += [f"{set_run.name}/{page}: page is not in the answer key" for page in unknown]
        scores[set_run.name] = score_set(expected, set_run.found)
        errors += set_run.errors

    async with sessions() as session:
        totals = await run_totals(session, [set_run.run_id for set_run in runs])
    calls = sum(int(total.get("claude_calls", 0)) for total in totals.values())
    cost = sum((Decimal(total.get("cost_usd", 0)) for total in totals.values()), Decimal(0))

    return EvalReport(
        label=label,
        ai=ai,
        sets=scores,
        rules=combine(scores),
        run_ids={set_run.name: set_run.run_id for set_run in runs},
        errors=errors,
        claude_calls=calls,
        cost_usd=cost,
        seconds=round(time.perf_counter() - started, 1),
        pages=sum(score.pages for score in scores.values()),
        stopped={set_run.name: set_run.stopped for set_run in runs if set_run.stopped},
        prompt=prompt if ai else None,
        code_first=code_first if ai else None,
        model=model if ai else None,
        provider=provider if ai else None,
    )


def percent(value: float) -> str:
    return f"{value:4.0%}"


def report_text(report: EvalReport) -> str:
    if report.ai:
        gating = "code checks first" if report.code_first else "AI without the code checks' results"
        mode = f"code and AI checks · {report.model} ({report.provider}) · page prompt {report.prompt} · {gating}"
    else:
        mode = "code checks only (free; AI rules not scored)"
    lines = [f"Evals: {report.label} · {mode}", ""]

    if not report.complete:
        lines.append(
            "INCOMPLETE: the AI checks stopped early, so the AI scores below are not valid."
        )
        lines += [f"  {name}: {reason}" for name, reason in report.stopped.items()]
        lines.append("")

    lines.append(f"{'Rule':<26}{'Precision':>10}{'Recall':>8}{'Right':>7}{'Wrong':>7}{'Missed':>8}")
    for rule, score in report.rules.items():
        lines.append(
            f"{rule:<26}{percent(score.precision):>10}{percent(score.recall):>8}"
            f"{score.found_right:>7}{score.found_wrong:>7}{score.missed:>8}"
        )
    total = report.total
    lines.append(
        f"{'All rules':<26}{percent(total.precision):>10}{percent(total.recall):>8}"
        f"{total.found_right:>7}{total.found_wrong:>7}{total.missed:>8}"
    )

    lines.append("")
    for name, set_score in report.sets.items():
        lines.append(
            f"{name:<10} run {report.run_ids[name]:<5} {set_score.pages:>3} pages · exact on {set_score.pages_exact} · "
            f"{set_score.total.found_wrong} wrong · {set_score.total.missed} missed"
        )

    for name, set_score in report.sets.items():
        lines += [f"  wrong   {name}/{page}: {rule}" for page, rule in set_score.wrong]
        lines += [f"  missed  {name}/{page}: {rule}" for page, rule in set_score.missed]

    lines += [f"  error   {error}" for error in report.errors]
    lines.append("")
    lines.append(
        f"Cost: ${report.cost_usd:.4f} · {report.claude_calls} AI calls · {report.pages} pages · {report.seconds} s"
    )
    return "\n".join(lines)


def report_json(report: EvalReport) -> dict[str, Any]:
    return {
        "label": report.label,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "ai": report.ai,
        "prompt": report.prompt,
        "code_first": report.code_first,
        "model": report.model,
        "provider": report.provider,
        "complete": report.complete,
        "stopped": report.stopped,
        "total": report.total.as_dict(),
        "rules": {rule: score.as_dict() for rule, score in report.rules.items()},
        "sets": {
            name: {
                "run_id": report.run_ids[name],
                "pages": score.pages,
                "pages_exact": score.pages_exact,
                "total": score.total.as_dict(),
                "rules": {rule: rule_score.as_dict() for rule, rule_score in score.rules.items()},
                "wrong": [list(pair) for pair in score.wrong],
                "missed": [list(pair) for pair in score.missed],
            }
            for name, score in report.sets.items()
        },
        "errors": report.errors,
        "claude_calls": report.claude_calls,
        "cost_usd": str(report.cost_usd),
        "seconds": report.seconds,
    }


def save_report(report: EvalReport, results_dir: Path = RESULTS_DIR) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d-%H%M")
    path = results_dir / f"{stamp}-{report.label}.json"
    path.write_text(json.dumps(report_json(report), indent=2) + "\n", encoding="utf-8")
    return path


def passes(report: EvalReport, min_precision: float, min_recall: float) -> bool:
    return (
        report.complete
        and report.total.precision >= min_precision
        and report.total.recall >= min_recall
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score the checks against the eval sets' answer key."
    )
    parser.add_argument(
        "--ai",
        action="store_true",
        help="also run the AI checks (paid Claude calls, or free with --provider ollama)",
    )
    parser.add_argument(
        "--label", default="manual", help="name for this run, used in the results file name"
    )
    parser.add_argument(
        "--prompt",
        choices=PROMPT_VERSIONS,
        default=CURRENT_PROMPT,
        help="page check prompt version for the AI checks",
    )
    parser.add_argument(
        "--code-first",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="let the code checks' findings decide what Claude is sent and kept (--no-code-first to switch off)",
    )
    parser.add_argument(
        "--provider",
        choices=PROVIDERS,
        default="anthropic",
        help="who runs the AI checks: Claude through the budget guard, or a free local model through Ollama",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"model for both AI checks; Claude: one priced in app/budget.py (default {DEFAULT_MODEL}); "
        f"Ollama: a pulled model (default {DEFAULT_OLLAMA_MODEL})",
    )
    parser.add_argument(
        "--sets",
        nargs="+",
        choices=EVAL_SETS,
        default=list(EVAL_SETS),
        help="which eval sets to run",
    )
    parser.add_argument(
        "--min-precision", type=float, default=0.0, help="exit with 1 below this total precision"
    )
    parser.add_argument(
        "--min-recall", type=float, default=0.0, help="exit with 1 below this total recall"
    )
    parser.add_argument(
        "--demo-site", default=DEMO_SITE, help="where the demo site serves the eval sets"
    )
    parser.add_argument(
        "--save",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="write the results file to evals/results (--no-save for quick local checks)",
    )
    args = parser.parse_args(argv)

    if args.model is None:
        args.model = DEFAULT_MODEL if args.provider == "anthropic" else DEFAULT_OLLAMA_MODEL
    elif args.provider == "anthropic" and args.model not in PRICES:
        parser.error(
            f"no price for {args.model} in app/budget.py, so the budget guard can't allow it; use one of: {', '.join(PRICES)}"
        )
    return args


async def main(argv: list[str]) -> int:
    args = parse_args(argv)
    settings = get_settings()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sessions = get_sessionmaker(settings.database_url)

    claude: AiClient | None = None
    if args.ai and args.provider == "ollama":
        claude = get_ollama_client(settings)
    elif args.ai:
        claude = get_guarded_claude(settings)
    brand_guide = settings.read_brand_guide() if args.ai else None

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            timeout=settings.http_timeout_seconds,
        ) as client:
            report = await evaluate(
                client,
                sessions,
                claude,
                brand_guide,
                args.label,
                tuple(args.sets),
                demo_site=args.demo_site,
                prompt=args.prompt,
                code_first=args.code_first,
                model=args.model,
                provider=args.provider,
            )
    finally:
        if claude is not None:
            await claude.aclose()
        await get_engine(settings.database_url).dispose()
        shutdown_tracing()

    print(report_text(report))
    if args.save:
        print(f"Saved {save_report(report)}")
    return 0 if passes(report, args.min_precision, args.min_recall) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
