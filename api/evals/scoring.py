# Eval scoring
# Precision and recall per rule and per eval set, against the answer key.

from collections import defaultdict
from dataclasses import asdict, dataclass, field

PageRule = tuple[str, str]


@dataclass
class RuleScore:
    found_right: int = 0

    found_wrong: int = 0

    missed: int = 0

    @property
    def precision(self) -> float:
        reported = self.found_right + self.found_wrong
        return self.found_right / reported if reported else 1.0

    @property
    def recall(self) -> float:
        expected = self.found_right + self.missed
        return self.found_right / expected if expected else 1.0

    def add(self, other: "RuleScore") -> None:
        self.found_right += other.found_right
        self.found_wrong += other.found_wrong
        self.missed += other.missed

    def as_dict(self) -> dict[str, float | int]:
        return {
            **asdict(self),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
        }


@dataclass
class SetScore:
    rules: dict[str, RuleScore] = field(default_factory=dict)

    total: RuleScore = field(default_factory=RuleScore)

    wrong: list[PageRule] = field(default_factory=list)

    missed: list[PageRule] = field(default_factory=list)

    pages_exact: int = 0

    pages: int = 0


def score_set(expected: dict[str, frozenset[str]], found: set[PageRule]) -> SetScore:
    expected_pairs = {(page, rule) for page, rules in expected.items() for rule in rules}
    result = SetScore(pages=len(expected))
    rules: dict[str, RuleScore] = defaultdict(RuleScore)

    for _, rule in found & expected_pairs:
        rules[rule].found_right += 1
    for pair in sorted(found - expected_pairs):
        rules[pair[1]].found_wrong += 1
        result.wrong.append(pair)
    for pair in sorted(expected_pairs - found):
        rules[pair[1]].missed += 1
        result.missed.append(pair)

    for page, rules_expected in expected.items():
        if {rule for found_page, rule in found if found_page == page} == rules_expected:
            result.pages_exact += 1

    result.rules = dict(sorted(rules.items()))
    for rule_score in result.rules.values():
        result.total.add(rule_score)
    return result


def combine(sets: dict[str, SetScore]) -> dict[str, RuleScore]:
    combined: dict[str, RuleScore] = defaultdict(RuleScore)
    for set_score in sets.values():
        for rule, rule_score in set_score.rules.items():
            combined[rule].add(rule_score)
    return dict(sorted(combined.items()))
