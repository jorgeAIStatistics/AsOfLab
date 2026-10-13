"""Summarise a set of runs across seeds, without choosing among them.

Each seed is a separately simulated cohort with its own fitted models. The
spread across seeds therefore includes what a single run's bootstrap interval
leaves out: the variability of the cohort and of model fitting. With five
seeds this is a description of five simulated worlds, not a confidence interval.
"""

import json
import statistics as stats
from pathlib import Path
from typing import Any

from asoflab.audit.environment import utc_now

METRICS = ("auroc", "average_precision", "brier")

# (key in the summary, experiment block, difference name)
CONTRASTS = [
    ("naive_minus_as_known", "experiment_a", "naive_event_time_minus_pipeline_as_known"),
    ("source_minus_pipeline", "experiment_a", "source_as_known_minus_pipeline_as_known"),
    ("revision_effect", "experiment_a", "revision_effect"),
    ("availability_effect", "experiment_a", "availability_effect"),
    ("optimism_of_the_naive_pipeline", "experiment_b", "optimism_of_the_naive_pipeline"),
    (
        "as_known_training_minus_naive_training_at_deployment",
        "experiment_b",
        "as_known_training_minus_naive_training_at_deployment",
    ),
]


def load_runs(root: str | Path) -> list[dict[str, Any]]:
    runs = []
    for manifest_path in sorted(Path(root).glob("*/manifest.json")):
        directory = manifest_path.parent
        runs.append(
            {
                "directory": directory.name,
                "manifest": json.loads(manifest_path.read_text()),
                "statistics": json.loads((directory / "statistics.json").read_text()),
                "audit": json.loads((directory / "audit.json").read_text()),
            }
        )
    return runs


def _spread(values: list[float]) -> dict[str, Any]:
    return {
        "seeds": len(values),
        "mean": stats.fmean(values),
        "sd": stats.stdev(values) if len(values) > 1 else None,
        "min": min(values),
        "max": max(values),
    }


def _sign_counts(intervals: list[dict[str, Any]]) -> dict[str, int]:
    """How many seeds' 95% intervals lie wholly above zero, wholly below, or include it."""
    above = sum(1 for i in intervals if i.get("lower") is not None and i["lower"] > 0)
    below = sum(1 for i in intervals if i.get("upper") is not None and i["upper"] < 0)
    missing = sum(1 for i in intervals if i.get("lower") is None)
    return {
        "interval_above_zero": above,
        "interval_below_zero": below,
        "interval_includes_zero": len(intervals) - above - below - missing,
        "no_interval": missing,
    }


def summarise(root: str | Path) -> dict[str, Any]:
    """Per-scenario, per-seed values and their spread. Every run found under `root` is included."""
    runs = load_runs(root)
    scenarios: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        scenarios.setdefault(run["manifest"]["scenario"], []).append(run)

    summary: dict[str, Any] = {}
    for scenario, members in scenarios.items():
        members.sort(key=lambda run: run["manifest"]["seed"])
        per_seed = []
        for run in members:
            statistics, audit = run["statistics"], run["audit"]
            differential = audit["differential"]
            reference = statistics["experiment_a"]["prediction_weighted"]["conditions"][
                "pipeline_as_known"
            ]
            maturity = statistics["label_maturity"]
            timing = maturity["evaluation_against_impatient_labels"]
            entry: dict[str, Any] = {
                "seed": run["manifest"]["seed"],
                "run": run["directory"],
                "code_commit": run["manifest"]["environment"].get("code_commit"),
                "code_dirty": run["manifest"]["environment"].get("code_dirty"),
                "test_sample": statistics["experiment_a"]["sample"],
                "reference_metrics": {metric: reference[metric] for metric in METRICS},
                "share_of_predictions_with_a_finding": differential["predictions_with_findings"]
                / max(differential["predictions"], 1),
                "findings_by_type": {
                    name: item["count"] for name, item in differential["by_type"].items()
                },
                "premature_negatives": maturity["premature_negatives_if_labelled_at_the_horizon"],
                "positive_labels": statistics["label_coverage"]["by_status"]["positive"],
                "label_timing_contrast": timing["prediction_weighted"]["differences"][
                    "recorded_at_horizon_minus_mature"
                ],
                "engines_agree": all(
                    item["equivalent"]
                    for group in audit["parity"].values()
                    for item in group.values()
                ),
                "contrasts": {},
            }
            for key, experiment, name in CONTRASTS:
                block = statistics[experiment]["prediction_weighted"]["differences"][name]
                entry["contrasts"][key] = {metric: block[metric] for metric in METRICS}
            per_seed.append(entry)

        across: dict[str, Any] = {}
        for key, _, _ in CONTRASTS:
            across[key] = {}
            for metric in METRICS:
                intervals = [entry["contrasts"][key][metric] for entry in per_seed]
                points = [
                    interval["point"] for interval in intervals if interval["point"] is not None
                ]
                across[key][metric] = (
                    {**_spread(points), **_sign_counts(intervals)} if points else None
                )
        timing_points = [e["label_timing_contrast"]["auroc"]["point"] for e in per_seed]
        summary[scenario] = {
            "description": members[0]["statistics"]["scenario"]["description"],
            "seeds": [entry["seed"] for entry in per_seed],
            "per_seed": per_seed,
            "across_seeds": {
                "contrasts": across,
                "share_of_predictions_with_a_finding": _spread(
                    [entry["share_of_predictions_with_a_finding"] for entry in per_seed]
                ),
                "premature_negative_share_of_positives": _spread(
                    [
                        entry["premature_negatives"] / max(entry["positive_labels"], 1)
                        for entry in per_seed
                    ]
                ),
                "label_timing_auroc_contrast": _spread([p for p in timing_points if p is not None]),
                "reference_auroc": _spread(
                    [
                        e["reference_metrics"]["auroc"]["point"]
                        for e in per_seed
                        if e["reference_metrics"]["auroc"]["point"] is not None
                    ]
                ),
                "all_engines_agree": all(entry["engines_agree"] for entry in per_seed),
            },
        }
    return {
        "generated_at": utc_now(),
        "synthetic_data": True,
        "runs_included": len(runs),
        "selection": "Every run found under the results directory is included. None was dropped.",
        "interpretation": "Each seed is one simulated cohort with its own fitted models. The spread "
        "across seeds describes these simulated cohorts; it is not a confidence interval and says "
        "nothing about any real system.",
        "scenarios": summary,
    }
