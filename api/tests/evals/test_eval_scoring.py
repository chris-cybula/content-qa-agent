# Tests: eval scoring

from evals.scoring import RuleScore, combine, score_set

EXPECTED = {
    "news.html": frozenset({"off-brand-tone", "weak-meta-description"}),
    "faq.html": frozenset({"unclear-heading"}),
    "index.html": frozenset(),
}


def test_a_perfect_run_scores_100_percent_on_every_page() -> None:
    found = {
        ("news.html", "off-brand-tone"),
        ("news.html", "weak-meta-description"),
        ("faq.html", "unclear-heading"),
    }

    score = score_set(EXPECTED, found)

    assert (score.total.precision, score.total.recall) == (1.0, 1.0)
    assert (score.pages_exact, score.pages) == (3, 3)
    assert (score.wrong, score.missed) == ([], [])


def test_wrong_and_missed_findings_are_counted_and_listed() -> None:
    found = {
        ("news.html", "off-brand-tone"),
        ("faq.html", "unclear-heading"),
        ("index.html", "unclear-heading"),
    }

    score = score_set(EXPECTED, found)

    assert score.wrong == [("index.html", "unclear-heading")]
    assert score.missed == [("news.html", "weak-meta-description")]
    assert (
        score.rules["unclear-heading"].found_right,
        score.rules["unclear-heading"].found_wrong,
    ) == (1, 1)
    assert score.rules["weak-meta-description"].missed == 1
    assert score.pages_exact == 1


def test_precision_and_recall_come_from_the_counts() -> None:
    score = RuleScore(found_right=3, found_wrong=1, missed=3)

    assert (score.precision, score.recall) == (0.75, 0.5)


def test_nothing_reported_and_nothing_expected_counts_as_perfect() -> None:
    assert (RuleScore().precision, RuleScore().recall) == (1.0, 1.0)


def test_sets_combine_rule_by_rule() -> None:
    first = score_set({"a.html": frozenset({"missing-alt"})}, {("a.html", "missing-alt")})
    second = score_set({"b.html": frozenset()}, {("b.html", "missing-alt")})

    combined = combine({"seeded": first, "clean": second})

    assert combined["missing-alt"].as_dict() == {
        "found_right": 1,
        "found_wrong": 1,
        "missed": 0,
        "precision": 0.5,
        "recall": 1.0,
    }
