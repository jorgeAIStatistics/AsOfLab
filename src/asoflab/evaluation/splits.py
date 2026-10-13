"""Patient-level data splits. Rows of one patient are never divided between partitions.

Two designs, answering different questions:

`patient_split` holds out patients at random. It asks how the model performs
on new patients drawn from the same period and process as the training data.

`chronological_split` trains on what was known at a cutoff date and tests on
patients first seen after it. It asks how a model built at that date would
have performed on the patients who came next. It is also patient-disjoint.
"""

import hashlib
from datetime import datetime
from typing import Any

import numpy as np
import pyarrow as pa

from asoflab.schema.time import to_us

TRAIN, VALIDATION, TEST = "train", "validation", "test"
EXCLUDED_IMMATURE = "excluded_label_not_mature_at_cutoff"
EXCLUDED_RETURNING = "excluded_patient_seen_before_cutoff"


def patient_split(
    entity_ids: list[str], seed: int, fractions: tuple[float, float, float] = (0.6, 0.2, 0.2)
) -> dict[str, str]:
    """Assign each patient to train, validation or test. Deterministic for a seed."""
    if abs(sum(fractions) - 1.0) > 1e-9:
        raise ValueError("fractions must sum to 1")
    patients = sorted(set(entity_ids))
    order = np.random.default_rng(seed).permutation(len(patients))
    n_train = round(fractions[0] * len(patients))
    n_validation = round(fractions[1] * len(patients))
    assignment = {}
    for rank, position in enumerate(order):
        part = TRAIN if rank < n_train else VALIDATION if rank < n_train + n_validation else TEST
        assignment[patients[position]] = part
    return assignment


def chronological_split(
    labels: pa.Table, cutoff: datetime, seed: int, validation_fraction: float = 0.25
) -> list[str]:
    """One assignment per prediction, in table order, for a model built at `cutoff`.

    - A prediction is usable for fitting only if its label was mature by the
      cutoff (`label_mature_time <= cutoff`). Both classes wait the same time, so
      the training sample is not selected on the outcome.
    - Fitting patients are divided into train and validation by patient.
    - The test set is every prediction after the cutoff from patients whose first
      prediction is after the cutoff.
    - Predictions before the cutoff whose labels were not yet mature are excluded.
    - A patient seen before the cutoff who returns after it is excluded from the
      test set, so that train and test share no patients.
    """
    cutoff_us = to_us(cutoff)
    entities = labels.column("entity_id").to_pylist()
    times = labels.column("prediction_time").cast(pa.int64()).to_pylist()
    mature = labels.column("label_mature_time").cast(pa.int64()).to_pylist()

    first_seen: dict[str, int] = {}
    for entity_id, time_us in zip(entities, times, strict=True):
        first_seen[entity_id] = min(time_us, first_seen.get(entity_id, time_us))
    earlier_patients = sorted(entity for entity, first in first_seen.items() if first <= cutoff_us)
    order = np.random.default_rng(seed).permutation(len(earlier_patients))
    n_validation = round(validation_fraction * len(earlier_patients))
    validation_patients = {earlier_patients[position] for position in order[:n_validation]}

    assignment = []
    for entity_id, time_us, mature_us in zip(entities, times, mature, strict=True):
        if time_us > cutoff_us:
            assignment.append(TEST if first_seen[entity_id] > cutoff_us else EXCLUDED_RETURNING)
        elif mature_us <= cutoff_us:
            assignment.append(VALIDATION if entity_id in validation_patients else TRAIN)
        else:
            assignment.append(EXCLUDED_IMMATURE)
    return assignment


def split_hash(assignment: dict[str, str] | list[str]) -> str:
    """Identifies a split so a run can state exactly which one it used."""
    items: Any = sorted(assignment.items()) if isinstance(assignment, dict) else assignment
    return hashlib.sha256(repr(items).encode()).hexdigest()


def split_counts(parts: list[str], entities: list[str]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, Any]] = {}
    for part, entity_id in zip(parts, entities, strict=True):
        entry = counts.setdefault(part, {"predictions": 0, "patients": set()})
        entry["predictions"] += 1
        entry["patients"].add(entity_id)
    return {
        part: {"predictions": entry["predictions"], "patients": len(entry["patients"])}
        for part, entry in sorted(counts.items())
    }
