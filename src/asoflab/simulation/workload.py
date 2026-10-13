"""Synthetic engineering workload: a large, messy event archive with no clinical meaning.

This generator exists to exercise and time the engines. It produces the
timing structure that makes replay hard (delays, late arrivals, corrections,
retractions, out-of-order revisions, unknown availability) at any size, with
either even or heavily skewed activity per entity. Values are random numbers.

It is not the clinical simulation in `asoflab.simulation.clinical`, and nothing
statistical should be concluded from its output.
"""

from dataclasses import asdict, dataclass
from typing import Any, Literal

import numpy as np
import pyarrow as pa

from asoflab.schema.models import FeatureConfig
from asoflab.schema.tables import EVENT_SCHEMA, SPINE_SCHEMA, TIMESTAMP

WORKLOAD_GENERATOR_VERSION = "1"
_US = 1_000_000
_HOUR_US = 3_600 * _US
_START_US = 1_735_689_600 * _US  # 2025-01-01T00:00:00Z
_EVENT_TYPES = ["vital.heart_rate", "vital.resp_rate", "lab.lactate", "lab.creatinine"]
_TYPE_SHARE = [0.4, 0.3, 0.2, 0.1]


@dataclass(frozen=True)
class WorkloadSpec:
    """Settings for one workload. `n_events` is a target; the actual count is reported."""

    n_events: int
    seed: int
    skew: Literal["uniform", "zipf"] = "uniform"
    events_per_entity: int = 200
    events_per_prediction: int = 5
    span_days: int = 30
    correction_rate: float = 0.06
    retraction_rate: float = 0.02
    event_time_change_rate: float = 0.1  # share of corrections that also move the event time
    late_rate: float = 0.03  # share of first versions delivered hours to days late
    out_of_order_rate: float = 0.2  # share of corrections that overtake their first version
    unknown_availability_rate: float = 0.002
    duplicate_rate: float = 0.01
    zipf_exponent: float = 1.1

    def to_dict(self) -> dict[str, Any]:
        return {"generator_version": WORKLOAD_GENERATOR_VERSION, **asdict(self)}


def workload_features() -> FeatureConfig:
    """The fixed feature set used for engineering runs: all four aggregations, three windows."""
    return FeatureConfig(
        feature_set_version="workload-1",
        features=[
            {
                "name": "hr_last_6h",
                "event_type": "vital.heart_rate",
                "aggregation": "last",
                "window": "6h",
            },
            {
                "name": "hr_mean_6h",
                "event_type": "vital.heart_rate",
                "aggregation": "mean",
                "window": "6h",
            },
            {
                "name": "hr_count_6h",
                "event_type": "vital.heart_rate",
                "aggregation": "count",
                "window": "6h",
            },
            {
                "name": "rr_mean_6h",
                "event_type": "vital.resp_rate",
                "aggregation": "mean",
                "window": "6h",
            },
            {
                "name": "lac_last_24h",
                "event_type": "lab.lactate",
                "aggregation": "last",
                "window": "24h",
            },
            {
                "name": "lac_count_24h",
                "event_type": "lab.lactate",
                "aggregation": "count",
                "window": "24h",
            },
            {
                "name": "lac_since_24h",
                "event_type": "lab.lactate",
                "aggregation": "time_since_last",
                "window": "24h",
            },
            {
                "name": "cre_last_48h",
                "event_type": "lab.creatinine",
                "aggregation": "last",
                "window": "48h",
            },
        ],
    )


def generate_workload(spec: WorkloadSpec) -> tuple[pa.Table, pa.Table]:
    """Return (events, spine). Deterministic for a given spec on a given platform."""
    rng = np.random.default_rng(spec.seed)
    versions_per_logical = 1 + spec.correction_rate + spec.retraction_rate
    n_logical = max(1, round(spec.n_events / (versions_per_logical * (1 + spec.duplicate_rate))))
    n_entities = max(2, n_logical // spec.events_per_entity)
    weights = _entity_weights(n_entities, spec)

    entity = rng.choice(n_entities, size=n_logical, p=weights)
    type_index = rng.choice(len(_EVENT_TYPES), size=n_logical, p=_TYPE_SHARE)
    span_us = spec.span_days * 24 * _HOUR_US
    event_us = _START_US + rng.integers(0, span_us, size=n_logical)
    value = np.round(rng.normal(50.0, 15.0, size=n_logical), 2)

    # First version: source delay with a long tail, then a pipeline lag on top.
    source_delay = _seconds(rng.lognormal(np.log(1800), 0.8, size=n_logical))
    late = rng.random(n_logical) < spec.late_rate
    source_delay[late] += _seconds(rng.exponential(24 * 3600, size=int(late.sum())))
    pipeline_lag = _seconds(rng.lognormal(np.log(300), 0.6, size=n_logical))
    first = _Versions(
        logical=np.arange(n_logical),
        revision=np.ones(n_logical, dtype=np.int64),
        upsert=np.ones(n_logical, dtype=bool),
        event_us=event_us,
        value=value,
        source_us=event_us + source_delay,
        pipeline_us=event_us + source_delay + pipeline_lag,
    )

    # Corrections: a second revision, usually later, sometimes with a moved event time.
    corrected = np.flatnonzero(rng.random(n_logical) < spec.correction_rate)
    moved = rng.random(len(corrected)) < spec.event_time_change_rate
    shift = np.where(moved, rng.integers(-2 * _HOUR_US, 2 * _HOUR_US, size=len(corrected)), 0)
    correction_source = first.source_us[corrected] + _seconds(
        rng.exponential(6 * 3600, size=len(corrected))
    )
    correction = _Versions(
        logical=corrected,
        revision=np.full(len(corrected), 2, dtype=np.int64),
        upsert=np.ones(len(corrected), dtype=bool),
        event_us=event_us[corrected] + shift,
        value=np.round(value[corrected] + rng.normal(0.0, 5.0, size=len(corrected)), 2),
        source_us=correction_source,
        pipeline_us=correction_source
        + _seconds(rng.lognormal(np.log(300), 0.6, size=len(corrected))),
    )
    # Out-of-order delivery: the pipeline receives revision 1 after revision 2.
    overtaken = corrected[rng.random(len(corrected)) < spec.out_of_order_rate]
    position = {int(logical): index for index, logical in enumerate(corrected)}
    for logical in overtaken:
        first.pipeline_us[logical] = correction.pipeline_us[position[int(logical)]] + 3600 * _US

    # Retractions: revision 3, so they outrank a correction when both exist.
    retracted = np.flatnonzero(rng.random(n_logical) < spec.retraction_rate)
    retraction_source = first.source_us[retracted] + _seconds(
        rng.exponential(12 * 3600, size=len(retracted))
    )
    retraction = _Versions(
        logical=retracted,
        revision=np.full(len(retracted), 3, dtype=np.int64),
        upsert=np.zeros(len(retracted), dtype=bool),
        event_us=event_us[retracted],
        value=value[retracted],
        source_us=retraction_source,
        pipeline_us=retraction_source
        + _seconds(rng.lognormal(np.log(300), 0.6, size=len(retracted))),
    )

    versions = _Versions.concatenate([first, correction, retraction])
    unknown = rng.random(len(versions.logical)) < spec.unknown_availability_rate
    repeats = np.flatnonzero(rng.random(len(versions.logical)) < spec.duplicate_rate)
    order = rng.permutation(np.concatenate([np.arange(len(versions.logical)), repeats]))
    events = _events_table(versions, unknown, order, entity, type_index)

    n_predictions = max(1, n_logical // spec.events_per_prediction)
    prediction_entity = rng.choice(n_entities, size=n_predictions, p=weights)
    # Predictions fall on whole minutes so that some coincide exactly with event and arrival times.
    prediction_us = (
        _START_US + rng.integers(0, span_us // (60 * _US), size=n_predictions) * 60 * _US
    )
    spine = pa.table(
        {
            "prediction_id": pa.array(
                [f"Q{index:09d}" for index in range(n_predictions)], pa.string()
            ),
            "entity_id": pa.array([f"E{index:07d}" for index in prediction_entity], pa.string()),
            "encounter_id": pa.nulls(n_predictions, pa.string()),
            "prediction_time": pa.array(prediction_us, pa.int64()).cast(TIMESTAMP),
        },
        schema=SPINE_SCHEMA,
    )
    return events, spine


@dataclass
class _Versions:
    logical: np.ndarray
    revision: np.ndarray
    upsert: np.ndarray
    event_us: np.ndarray
    value: np.ndarray
    source_us: np.ndarray
    pipeline_us: np.ndarray

    @classmethod
    def concatenate(cls, parts: list["_Versions"]) -> "_Versions":
        names = ("logical", "revision", "upsert", "event_us", "value", "source_us", "pipeline_us")
        return cls(*(np.concatenate([getattr(part, name) for part in parts]) for name in names))


def _seconds(values: np.ndarray) -> np.ndarray:
    """Whole seconds, as microseconds. Whole seconds make exact ties plausible."""
    return np.round(values).astype(np.int64) * _US


def _entity_weights(n_entities: int, spec: WorkloadSpec) -> np.ndarray:
    if spec.skew == "uniform":
        return np.full(n_entities, 1.0 / n_entities)
    ranks = np.arange(1, n_entities + 1, dtype=float)
    weights = ranks**-spec.zipf_exponent
    return weights / weights.sum()


def _events_table(
    versions: _Versions,
    unknown: np.ndarray,
    order: np.ndarray,
    entity: np.ndarray,
    type_index: np.ndarray,
) -> pa.Table:
    logical = versions.logical[order]
    revision = versions.revision[order]
    upsert = versions.upsert[order]
    pipeline_unknown = unknown[order]
    n_rows = len(order)

    def timestamps(values: np.ndarray, mask: np.ndarray | None = None) -> pa.Array:
        return pa.array(values, pa.int64(), mask=mask).cast(TIMESTAMP)

    return pa.table(
        {
            "entity_id": pa.array([f"E{index:07d}" for index in entity[logical]], pa.string()),
            "encounter_id": pa.nulls(n_rows, pa.string()),
            "logical_event_id": pa.array([f"L{index:09d}" for index in logical], pa.string()),
            "version_id": pa.array([f"v{number}" for number in revision], pa.string()),
            "source_revision": pa.array(revision, pa.int64()),
            "operation": pa.array(np.where(upsert, "upsert", "retract"), pa.string()),
            "event_type": pa.array(np.array(_EVENT_TYPES)[type_index[logical]], pa.string()),
            "event_time": timestamps(versions.event_us[order]),
            "value": pa.array(versions.value[order], pa.float64()),
            "unit": pa.nulls(n_rows, pa.string()),
            "source": pa.array(["workload"] * n_rows, pa.string()),
            "schema_version": pa.array(["1"] * n_rows, pa.string()),
            "source_available_time": timestamps(versions.source_us[order]),
            "source_availability_provenance": pa.array(["observed"] * n_rows, pa.string()),
            "pipeline_available_time": timestamps(
                versions.pipeline_us[order], mask=pipeline_unknown
            ),
            "pipeline_availability_provenance": pa.array(
                np.where(pipeline_unknown, "unknown", "observed"), pa.string()
            ),
            "ingestion_time": pa.nulls(n_rows, TIMESTAMP),
        },
        schema=EVENT_SCHEMA,
    )
