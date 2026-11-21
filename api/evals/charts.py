# Eval charts
# Turns saved eval results into tables and charts for evals/report.ipynb.

import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from evals.run_evals import RESULTS_DIR

PRECISION_COLOUR = "#2f55d4"
RECALL_COLOUR = "#277a4c"
COST_COLOUR = "#8a5a00"


def load_results(results_dir: Path = RESULTS_DIR) -> list[dict[str, Any]]:
    results = []
    for path in sorted(results_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["file"] = path.name
        results.append(data)
    return sorted(results, key=lambda result: result["created_at"])


def usable(result: dict[str, Any]) -> bool:
    return bool(result["ai"]) and result.get("complete", True)


def latest_full_run(results: list[dict[str, Any]]) -> dict[str, Any]:
    full = [result for result in results if usable(result)]
    if not full:
        raise ValueError(
            "No complete AI eval yet. Run: python -m evals.run_evals --ai --label <name>"
        )
    return full[-1]


def runs_table(results: list[dict[str, Any]]) -> pd.DataFrame:
    rows = [
        {
            "label": result["label"],
            "created": result["created_at"][:16].replace("T", " "),
            "ai": result["ai"],
            "complete": result.get("complete", True),
            "precision": result["total"]["precision"],
            "recall": result["total"]["recall"],
            "wrong": result["total"]["found_wrong"],
            "missed": result["total"]["missed"],
            "claude_calls": result["claude_calls"],
            "cost_usd": float(result["cost_usd"]),
            "minutes": round(result["seconds"] / 60, 1),
        }
        for result in results
    ]
    return pd.DataFrame(rows)


def rules_table(result: dict[str, Any]) -> pd.DataFrame:
    rows = [
        {
            "rule": rule,
            "precision": score["precision"],
            "recall": score["recall"],
            "right": score["found_right"],
            "wrong": score["found_wrong"],
            "missed": score["missed"],
        }
        for rule, score in result["rules"].items()
    ]
    table = pd.DataFrame(rows)
    table["weakest"] = table[["precision", "recall"]].min(axis=1)
    return table.sort_values(["weakest", "rule"]).drop(columns="weakest").reset_index(drop=True)


def sets_table(result: dict[str, Any]) -> pd.DataFrame:
    rows = [
        {
            "set": name,
            "run_id": data["run_id"],
            "pages": data["pages"],
            "exact": data["pages_exact"],
            "wrong": data["total"]["found_wrong"],
            "missed": data["total"]["missed"],
            "precision": data["total"]["precision"],
            "recall": data["total"]["recall"],
        }
        for name, data in result["sets"].items()
    ]
    return pd.DataFrame(rows)


def mistakes_table(result: dict[str, Any]) -> pd.DataFrame:
    rows = [
        {"set": name, "page": page, "rule": rule, "kind": kind}
        for name, data in result["sets"].items()
        for kind in ("wrong", "missed")
        for page, rule in data[kind]
    ]
    return pd.DataFrame(rows, columns=["set", "page", "rule", "kind"])


def bar_pair(ax: Axes, labels: list[str], precision: list[float], recall: list[float]) -> None:
    positions = range(len(labels))
    width = 0.38
    ax.bar(
        [x - width / 2 for x in positions],
        precision,
        width,
        label="Precision",
        color=PRECISION_COLOUR,
    )
    ax.bar([x + width / 2 for x in positions], recall, width, label="Recall", color=RECALL_COLOUR)
    ax.set_xticks(list(positions), labels, rotation=30, ha="right")
    ax.set_ylim(0, 1.05)
    ax.yaxis.set_major_formatter(lambda value, _: f"{value:.0%}")
    ax.legend(loc="lower right", bbox_to_anchor=(1, 1), ncol=2, frameon=False)
    ax.grid(axis="y", alpha=0.3)


def plot_rules(result: dict[str, Any]) -> Figure:
    table = rules_table(result).sort_values("rule")
    figure, ax = plt.subplots(figsize=(11, 4))
    bar_pair(ax, list(table["rule"]), list(table["precision"]), list(table["recall"]))
    ax.set_title(f"Precision and recall per rule · {result['label']}", loc="left")
    figure.tight_layout()
    return figure


def plot_sets(result: dict[str, Any]) -> Figure:
    table = sets_table(result)
    figure, ax = plt.subplots(figsize=(7, 3.5))
    positions = range(len(table))
    width = 0.38
    ax.bar(
        [x - width / 2 for x in positions],
        table["wrong"],
        width,
        label="Wrong (false alarms)",
        color=COST_COLOUR,
    )
    ax.bar(
        [x + width / 2 for x in positions],
        table["missed"],
        width,
        label="Missed",
        color=PRECISION_COLOUR,
    )
    ax.set_xticks(
        list(positions),
        [
            f"{name}\n{exact}/{pages} exact"
            for name, exact, pages in zip(table["set"], table["exact"], table["pages"], strict=True)
        ],
    )
    locator = ax.yaxis.get_major_locator()
    if isinstance(locator, MaxNLocator):
        locator.set_params(integer=True)
    ax.set_title(f"Mistakes per eval set · {result['label']}")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    figure.tight_layout()
    return figure


def plot_runs(results: list[dict[str, Any]]) -> Figure:
    table = runs_table([result for result in results if usable(result)])
    figure, ax = plt.subplots(figsize=(8, 3.5))
    bar_pair(ax, list(table["label"]), list(table["precision"]), list(table["recall"]))
    ax.set_title("Runs compared", loc="left")
    figure.tight_layout()
    return figure


def plot_cost_vs_quality(results: list[dict[str, Any]]) -> Figure:
    table = runs_table([result for result in results if usable(result)])
    total = table["precision"] + table["recall"]
    table["f1"] = (2 * table["precision"] * table["recall"] / total).where(total > 0, 0.0)

    figure, ax = plt.subplots(figsize=(6, 4))
    ax.scatter(table["cost_usd"], table["f1"], s=80, color=PRECISION_COLOUR)
    for _, row in table.iterrows():
        ax.annotate(
            row["label"], (row["cost_usd"], row["f1"]), textcoords="offset points", xytext=(6, 6)
        )
    ax.set_xlabel("Cost of one full eval run (USD)")
    ax.set_ylabel("F1 (precision and recall combined)")
    ax.set_ylim(0, 1.05)
    ax.set_xlim(0, max(table["cost_usd"].max(), 0.01) * 1.4)
    ax.set_title("Cost against quality")
    ax.grid(alpha=0.3)
    figure.tight_layout()
    return figure
