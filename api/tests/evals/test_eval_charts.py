# Tests: eval charts helpers

import json
from pathlib import Path
from typing import Any

import matplotlib
import pytest

from evals.charts import (
    latest_full_run,
    load_results,
    mistakes_table,
    plot_cost_vs_quality,
    plot_rules,
    plot_runs,
    plot_sets,
    rules_table,
    runs_table,
    sets_table,
)

matplotlib.use("Agg")


def result(label: str, created_at: str, ai: bool = True, complete: bool = True) -> dict[str, Any]:
    return {
        "label": label,
        "created_at": created_at,
        "ai": ai,
        "complete": complete,
        "stopped": {},
        "total": {
            "found_right": 8,
            "found_wrong": 1,
            "missed": 1,
            "precision": 0.8889,
            "recall": 0.8889,
        },
        "rules": {
            "missing-alt": {
                "found_right": 2,
                "found_wrong": 0,
                "missed": 0,
                "precision": 1.0,
                "recall": 1.0,
            },
            "unclear-heading": {
                "found_right": 3,
                "found_wrong": 1,
                "missed": 0,
                "precision": 0.75,
                "recall": 1.0,
            },
            "off-brand-tone": {
                "found_right": 3,
                "found_wrong": 0,
                "missed": 1,
                "precision": 1.0,
                "recall": 0.75,
            },
        },
        "sets": {
            "clean": {
                "run_id": 1,
                "pages": 2,
                "pages_exact": 1,
                "total": {
                    "found_right": 0,
                    "found_wrong": 1,
                    "missed": 0,
                    "precision": 0.0,
                    "recall": 1.0,
                },
                "rules": {},
                "wrong": [["a.html", "unclear-heading"]],
                "missed": [],
            },
            "seeded": {
                "run_id": 2,
                "pages": 3,
                "pages_exact": 2,
                "total": {
                    "found_right": 8,
                    "found_wrong": 0,
                    "missed": 1,
                    "precision": 1.0,
                    "recall": 0.8889,
                },
                "rules": {},
                "wrong": [],
                "missed": [["b.html", "off-brand-tone"]],
            },
        },
        "errors": [],
        "claude_calls": 40,
        "cost_usd": "0.2366",
        "seconds": 401.4,
    }


@pytest.fixture
def results_dir(tmp_path: Path) -> Path:
    files = {
        "3.json": result("prompt-v2", "2026-11-20T10:00:00+00:00"),
        "1.json": result("code-only", "2026-11-17T10:05:00+00:00", ai=False),
        "2.json": result("prompt-v1", "2026-11-17T12:29:00+00:00"),
        "0.json": result("broken", "2026-11-17T12:09:00+00:00", complete=False),
    }
    for name, data in files.items():
        (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


def test_results_load_oldest_first_with_their_file_names(results_dir: Path) -> None:
    results = load_results(results_dir)

    assert [result["label"] for result in results] == [
        "code-only",
        "broken",
        "prompt-v1",
        "prompt-v2",
    ]
    assert results[0]["file"] == "1.json"


def test_the_latest_full_run_skips_free_and_incomplete_runs(results_dir: Path) -> None:
    assert latest_full_run(load_results(results_dir))["label"] == "prompt-v2"
    assert latest_full_run(load_results(results_dir)[:3])["label"] == "prompt-v1"


def test_no_full_run_yet_says_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"python -m evals\.run_evals --ai"):
        latest_full_run(load_results(tmp_path))


def test_runs_table_has_one_row_per_run(results_dir: Path) -> None:
    table = runs_table(load_results(results_dir))

    assert list(table["label"]) == ["code-only", "broken", "prompt-v1", "prompt-v2"]
    assert table.loc[2, "cost_usd"] == pytest.approx(0.2366)
    assert table.loc[2, "minutes"] == 6.7
    assert list(table["complete"]) == [True, False, True, True]


def test_rules_table_puts_the_weakest_rules_first(results_dir: Path) -> None:
    table = rules_table(load_results(results_dir)[-1])

    assert list(table["rule"]) == ["off-brand-tone", "unclear-heading", "missing-alt"]


def test_sets_and_mistakes_tables(results_dir: Path) -> None:
    run = load_results(results_dir)[-1]

    sets = sets_table(run)
    mistakes = mistakes_table(run)

    assert list(sets["set"]) == ["clean", "seeded"]
    assert list(sets["exact"]) == [1, 2]
    assert mistakes.to_dict("records") == [
        {"set": "clean", "page": "a.html", "rule": "unclear-heading", "kind": "wrong"},
        {"set": "seeded", "page": "b.html", "rule": "off-brand-tone", "kind": "missed"},
    ]


def test_every_chart_draws(results_dir: Path) -> None:
    results = load_results(results_dir)
    latest = latest_full_run(results)

    figures = [
        plot_rules(latest),
        plot_sets(latest),
        plot_runs(results),
        plot_cost_vs_quality(results),
    ]

    assert [
        figure.axes[0].get_title("left") or figure.axes[0].get_title() for figure in figures
    ] == [
        "Precision and recall per rule · prompt-v2",
        "Mistakes per eval set · prompt-v2",
        "Runs compared",
        "Cost against quality",
    ]


def test_the_real_results_folder_loads() -> None:
    results = load_results()

    assert results
    assert all("label" in result and "total" in result for result in results)
