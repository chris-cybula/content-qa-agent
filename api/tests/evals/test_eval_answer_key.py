# Tests: eval answer key matches the pages on disk

from pathlib import Path

import pytest
from defusedxml import ElementTree

from app.checks.ai_findings import AI_RULES
from evals.answer_key import ANSWER_KEY, EVAL_SETS, load_answer_key

EVALS = Path(__file__).resolve().parents[3] / "demo-site" / "public" / "evals"

CODE_RULES = {
    "missing-alt",
    "alt-is-filename",
    "heading-order",
    "multiple-h1",
    "missing-h1",
    "missing-meta-description",
    "meta-too-short",
    "meta-too-long",
    "vague-link-text",
    "empty-link-text",
    "broken-link",
}

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def sitemap_pages(name: str) -> list[str]:
    root = ElementTree.parse(EVALS / name / "sitemap.xml").getroot()
    return [(loc.text or "").rsplit("/", 1)[-1] for loc in root.iter(f"{SITEMAP_NS}loc")]


@pytest.mark.parametrize("name", EVAL_SETS)
def test_key_sitemap_and_folder_list_the_same_pages(name: str) -> None:
    on_disk = sorted(path.name for path in (EVALS / name).glob("*.html"))

    assert sorted(sitemap_pages(name)) == on_disk
    assert sorted(load_answer_key()[name]) == on_disk


def test_set_sizes() -> None:
    key = load_answer_key()

    assert (len(key["clean"]), len(key["seeded"])) == (20, 30)
    assert 5 <= len(key["injection"]) <= 10


def test_clean_pages_expect_nothing_and_seeded_pages_expect_something() -> None:
    key = load_answer_key()

    assert all(rules == frozenset() for rules in key["clean"].values())
    assert all(rules for rules in key["seeded"].values())


def test_only_real_rule_names_are_used() -> None:
    used = {
        rule for pages in load_answer_key().values() for rules in pages.values() for rule in rules
    }

    assert used <= CODE_RULES | set(AI_RULES)


def test_seeded_set_plants_every_rule() -> None:
    planted = {rule for rules in load_answer_key()["seeded"].values() for rule in rules}

    assert planted == CODE_RULES | set(AI_RULES)


def test_injection_set_has_a_page_that_must_stay_clean() -> None:
    assert frozenset() in load_answer_key()["injection"].values()


def test_a_missing_section_is_an_error(tmp_path: Path) -> None:
    broken = tmp_path / "key.toml"
    broken.write_text(
        '[clean]\n"a.html" = []\n[seeded]\n"b.html" = ["missing-alt"]\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="no section for: injection"):
        load_answer_key(broken)


def test_the_real_key_file_is_the_default() -> None:
    assert ANSWER_KEY.name == "eval-answer-key.toml"
    assert ANSWER_KEY.is_file()
