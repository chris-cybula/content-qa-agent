# Experiments tables
# Builds the README's before-and-after experiments table and the README_results.md page.

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from app.checks.ai_findings import AI_RULES
from evals.compare import Result, find_result
from evals.run_evals import RESULTS_DIR


@dataclass(frozen=True)
class Experiment:
    change: str

    before: str

    after: str

    kept: bool


EXPERIMENTS: tuple[Experiment, ...] = (
    Experiment(
        "Page prompt v1 → v2: headings judged with the page title around them",
        "prompt-v1",
        "prompt-v2",
        True,
    ),
    Experiment("Claude checks everything → code checks first", "no-code-first", "prompt-v2", True),
    Experiment("Claude Opus 5.5 → Claude Haiku 5.5", "prompt-v2", "haiku", True),
    Experiment(
        "Claude Haiku 5.5 → Gemma 3 4B, a free local model through Ollama",
        "haiku",
        "ollama-gemma3",
        False,
    ),
)


def arrow(before: str, after: str) -> str:
    return before if before == after else f"{before} → {after}"


def percent(value: float) -> str:
    return f"{value:.0%}"


def dollars(value: str) -> str:
    amount = float(value)
    if amount == 0:
        return "$0"
    return f"${amount:.3f}" if amount < 0.01 else f"${amount:.2f}"


def row(experiment: Experiment, before: Result, after: Result) -> str:
    cells = [
        experiment.change,
        arrow(percent(before["total"]["precision"]), percent(after["total"]["precision"])),
        arrow(percent(before["total"]["recall"]), percent(after["total"]["recall"])),
        arrow(str(before["total"]["found_wrong"]), str(after["total"]["found_wrong"])),
        arrow(str(before["total"]["missed"]), str(after["total"]["missed"])),
        arrow(dollars(before["cost_usd"]), dollars(after["cost_usd"])),
        arrow(f"{before['seconds'] / 60:.1f} min", f"{after['seconds'] / 60:.1f} min"),
        "Kept" if experiment.kept else "Not kept",
    ]
    return "| " + " | ".join(cells) + " |"


def experiments_table(
    results_dir: Path = RESULTS_DIR, experiments: tuple[Experiment, ...] = EXPERIMENTS
) -> str:
    lines = [
        "| Change | Precision | Recall | False alarms | Missed | Cost per eval run | Time | Result |",
        "|--------|-----------|--------|--------------|--------|-------------------|------|--------|",
    ]
    for experiment in experiments:
        before = find_result(experiment.before, results_dir)
        after = find_result(experiment.after, results_dir)
        for result in (before, after):
            if not result.get("complete", True):
                raise ValueError(
                    f"The newest {result['label']} run is incomplete; run it again before building the table"
                )
        lines.append(row(experiment, before, after))
    return "\n".join(lines)


def complete_runs(results_dir: Path = RESULTS_DIR) -> list[Result]:
    runs = [json.loads(path.read_text(encoding="utf-8")) for path in results_dir.glob("*.json")]
    return sorted(
        (run for run in runs if run.get("complete", True)), key=lambda run: run["created_at"]
    )


def setup(run: Result) -> str:
    if not run["ai"]:
        return "Code checks only"
    parts = [run.get("model") or "claude-opus-5-5"]
    if run.get("prompt"):
        parts.append(f"prompt {run['prompt']}")
    parts.append("code checks first" if run.get("code_first", True) else "no code checks first")
    return ", ".join(parts)


def runs_table(runs: list[Result]) -> str:
    lines = [
        "| Run | Date | Setup | Precision | Recall | False alarms | Missed | AI calls | Cost | Time |",
        "|-----|------|-------|-----------|--------|--------------|--------|----------|------|------|",
    ]
    for run in runs:
        total = run["total"]
        cells = [
            run["label"],
            run["created_at"][:10],
            setup(run),
            percent(total["precision"]),
            percent(total["recall"]),
            str(total["found_wrong"]),
            str(total["missed"]),
            str(run["claude_calls"]),
            dollars(run["cost_usd"]),
            f"{run['seconds'] / 60:.1f} min",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def ai_rules_table(runs: list[Result]) -> str:
    ai_runs = [run for run in runs if run["ai"]]
    lines = [
        "| Rule | " + " | ".join(run["label"] for run in ai_runs) + " |",
        "|------|" + "|".join("---" for _ in ai_runs) + "|",
    ]
    for rule in AI_RULES:
        cells = []
        for run in ai_runs:
            score = run["rules"].get(rule)
            cells.append(
                "—"
                if score is None
                else f"{percent(score['precision'])} / {percent(score['recall'])}"
            )
        lines.append(f"| {rule} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


FINDINGS = """\
- **Prompt v2:** reading each heading with the page title removed one of the two false alarms; nothing was missed and the cost didn't go up.
- **Code checks first:** without them Claude made 4 more calls and its 5 extra findings all repeated problems code had already reported.
- **Haiku:** matched Opus on every rule for about 3% of the cost, and ignored every hidden instruction too, so both AI checks run on Haiku.
- **Local model:** Gemma 3 4B costs nothing but called almost every page off-brand, judged most correct alt text wrong and found none of the unclear headings, so the AI checks stay on Claude.
- **Run-to-run variation:** Claude's answers vary a little between runs; the same short heading was flagged in one run and not in another, so a difference of one finding is within noise."""


def results_page(results_dir: Path = RESULTS_DIR) -> str:
    runs = complete_runs(results_dir)
    return "\n".join(
        [
            "# Eval results",
            "",
            "Every run checks the same 56 demo pages against a known answer key: 20 clean pages, 30 with planted problems "
            "and 6 that hide instructions aimed at the AI. Precision is how many reported problems are real; recall is how "
            "many real problems were found. Built from the saved results in `api/evals/results` with "
            "`python -m evals.experiments --page`.",
            "",
            "## Experiments",
            "",
            experiments_table(results_dir),
            "",
            FINDINGS,
            "",
            "## Every run",
            "",
            runs_table(runs),
            "",
            "## AI rules per run (precision / recall)",
            "",
            "The 11 code rules score 100% / 100% in every run, so only the 4 AI rules are shown.",
            "",
            ai_rules_table(runs),
            "",
        ]
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build Markdown tables of the saved eval results.")
    parser.add_argument(
        "--page",
        type=Path,
        help="write the full results page to this file instead of printing the experiments table",
    )
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.page is not None:
            args.page.write_text(results_page(), encoding="utf-8")
            print(f"Saved {args.page.resolve()}")
        else:
            print(experiments_table())
    except ValueError as error:
        print(error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
