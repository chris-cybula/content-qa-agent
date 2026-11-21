# Tests: experiments table

import json
from pathlib import Path
from typing import Any

import pytest

from evals.experiments import (
    EXPERIMENTS,
    Experiment,
    ai_rules_table,
    complete_runs,
    experiments_table,
    runs_table,
)


def results_page_rows(results_dir: Path) -> str:
    runs = complete_runs(results_dir)
    return runs_table(runs) + "\n" + ai_rules_table(runs)


def result(
    label: str, precision: float, recall: float, wrong: int, missed: int, cost: str, seconds: float
) -> dict[str, Any]:
    return {
        "label": label,
        "created_at": "2026-11-18T10:00:00+00:00",
        "ai": True,
        "complete": True,
        "total": {"precision": precision, "recall": recall, "found_wrong": wrong, "missed": missed},
        "cost_usd": cost,
        "seconds": seconds,
    }


def save(folder: Path, data: dict[str, Any]) -> None:
    (folder / f"{data['label']}.json").write_text(json.dumps(data), encoding="utf-8")


CHANGED = Experiment("Bigger model → smaller model", "big", "small", False)
SAME = Experiment("Old wording → new wording", "old", "new", True)


def test_each_experiment_is_a_row_of_before_and_after(tmp_path: Path) -> None:
    save(tmp_path, result("big", 0.97, 1.0, 1, 0, "0.006735", 400))
    save(tmp_path, result("small", 0.34, 0.74, 55, 10, "0", 340))
    save(tmp_path, result("old", 0.97, 1.0, 1, 0, "0.25", 402))
    save(tmp_path, result("new", 0.97, 1.0, 1, 0, "0.25", 402))

    table = experiments_table(tmp_path, (CHANGED, SAME)).splitlines()

    assert table[0].startswith("| Change | Precision | Recall | False alarms | Missed |")
    assert table[2] == (
        "| Bigger model → smaller model | 97% → 34% | 100% → 74% | 1 → 55 | 0 → 10 | $0.007 → $0 | 6.7 min → 5.7 min | Not kept |"
    )
    assert table[3] == "| Old wording → new wording | 97% | 100% | 1 | 0 | $0.25 | 6.7 min | Kept |"


def test_an_incomplete_run_is_refused(tmp_path: Path) -> None:
    save(tmp_path, result("big", 0.97, 1.0, 1, 0, "0.006735", 400))
    save(tmp_path, {**result("small", 0.32, 0.63, 51, 14, "0", 292), "complete": False})

    with pytest.raises(ValueError, match="small run is incomplete"):
        experiments_table(tmp_path, (CHANGED,))


def test_the_results_page_lists_every_finished_run_and_each_ai_rule(tmp_path: Path) -> None:
    free = {
        **result("code-only", 1.0, 1.0, 0, 0, "0", 1),
        "ai": False,
        "rules": {},
        "claude_calls": 0,
    }
    rules = {"off-brand-tone": {"precision": 0.13, "recall": 1.0}}
    local = {
        **result("local", 0.34, 0.74, 55, 10, "0", 340),
        "model": "gemma3:4b",
        "prompt": "v2",
        "rules": rules,
        "claude_calls": 67,
        "created_at": "2026-11-18T12:10:00+00:00",
    }
    stopped = {**local, "label": "stopped", "complete": False}
    for data in (free, local, stopped):
        save(tmp_path, data)

    page = results_page_rows(tmp_path)

    assert (
        "| code-only | 2026-11-18 | Code checks only | 100% | 100% | 0 | 0 | 0 | $0 | 0.0 min |"
        in page
    )
    assert (
        "| local | 2026-11-18 | gemma3:4b, prompt v2, code checks first | 34% | 74% | 55 | 10 | 67 | $0 | 5.7 min |"
        in page
    )
    assert "stopped" not in page
    assert "| off-brand-tone | 13% / 100% |" in page


def test_every_experiment_has_its_saved_runs() -> None:
    table = experiments_table()

    assert len(table.splitlines()) == 2 + len(EXPERIMENTS)
