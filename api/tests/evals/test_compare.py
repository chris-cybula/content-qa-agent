# Tests: comparing two eval runs

import json
from pathlib import Path
from typing import Any

import pytest

from evals.compare import Change, changes, compare_text, find_result, main


def score(right: int, wrong: int, missed: int) -> dict[str, Any]:
    found = right + wrong
    expected = right + missed
    return {
        "found_right": right,
        "found_wrong": wrong,
        "missed": missed,
        "precision": right / found if found else 1.0,
        "recall": right / expected if expected else 1.0,
    }


def result(
    label: str,
    created_at: str,
    wrong: list[list[str]],
    missed: list[list[str]],
    heading: dict[str, Any],
    prompt: str | None = "v1",
    complete: bool = True,
) -> dict[str, Any]:
    rules = {"off-brand-tone": score(7, 0, 0), "unclear-heading": heading}
    data: dict[str, Any] = {
        "label": label,
        "created_at": created_at,
        "ai": True,
        "complete": complete,
        "total": score(7 + heading["found_right"], heading["found_wrong"], heading["missed"]),
        "rules": rules,
        "sets": {"seeded": {"wrong": wrong, "missed": missed}},
        "claude_calls": 67,
        "cost_usd": "0.236575",
        "seconds": 401.4,
    }
    if prompt is not None:
        data["prompt"] = prompt
    return data


BEFORE = result(
    "prompt-v1",
    "2026-11-17T12:29:00+00:00",
    wrong=[["repairs.html", "unclear-heading"], ["safety.html", "unclear-heading"]],
    missed=[],
    heading=score(6, 2, 0),
    prompt=None,
)
AFTER = result(
    "prompt-v2",
    "2026-11-18T10:00:00+00:00",
    wrong=[["safety.html", "unclear-heading"], ["team.html", "unclear-heading"]],
    missed=[["visit.html", "unclear-heading"]],
    heading=score(5, 2, 1),
    prompt="v2",
)


def save(folder: Path, name: str, data: dict[str, Any]) -> Path:
    path = folder / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_a_label_finds_the_newest_run_with_it(tmp_path: Path) -> None:
    save(tmp_path, "a.json", {**BEFORE, "created_at": "2026-11-17T12:29:00+00:00", "seconds": 1})
    save(tmp_path, "b.json", {**BEFORE, "created_at": "2026-11-18T09:00:00+00:00", "seconds": 2})
    save(tmp_path, "c.json", AFTER)

    assert find_result("prompt-v1", tmp_path)["seconds"] == 2


def test_a_path_is_read_as_it_is(tmp_path: Path) -> None:
    path = save(tmp_path, "2026-11-18-1000-prompt-v2.json", AFTER)

    assert find_result(str(path), tmp_path / "elsewhere")["label"] == "prompt-v2"


def test_an_unknown_label_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="prompt-v9"):
        find_result("prompt-v9", tmp_path)


def test_changes_lists_each_finding_whose_outcome_moved() -> None:
    assert changes(BEFORE, AFTER) == [
        Change("fixed", "seeded/repairs.html", "unclear-heading"),
        Change("broke", "seeded/team.html", "unclear-heading"),
        Change("lost", "seeded/visit.html", "unclear-heading"),
    ]
    assert changes(AFTER, AFTER) == []


def test_report_shows_before_and_after() -> None:
    text = compare_text(BEFORE, AFTER)

    assert (
        "Before: prompt-v1 (2026-11-17 12:29, claude-opus-5-5, page prompt not recorded, code checks first)"
        in text
    )
    assert (
        "After:  prompt-v2 (2026-11-18 10:00, claude-opus-5-5, page prompt v2, code checks first)"
        in text
    )
    heading_row = next(line for line in text.splitlines() if line.startswith("unclear-heading"))
    assert "75% → 71%" in heading_row
    assert "100% → 83%" in heading_row
    assert "0 → 1" in heading_row
    tone_row = next(line for line in text.splitlines() if line.startswith("off-brand-tone"))
    assert "→" not in tone_row
    assert "no longer a false alarm  seeded/repairs.html: unclear-heading" in text
    assert "now missed" in text
    assert "new false alarm" in text
    assert "Cost: $0.2366 → $0.2366" in text
    assert "WARNING" not in text


def test_an_incomplete_run_is_flagged() -> None:
    text = compare_text(BEFORE, {**AFTER, "complete": False})

    assert (
        text.splitlines()[3]
        == "WARNING: the after run is incomplete, so its AI scores are not valid."
    )


def test_each_run_says_how_it_ran() -> None:
    text = compare_text(
        {**AFTER, "ai": False}, {**AFTER, "code_first": False, "model": "claude-haiku-5-5"}
    )

    assert "Before: prompt-v2 (2026-11-18 10:00, code checks only)" in text
    assert (
        "After:  prompt-v2 (2026-11-18 10:00, claude-haiku-5-5, page prompt v2, without the code checks' results)"
        in text
    )


def test_the_same_run_twice_changes_nothing() -> None:
    assert "No finding changed." in compare_text(AFTER, AFTER)


def test_main_needs_two_runs(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["prompt-v1"]) == 2
    assert "Usage" in capsys.readouterr().out
