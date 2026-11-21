# Eval answer key reader
# Reads demo-site/eval-answer-key.toml: the rules a perfect run reports on each page.

import tomllib
from pathlib import Path

ANSWER_KEY = Path(__file__).resolve().parents[2] / "demo-site" / "eval-answer-key.toml"

EVAL_SETS: tuple[str, ...] = ("clean", "seeded", "injection")

AnswerKey = dict[str, dict[str, frozenset[str]]]


def load_answer_key(path: Path = ANSWER_KEY) -> AnswerKey:
    with path.open("rb") as file:
        data = tomllib.load(file)

    missing = [name for name in EVAL_SETS if name not in data]
    if missing:
        raise ValueError(f"{path.name} has no section for: {', '.join(missing)}")

    key: AnswerKey = {}
    for name in EVAL_SETS:
        key[name] = {}
        for page, rules in data[name].items():
            if not isinstance(rules, list) or not all(isinstance(rule, str) for rule in rules):
                raise ValueError(f"{path.name}: [{name}] {page} must be a list of rule names")
            key[name][page] = frozenset(rules)
    return key
