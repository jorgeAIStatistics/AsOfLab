"""Fixed-horizon binary labels with explicit unknowns. Section 8 of the temporal contract.

The central rule: a label that is not known is never turned into a zero.
"""

from collections import defaultdict
from datetime import datetime
from typing import Any

import pyarrow as pa

from asoflab.contract import MICROSECONDS_PER_SECOND
from asoflab.schema.models import LabelSpec
from asoflab.schema.tables import FOLLOW_UP_SCHEMA, OUTCOME_SCHEMA, SPINE_SCHEMA, TIMESTAMP, conform
from asoflab.schema.time import to_us

POSITIVE = "positive"
NEGATIVE = "negative"
PENDING_HORIZON = "pending_horizon"
PENDING_ASCERTAINMENT = "pending_ascertainment"
UNKNOWN_FOLLOW_UP = "unknown_follow_up"
UNKNOWN_STATUSES = (PENDING_HORIZON, PENDING_ASCERTAINMENT, UNKNOWN_FOLLOW_UP)

LABEL_SCHEMA = pa.schema(
    [
        *SPINE_SCHEMA,
        ("label", pa.int8()),  # 1, 0, or null when unknown
        ("label_status", pa.string()),
        ("label_mature_time", TIMESTAMP),  # prediction_time + horizon + maximum ascertainment lag
    ]
)


def assign_labels(
    spine: pa.Table,
    outcomes: pa.Table,
    follow_up: pa.Table,
    spec: LabelSpec,
    *,
    as_of: datetime | None = None,
) -> pa.Table:
    """Label every prediction using only what had been ascertained by `as_of`.

    With `as_of=None` the study is treated as complete: every recorded outcome
    and all recorded follow-up count.

    For a prediction at T with horizon H and maximum ascertainment lag L:

    - positive: an outcome with event time in (T, T + H], ascertained by `as_of`.
    - pending_horizon: no such outcome known and `as_of` < T + H.
    - pending_ascertainment: horizon elapsed, but `as_of` < T + H + L, so an outcome
      could have happened and not yet be known.
    - unknown_follow_up: follow-up for the encounter ends before T + H (or is missing).
    - negative: none of the above.
    """
    spine = conform(spine, SPINE_SCHEMA, "spine")
    outcomes = conform(outcomes, OUTCOME_SCHEMA, "outcomes")
    follow_up = conform(follow_up, FOLLOW_UP_SCHEMA, "follow_up")
    horizon_us = spec.horizon_seconds * MICROSECONDS_PER_SECOND
    lag_us = spec.max_ascertainment_lag_seconds * MICROSECONDS_PER_SECOND
    as_of_us = None if as_of is None else to_us(as_of)

    # (entity, encounter) -> [(event_us, ascertainment_us), ...] for the target outcome type
    known_outcomes: dict[tuple[str, str | None], list[tuple[int, int]]] = defaultdict(list)
    for row in _rows(
        outcomes, ["entity_id", "encounter_id", "outcome_type", "event_time", "ascertainment_time"]
    ):
        entity_id, encounter_id, outcome_type, event_us, ascertained_us = row
        if outcome_type != spec.outcome_type:
            continue
        if as_of_us is not None and ascertained_us > as_of_us:
            continue  # it happened, but nobody assigning labels knew yet
        known_outcomes[(entity_id, encounter_id)].append((event_us, ascertained_us))

    follow_up_end: dict[tuple[str, str | None], int] = {}
    for entity_id, encounter_id, _, end_us in _rows(
        follow_up, ["entity_id", "encounter_id", "follow_up_start", "follow_up_end"]
    ):
        key = (entity_id, encounter_id)
        follow_up_end[key] = max(end_us, follow_up_end.get(key, end_us))

    labels: list[int | None] = []
    statuses: list[str] = []
    mature: list[int] = []
    for entity_id, encounter_id, cutoff_us in _rows(
        spine, ["entity_id", "encounter_id", "prediction_time"]
    ):
        key = (entity_id, encounter_id)
        horizon_end_us = cutoff_us + horizon_us
        mature.append(horizon_end_us + lag_us)
        happened = any(
            cutoff_us < event_us <= horizon_end_us for event_us, _ in known_outcomes.get(key, ())
        )
        if happened:
            status = POSITIVE
        elif as_of_us is not None and as_of_us < horizon_end_us:
            status = PENDING_HORIZON
        elif follow_up_end.get(key, cutoff_us) < horizon_end_us:
            status = UNKNOWN_FOLLOW_UP
        elif as_of_us is not None and as_of_us < horizon_end_us + lag_us:
            status = PENDING_ASCERTAINMENT
        else:
            status = NEGATIVE
        statuses.append(status)
        labels.append(1 if status == POSITIVE else 0 if status == NEGATIVE else None)

    return pa.table(
        {
            **{name: spine.column(name) for name in SPINE_SCHEMA.names},
            "label": pa.array(labels, pa.int8()),
            "label_status": pa.array(statuses, pa.string()),
            "label_mature_time": pa.array(mature, pa.int64()).cast(TIMESTAMP),
        },
        schema=LABEL_SCHEMA,
    )


def _outcomes_by_encounter(
    outcomes: pa.Table, spec: LabelSpec
) -> dict[tuple[str, str | None], list[tuple[int, int]]]:
    """(entity, encounter) -> [(event_us, ascertainment_us), ...] for the target outcome type."""
    grouped: dict[tuple[str, str | None], list[tuple[int, int]]] = defaultdict(list)
    for entity_id, encounter_id, outcome_type, event_us, ascertained_us in _rows(
        conform(outcomes, OUTCOME_SCHEMA, "outcomes"),
        ["entity_id", "encounter_id", "outcome_type", "event_time", "ascertainment_time"],
    ):
        if outcome_type == spec.outcome_type:
            grouped[(entity_id, encounter_id)].append((event_us, ascertained_us))
    return grouped


def labels_recorded_at_horizon(
    spine: pa.Table, outcomes: pa.Table, follow_up: pa.Table, spec: LabelSpec
) -> list[int | None]:
    """The label an impatient pipeline would record: decided the moment each horizon ends.

    An event that happened inside the horizon but had not been ascertained at
    T + H is recorded as a negative. This exists to measure that mistake. It is
    never used as the label of record.
    """
    spine = conform(spine, SPINE_SCHEMA, "spine")
    horizon_us = spec.horizon_seconds * MICROSECONDS_PER_SECOND
    by_encounter = _outcomes_by_encounter(outcomes, spec)
    follow_up_end: dict[tuple[str, str | None], int] = {}
    for entity_id, encounter_id, _, end_us in _rows(
        conform(follow_up, FOLLOW_UP_SCHEMA, "follow_up"),
        ["entity_id", "encounter_id", "follow_up_start", "follow_up_end"],
    ):
        key = (entity_id, encounter_id)
        follow_up_end[key] = max(end_us, follow_up_end.get(key, end_us))

    recorded: list[int | None] = []
    for entity_id, encounter_id, cutoff_us in _rows(
        spine, ["entity_id", "encounter_id", "prediction_time"]
    ):
        key = (entity_id, encounter_id)
        horizon_end_us = cutoff_us + horizon_us
        known_in_time = any(
            cutoff_us < event_us <= horizon_end_us and ascertained_us <= horizon_end_us
            for event_us, ascertained_us in by_encounter.get(key, ())
        )
        if known_in_time:
            recorded.append(1)
        elif follow_up_end.get(key, cutoff_us) < horizon_end_us:
            recorded.append(None)
        else:
            recorded.append(0)
    return recorded


def count_premature_negatives(spine: pa.Table, outcomes: pa.Table, spec: LabelSpec) -> int:
    """Predictions whose event fell inside the horizon but was not yet ascertained when it ended.

    A pipeline that assigns labels the moment the horizon elapses would record
    each of these as a negative. Waiting for the maximum ascertainment lag avoids it.
    """
    spine = conform(spine, SPINE_SCHEMA, "spine")
    horizon_us = spec.horizon_seconds * MICROSECONDS_PER_SECOND
    by_encounter = _outcomes_by_encounter(outcomes, spec)
    premature = 0
    for entity_id, encounter_id, cutoff_us in _rows(
        spine, ["entity_id", "encounter_id", "prediction_time"]
    ):
        horizon_end_us = cutoff_us + horizon_us
        inside = [
            ascertained_us
            for event_us, ascertained_us in by_encounter.get((entity_id, encounter_id), ())
            if cutoff_us < event_us <= horizon_end_us
        ]
        if inside and min(inside) > horizon_end_us:
            premature += 1
    return premature


def label_coverage(labels: pa.Table) -> dict[str, Any]:
    """Counts by status. Unknown labels are reported, never dropped silently."""
    statuses = labels.column("label_status").to_pylist()
    counts = {status: statuses.count(status) for status in (POSITIVE, NEGATIVE, *UNKNOWN_STATUSES)}
    known = counts[POSITIVE] + counts[NEGATIVE]
    return {
        "predictions": len(statuses),
        "labelled": known,
        "unknown": len(statuses) - known,
        "coverage": known / len(statuses) if statuses else None,
        "by_status": counts,
    }


def _rows(table: pa.Table, columns: list[str]) -> list[tuple[Any, ...]]:
    """Rows as tuples, with timestamps as integer microseconds."""
    arrays = []
    for name in columns:
        column = table.column(name)
        if pa.types.is_timestamp(column.type):
            column = column.cast(pa.int64())
        arrays.append(column.to_pylist())
    return list(zip(*arrays, strict=True))
