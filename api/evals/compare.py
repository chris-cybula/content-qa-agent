# Eval compare
# Compares two saved eval runs: scores per rule, changed findings, cost and time.

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.run_evals import RESULTS_DIR

Result = dict[str, Any]


@dataclass(frozen=True)
class Change:
    kind: str

    where: str

    rule: str


CHANGE_TEXT = {
    "fixed": "no longer a false alarm",
    "found": "now found",
    "broke": "new false alarm",
    "lost": "now missed",
}


def find_result(name: str, results_dir: Path = RESULTS_DIR) -> Result:
    path = Path(name)
    if path.suffix == ".json" and path.is_file():
        result: Result = json.loads(path.read_text(encoding="utf-8"))
        return result

    matches = [
        data
        for data in (
            json.loads(file.read_text(encoding="utf-8")) for file in results_dir.glob("*.json")
        )
        if data["label"] == name
    ]
    if not matches:
        raise ValueError(f"No results file with the label {name!r} in {results_dir}")
    return max(matches, key=lambda data: data["created_at"])


def mistakes(result: Result, kind: str) -> set[tuple[str, str]]:
    return {
        (f"{set_name}/{page}", rule)
        for set_name, data in result["sets"].items()
        for page, rule in data[kind]
    }


def changes(before: Result, after: Result) -> list[Change]:
    wrong_before, wrong_after = mistakes(before, "wrong"), mistakes(after, "wrong")
    missed_before, missed_after = mistakes(before, "missed"), mistakes(after, "missed")
    groups = {
        "fixed": wrong_before - wrong_after,
        "found": missed_before - missed_after,
        "broke": wrong_after - wrong_before,
        "lost": missed_after - missed_before,
    }
    return [
        Change(kind, where, rule) for kind, pairs in groups.items() for where, rule in sorted(pairs)
    ]


def describe(result: Result) -> str:
    created = result["created_at"][:16].replace("T", " ")
    if not result["ai"]:
        return f"{result['label']} ({created}, code checks only)"
    model = result.get("model") or "claude-opus-5-5"
    prompt = result.get("prompt") or "not recorded"
    code_first = (
        "code checks first"
        if result.get("code_first", True)
        else "without the code checks' results"
    )
    return f"{result['label']} ({created}, {model}, page prompt {prompt}, {code_first})"


def arrow(before: str, after: str, width: int) -> str:
    text = before if before == after else f"{before} → {after}"
    return f"{text:>{width}}"


def percent(value: float) -> str:
    return f"{value:.0%}"


def score_row(name: str, before: dict[str, Any], after: dict[str, Any]) -> str:
    return (
        f"{name:<26}"
        f"{arrow(percent(before['precision']), percent(after['precision']), 13)}"
        f"{arrow(percent(before['recall']), percent(after['recall']), 13)}"
        f"{arrow(str(before['found_wrong']), str(after['found_wrong']), 9)}"
        f"{arrow(str(before['missed']), str(after['missed']), 9)}"
    )


def compare_text(before: Result, after: Result) -> str:
    lines = [f"Before: {describe(before)}", f"After:  {describe(after)}", ""]

    incomplete = [
        name
        for name, result in (("before", before), ("after", after))
        if not result.get("complete", True)
    ]
    for name in incomplete:
        lines.append(f"WARNING: the {name} run is incomplete, so its AI scores are not valid.")
    if incomplete:
        lines.append("")

    lines.append(f"{'Rule':<26}{'Precision':>13}{'Recall':>13}{'Wrong':>9}{'Missed':>9}")
    empty = {"precision": 1.0, "recall": 1.0, "found_wrong": 0, "missed": 0}
    for rule in sorted(before["rules"].keys() | after["rules"].keys()):
        lines.append(
            score_row(rule, before["rules"].get(rule, empty), after["rules"].get(rule, empty))
        )
    lines.append(score_row("All rules", before["total"], after["total"]))

    found_changes = changes(before, after)
    lines.append("")
    if found_changes:
        lines += [
            f"  {CHANGE_TEXT[change.kind]:<25}{change.where}: {change.rule}"
            for change in found_changes
        ]
    else:
        lines.append("  No finding changed.")

    lines.append("")
    lines.append(
        f"Cost: ${float(before['cost_usd']):.4f} → ${float(after['cost_usd']):.4f} · "
        f"Claude calls: {before['claude_calls']} → {after['claude_calls']} · "
        f"Time: {before['seconds']:.0f} s → {after['seconds']:.0f} s"
    )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python -m evals.compare BEFORE AFTER   (each a label or a results file path)")
        return 2

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        before, after = (find_result(name) for name in argv)
    except ValueError as error:
        print(error)
        return 1
    print(compare_text(before, after))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
