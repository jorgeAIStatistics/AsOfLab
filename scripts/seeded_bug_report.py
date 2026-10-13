"""Measure how well the validation suite catches deliberately wrong implementations.

Writes results/validation/seeded_bugs.json with two things for every seeded bug:

1. Which handwritten fixtures contradict it (the kill matrix).
2. How many randomly generated archives were needed before one exposed it,
   over several independent random seeds, and the minimal counterexample
   Hypothesis shrank the first failure to.

Run from the repository root:

    uv run python scripts/seeded_bug_report.py --trials 10

Nothing here is tuned to a target. If a bug is not found within the budget the
miss is recorded as a miss.
"""

import argparse
import json
import random
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "tests" / "property", ROOT / "tests" / "unit"):
    sys.path.insert(0, str(extra))

from hypothesis import find, settings  # noqa: E402
from hypothesis.errors import NoSuchExample  # noqa: E402
from test_bug_discovery import POLICY, disagrees_with_oracle  # noqa: E402
from test_seeded_bugs import killers  # noqa: E402
from test_seeded_sql_bugs import SQL_MUTATIONS, exposing_fixtures  # noqa: E402

from asoflab.audit.environment import describe_environment, utc_now  # noqa: E402
from seeded_bugs import CONTROL, DESCRIPTIONS, SEEDED_BUGS  # noqa: E402
from strategies import archives  # noqa: E402


def discover(compute: Any, seed: int, budget: int) -> tuple[int | None, Any]:
    """Number of archives generated before the first one that exposes `compute`, and the shrunk example."""
    calls = {"count": 0, "first": None}

    def condition(archive: Any) -> bool:
        calls["count"] += 1
        exposed = disagrees_with_oracle(compute, archive)
        if exposed and calls["first"] is None:
            calls["first"] = calls["count"]
        return exposed

    try:
        minimal = find(
            archives(),
            condition,
            settings=settings(max_examples=budget, database=None, deadline=None),
            random=random.Random(seed),
        )
    except NoSuchExample:
        return None, None
    return calls["first"], minimal


def archive_to_dict(archive: Any) -> dict[str, Any]:
    return {
        "policy": POLICY.slug(),
        "features": archive.config.canonical()["features"],
        "events": [event.model_dump(mode="json", exclude_none=True) for event in archive.events],
        "spine": [row.model_dump(mode="json", exclude_none=True) for row in archive.spine],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--budget", type=int, default=4000)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results" / "validation" / "seeded_bugs.json"
    )
    arguments = parser.parse_args()

    bugs = {}
    for name, compute in sorted(SEEDED_BUGS.items()):
        first_hits = []
        minimal = None
        for seed in range(arguments.trials):
            first, example = discover(compute, seed, arguments.budget)
            first_hits.append(first)
            if minimal is None and example is not None:
                minimal = example
        found = [hit for hit in first_hits if hit is not None]
        bugs[name] = {
            "what_it_gets_wrong": DESCRIPTIONS[name],
            "fixtures_that_expose_it": killers(compute),
            "discovery": {
                "trials": arguments.trials,
                "found_in_trials": len(found),
                "archives_until_first_exposure": first_hits,
                "median": statistics.median(found) if found else None,
                "worst": max(found) if found else None,
            },
            "minimal_counterexample": archive_to_dict(minimal) if minimal is not None else None,
        }
        print(
            f"{name:32s} found {len(found)}/{arguments.trials}  median {bugs[name]['discovery']['median']}  worst {bugs[name]['discovery']['worst']}"
        )

    control_first, _ = discover(CONTROL, 0, arguments.budget)
    report = {
        "generated_at": utc_now(),
        "command": "uv run python scripts/seeded_bug_report.py "
        f"--trials {arguments.trials} --budget {arguments.budget}",
        "environment": describe_environment(),
        "scope": "Synthetic archives from tests/strategies.py, policy pipeline_as_known. "
        "A seeded bug is one deliberate mistake in an otherwise correct implementation.",
        "archive_budget_per_trial": arguments.budget,
        "control": {
            "fixtures_that_contradict_it": killers(CONTROL),
            "exposed_within_budget": control_first is not None,
        },
        "python_bugs": bugs,
        "sql_mutations": {
            name: {
                "replaced": old,
                "with": new,
                "fixtures_that_expose_it": exposing_fixtures(old, new),
            }
            for name, (old, new, _) in sorted(SQL_MUTATIONS.items())
        },
    }
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {arguments.out}")


if __name__ == "__main__":
    main()
