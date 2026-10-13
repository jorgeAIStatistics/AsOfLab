"""Compare AsOfLab with Feast on the semantics the two share, and label what they do not share.

    uv run python integrations/feast/run_comparison.py

Run it from the repository root, in the AsOfLab environment. The Feast half
runs in its own environment (integrations/feast/.venv) because Feast pins a
different dependency set; the two sides exchange Parquet and JSON files.

Shared semantics. Feast's point-in-time join returns, for each entity row at
time T, the feature row with the latest event timestamp at or before T inside
the view's time-to-live. With `filter_by_created_timestamp=True` it also
requires created <= T. For a single event type with no revisions that is the
same request as AsOfLab's `last` aggregation:

    Feast default                      <->  AsOfLab naive_event_time
    Feast filter_by_created_timestamp  <->  AsOfLab pipeline_as_known

Not shared. Feast rows have no logical-event identity, revision order or
retraction marker. A correction that keeps its event time is covered by
Feast's "highest created timestamp wins among rows with the same event
timestamp". A correction that moves the event time, a retraction, and a lower
revision delivered late are outside Feast's model. Differences there are
expected and are reported as unsupported semantics, not as defects.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

from asoflab.audit.environment import describe_environment, utc_now  # noqa: E402
from asoflab.batch import compute_features  # noqa: E402
from asoflab.schema.models import FeatureConfig, TimePolicy  # noqa: E402
from asoflab.simulation.workload import WorkloadSpec, generate_workload  # noqa: E402

EVENT_TYPE = "lab.lactate"
WINDOW_SECONDS = 24 * 3600
CONFIG = FeatureConfig(
    features=[{"name": "last_value", "event_type": EVENT_TYPE, "aggregation": "last", "window_seconds": WINDOW_SECONDS}]
)
CLEAN = {"duplicate_rate": 0.0, "unknown_availability_rate": 0.0, "late_rate": 0.05}
CASES: dict[str, dict[str, Any]] = {
    "delays_only": {
        "shared": True,
        "what": "Results arrive after a delay. No corrections, no retractions.",
        "spec": {**CLEAN, "correction_rate": 0.0, "retraction_rate": 0.0},
    },
    "corrections_in_arrival_order": {
        "shared": True,
        "what": "Values are corrected later. The event time never changes and revisions arrive in order.",
        "spec": {**CLEAN, "correction_rate": 0.15, "retraction_rate": 0.0, "event_time_change_rate": 0.0, "out_of_order_rate": 0.0},
    },
    "revisions_outside_feasts_model": {
        "shared": False,
        "what": "Retractions, corrections that move the event time, and lower revisions delivered late.",
        "spec": {**CLEAN, "correction_rate": 0.15, "retraction_rate": 0.05, "event_time_change_rate": 0.5, "out_of_order_rate": 0.3},
    },
}


def run_case(name: str, case: dict[str, Any], stores: list[str]) -> dict[str, Any]:
    events, spine = generate_workload(WorkloadSpec(n_events=20_000, seed=20261, events_per_entity=100, **case["spec"]))
    events = events.filter(pc.equal(events.column("event_type"), EVENT_TYPE))
    asoflab = {
        slug: dict(
            zip(
                (table := compute_features(events, spine, CONFIG, TimePolicy.parse(slug))).column("prediction_id").to_pylist(),
                table.column("last_value").to_pylist(),
                strict=True,
            )
        )
        for slug in ("naive_event_time", "pipeline_as_known")
    }
    # One Feast row per upsert version. Retractions have no representation in a Feast source.
    upserts = events.filter(pc.equal(events.column("operation"), "upsert"))
    feature_rows = pa.table(
        {
            "patient_id": upserts.column("entity_id"),
            "event_timestamp": upserts.column("event_time"),
            "created": upserts.column("pipeline_available_time"),
            "value": upserts.column("value"),
        }
    )
    entity_rows = pa.table(
        {
            "prediction_id": spine.column("prediction_id"),
            "patient_id": spine.column("entity_id"),
            "event_timestamp": spine.column("prediction_time"),
        }
    )
    # (entity, event time) -> the values of every version recorded at that instant
    values_at: dict[tuple[str, Any], set[float]] = {}
    for entity_id, event_time, value in zip(
        upserts.column("entity_id").to_pylist(), upserts.column("event_time").to_pylist(),
        upserts.column("value").to_pylist(), strict=True,
    ):  # fmt: skip
        values_at.setdefault((entity_id, event_time), set()).add(value)
    entity_of = dict(zip(spine.column("prediction_id").to_pylist(), spine.column("entity_id").to_pylist(), strict=True))

    def same_instant_other_version(prediction_id: str, feast_value: float | None, expected: float | None) -> bool:
        """Did Feast return a different version recorded at the same event timestamp as AsOfLab's?"""
        entity_id = entity_of[prediction_id]
        return any(
            key[0] == entity_id and feast_value in values and expected in values for key, values in values_at.items()
        )

    outcome: dict[str, Any] = {
        "what": case["what"],
        "within_shared_semantics": case["shared"],
        "event_versions": events.num_rows,
        "retractions_not_representable_in_feast": events.num_rows - upserts.num_rows,
        "predictions": spine.num_rows,
        "stores": {},
    }
    with tempfile.TemporaryDirectory(prefix="asoflab-feast-") as temporary:
        work = Path(temporary)
        pq.write_table(feature_rows, work / "feature_rows.parquet")
        pq.write_table(entity_rows, work / "entity_rows.parquet")
        for store in stores:
            done = subprocess.run(
                [str(HERE / ".venv" / "bin" / "python"), str(HERE / "feast_side.py"), str(work), str(WINDOW_SECONDS), store],
                capture_output=True, text=True, check=False,
            )  # fmt: skip
            if done.returncode != 0:
                outcome["stores"][store] = {"status": "failed", "reason": done.stderr.strip().splitlines()[-1][:300]}
                continue
            result = json.loads((work / f"feast_result_{store}.json").read_text())
            outcome["feast_version"] = result["feast_version"]
            outcome["stores"][store] = {"status": "ok", "modes": {}}
            for mode, slug in (("default", "naive_event_time"), ("filter_by_created_timestamp", "pipeline_as_known")):
                feast_values = result["modes"][mode]["values"]
                expected = asoflab[slug]
                missing = [pid for pid in expected if pid not in feast_values]
                differing = [
                    pid for pid in expected if pid in feast_values and feast_values[pid] != expected[pid]
                ]
                absent_but_null = sum(1 for pid in missing if expected[pid] is None)
                outcome["stores"][store]["modes"][mode] = {
                    "compared_with_asoflab_policy": slug,
                    "feast_rows_returned": result["modes"][mode]["rows_returned"],
                    "predictions_with_equal_value": len(expected) - len(missing) - len(differing),
                    "predictions_with_a_different_value": len(differing),
                    "differences_that_are_another_version_at_the_same_event_timestamp": sum(
                        same_instant_other_version(pid, feast_values[pid], expected[pid]) for pid in differing
                    ),
                    "predictions_absent_from_feast_output": len(missing),
                    "absent_predictions_where_asoflab_has_no_value": absent_but_null,
                    "examples_of_differences": [
                        {"prediction_id": pid, "feast": feast_values[pid], "asoflab": expected[pid]} for pid in differing[:5]
                    ],
                }
    return outcome


def main() -> None:
    if not (HERE / ".venv" / "bin" / "python").exists():
        sys.exit("The Feast environment is missing. Run: uv sync --project integrations/feast --frozen")
    stores = ["duckdb", "dask"]
    cases = {name: run_case(name, case, stores) for name, case in CASES.items()}
    for name, case in cases.items():
        for store, entry in case["stores"].items():
            if entry["status"] != "ok":
                print(f"{name} [{store}]: {entry['status']} ({entry['reason']})")
                continue
            for mode, numbers in entry["modes"].items():
                print(
                    f"{name} [{store}] {mode}: equal {numbers['predictions_with_equal_value']}, different "
                    f"{numbers['predictions_with_a_different_value']} (same-instant other version: "
                    f"{numbers['differences_that_are_another_version_at_the_same_event_timestamp']}), "
                    f"absent {numbers['predictions_absent_from_feast_output']} "
                    f"(of which AsOfLab also has no value: {numbers['absent_predictions_where_asoflab_has_no_value']})"
                )
    report = {
        "generated_at": utc_now(),
        "command": "uv run python integrations/feast/run_comparison.py",
        "synthetic_data": True,
        "environment": describe_environment(),
        "scope": "One feature ('last' value of one event type, 24 h window), Feast local file sources, "
        "synthetic data. Agreement is expected only inside the shared semantics. Differences in the "
        "case marked outside the shared semantics are unsupported semantics, not defects in Feast.",
        "mapping": {
            "feast default": "asoflab naive_event_time",
            "feast filter_by_created_timestamp=True": "asoflab pipeline_as_known",
            "feast event_timestamp": "event_time of each upsert version",
            "feast created_timestamp_column": "pipeline_available_time of each version",
            "feast ttl": "the feature's lookback window",
        },
        "cases": cases,
    }
    target = ROOT / "results" / "interop" / "feast.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
