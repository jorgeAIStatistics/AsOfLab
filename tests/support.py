"""Helpers shared by the test modules."""

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa

from asoflab.contract import FLOAT_ABS_TOL, FLOAT_REL_TOL
from asoflab.schema.models import EventVersion, FeatureConfig, PredictionRow, TimePolicy

# Every policy variant the engines are compared under.
POLICY_SLUGS = [
    "pipeline_as_known",
    "source_as_known",
    "latest_revised",
    "latest_revised-source",
    "naive_event_time",
    "pipeline_as_known-impute0s",
    "source_as_known-impute3600s",
]
POLICIES = [TimePolicy.parse(slug) for slug in POLICY_SLUGS]

COUNTEREXAMPLE_DIR = Path(__file__).parent / "counterexamples"


def same_value(got: Any, want: Any) -> bool:
    if got is None or want is None:
        return got is None and want is None
    return math.isclose(got, want, rel_tol=FLOAT_REL_TOL, abs_tol=FLOAT_ABS_TOL)


def differences_from_expected(
    table: pa.Table, expected: dict[str, dict[str, float | int | None]]
) -> list[str]:
    """Human-readable differences between a feature table and hand-calculated values."""
    rows = {row["prediction_id"]: row for row in table.to_pylist()}
    problems = []
    if sorted(rows) != sorted(expected):
        problems.append(f"prediction ids {sorted(rows)} != expected {sorted(expected)}")
    for prediction_id, features in expected.items():
        row = rows.get(prediction_id)
        if row is None:
            continue
        for name, want in features.items():
            if not same_value(row[name], want):
                problems.append(f"{prediction_id}.{name}: got {row[name]!r}, expected {want!r}")
    return problems


def save_counterexample(
    name: str,
    events: Sequence[EventVersion],
    spine: Sequence[PredictionRow],
    config: FeatureConfig,
    policy: TimePolicy,
    detail: dict[str, Any],
) -> Path:
    """Persist a failing synthetic case so it can be inspected and turned into a fixture.

    Hypothesis replays the smallest failing example last, so the file left
    behind after a failed run is the minimal counterexample.
    """
    COUNTEREXAMPLE_DIR.mkdir(exist_ok=True)
    path = COUNTEREXAMPLE_DIR / f"{name}.json"
    document = {
        "name": name,
        "policy": policy.slug(),
        "detail": detail,
        "features": config.canonical()["features"],
        "events": [event.model_dump(mode="json") for event in events],
        "spine": [row.model_dump(mode="json") for row in spine],
    }
    path.write_text(json.dumps(document, indent=2, sort_keys=True))
    return path


# --- engines, all with one signature: records in, feature table out -------------------------

from asoflab.batch import compute_features as _batch_features  # noqa: E402
from asoflab.oracle import compute_features as _oracle_features  # noqa: E402
from asoflab.replay import replay_features as _replay_features  # noqa: E402
from asoflab.schema.tables import (  # noqa: E402
    events_to_table,
    feature_rows_to_table,
    spine_to_table,
)


def run_oracle(
    events: Sequence[EventVersion],
    spine: Sequence[PredictionRow],
    config: FeatureConfig,
    policy: TimePolicy,
) -> pa.Table:
    return feature_rows_to_table(_oracle_features(events, spine, config, policy), config)


def run_batch(
    events: Sequence[EventVersion],
    spine: Sequence[PredictionRow],
    config: FeatureConfig,
    policy: TimePolicy,
) -> pa.Table:
    return _batch_features(events_to_table(events), spine_to_table(spine), config, policy)


def run_replay(
    events: Sequence[EventVersion],
    spine: Sequence[PredictionRow],
    config: FeatureConfig,
    policy: TimePolicy,
    **options: Any,
) -> pa.Table:
    return _replay_features(
        events_to_table(events), spine_to_table(spine), config, policy, **options
    )


ENGINES = {"oracle": run_oracle, "batch": run_batch, "replay": run_replay}
