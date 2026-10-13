"""Splits are by patient, deterministic, and the chronological design respects label maturity."""

from datetime import UTC, datetime, timedelta

import pyarrow as pa

from asoflab.evaluation.labels import LABEL_SCHEMA
from asoflab.evaluation.splits import (
    EXCLUDED_IMMATURE,
    EXCLUDED_RETURNING,
    TEST,
    TRAIN,
    VALIDATION,
    chronological_split,
    patient_split,
    split_counts,
    split_hash,
)

START = datetime(2025, 1, 1, tzinfo=UTC)


def day(n: float) -> datetime:
    return START + timedelta(days=n)


def labels_table(rows: list[tuple[str, float]], mature_after_days: float = 1.0) -> pa.Table:
    return pa.table(
        {
            "prediction_id": [f"q{index}" for index in range(len(rows))],
            "entity_id": [entity for entity, _ in rows],
            "encounter_id": [None] * len(rows),
            "prediction_time": [day(when) for _, when in rows],
            "label": [0] * len(rows),
            "label_status": ["negative"] * len(rows),
            "label_mature_time": [day(when + mature_after_days) for _, when in rows],
        },
        schema=LABEL_SCHEMA,
    )


def test_patient_split_is_disjoint_complete_and_deterministic() -> None:
    patients = [f"P{index:03d}" for index in range(200)]
    rows = patients * 3  # repeated records per patient
    first = patient_split(rows, seed=1)
    assert set(first) == set(patients)
    sizes = {
        part: sum(value == part for value in first.values()) for part in (TRAIN, VALIDATION, TEST)
    }
    assert sizes == {TRAIN: 120, VALIDATION: 40, TEST: 40}
    assert patient_split(list(reversed(rows)), seed=1) == first, "input order must not matter"
    assert split_hash(patient_split(rows, seed=1)) == split_hash(first)
    assert split_hash(patient_split(rows, seed=2)) != split_hash(first)


def test_chronological_split_rules() -> None:
    labels = labels_table(
        [
            ("early", 1.0),  # mature at day 2: usable for fitting
            ("early", 9.5),  # before the cutoff but mature at 10.5: not yet known at the cutoff
            ("returning", 3.0),  # seen before the cutoff ...
            ("returning", 15.0),  # ... so the later record is excluded from the test set
            ("new", 12.0),  # first seen after the cutoff: test
            ("new", 20.0),
            (
                "on_the_cutoff",
                10.0,
            ),  # at the cutoff exactly: belongs to the earlier period, not mature
        ]
    )
    parts = chronological_split(labels, cutoff=day(10), seed=1, validation_fraction=0.0)
    assert parts == [
        TRAIN,
        EXCLUDED_IMMATURE,
        TRAIN,
        EXCLUDED_RETURNING,
        TEST,
        TEST,
        EXCLUDED_IMMATURE,
    ]


def test_chronological_split_shares_no_patient_between_fitting_and_test() -> None:
    rows = [(f"P{index:03d}", float(index % 30)) for index in range(300)] + [
        (f"P{index:03d}", float(index % 30) + 20) for index in range(0, 300, 7)
    ]
    labels = labels_table(rows)
    parts = chronological_split(labels, cutoff=day(15), seed=4)
    entities = labels.column("entity_id").to_pylist()
    fitting = {
        entity for entity, part in zip(entities, parts, strict=True) if part in (TRAIN, VALIDATION)
    }
    testing = {entity for entity, part in zip(entities, parts, strict=True) if part == TEST}
    train = {entity for entity, part in zip(entities, parts, strict=True) if part == TRAIN}
    validation = {
        entity for entity, part in zip(entities, parts, strict=True) if part == VALIDATION
    }
    assert fitting
    assert testing
    assert validation
    assert not (fitting & testing)
    assert not (train & validation)
    counts = split_counts(parts, entities)
    assert sum(entry["predictions"] for entry in counts.values()) == len(rows)
