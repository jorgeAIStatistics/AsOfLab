"""Compare two feature tables under the equivalence rules in section 7 of the contract."""

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from asoflab.contract import FLOAT_ABS_TOL, FLOAT_REL_TOL, Aggregation
from asoflab.schema.models import FeatureConfig

_EXACT_AGGREGATIONS = {Aggregation.COUNT, Aggregation.LAST}


@dataclass(frozen=True)
class Mismatch:
    prediction_id: str
    column: str
    left: Any
    right: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "column": self.column,
            "left": _plain(self.left),
            "right": _plain(self.right),
        }


@dataclass
class ParityReport:
    left_name: str
    right_name: str
    rows_left: int
    rows_right: int
    n_mismatches: int = 0
    max_abs_difference: float = 0.0
    examples: list[Mismatch] = field(default_factory=list)

    @property
    def equivalent(self) -> bool:
        return self.n_mismatches == 0 and self.rows_left == self.rows_right

    def to_dict(self) -> dict[str, Any]:
        return {
            "left": self.left_name,
            "right": self.right_name,
            "rows_left": self.rows_left,
            "rows_right": self.rows_right,
            "equivalent": self.equivalent,
            "n_mismatches": self.n_mismatches,
            "max_abs_difference_within_tolerance": self.max_abs_difference,
            "examples": [example.to_dict() for example in self.examples],
        }

    def describe(self) -> str:
        if self.equivalent:
            return f"{self.left_name} and {self.right_name} agree on {self.rows_left} rows."
        shown = "; ".join(
            f"{m.prediction_id}.{m.column}: {m.left!r} vs {m.right!r}" for m in self.examples[:5]
        )
        return (
            f"{self.left_name} and {self.right_name} disagree in {self.n_mismatches} cells "
            f"({self.rows_left} vs {self.rows_right} rows). {shown}"
        )


def compare_feature_tables(
    left: pa.Table,
    right: pa.Table,
    config: FeatureConfig,
    *,
    left_name: str = "left",
    right_name: str = "right",
    max_examples: int = 20,
) -> ParityReport:
    """Exact on identifiers, times, `count` and `last`; within tolerance on the rest."""
    left = left.sort_by("prediction_id")
    right = right.sort_by("prediction_id")
    report = ParityReport(left_name, right_name, left.num_rows, right.num_rows)

    left_ids = left.column("prediction_id").to_pylist()
    right_ids = right.column("prediction_id").to_pylist()
    if left_ids != right_ids:
        only_left = sorted(set(left_ids) - set(right_ids))
        only_right = sorted(set(right_ids) - set(left_ids))
        report.n_mismatches = max(len(only_left) + len(only_right), 1)
        for prediction_id in only_left[:max_examples]:
            report.examples.append(Mismatch(prediction_id, "<row>", "present", "absent"))
        for prediction_id in only_right[:max_examples]:
            report.examples.append(Mismatch(prediction_id, "<row>", "absent", "present"))
        if not report.examples:
            report.examples.append(Mismatch(left_ids[0], "<row>", "repeated ids", "repeated ids"))
        return report

    tolerant = {
        feature.name
        for feature in config.features
        if feature.aggregation not in _EXACT_AGGREGATIONS
    }
    for column in left.column_names:
        if column == "prediction_id":
            continue
        left_column = left.column(column).combine_chunks()
        right_column = right.column(column).combine_chunks()
        if column in tolerant:
            differs, worst = _differs_within_tolerance(left_column, right_column)
            report.max_abs_difference = max(report.max_abs_difference, worst)
        else:
            differs = _differs_exactly(left_column, right_column)
        positions = np.flatnonzero(differs)
        report.n_mismatches += len(positions)
        for position in positions[: max(0, max_examples - len(report.examples))]:
            index = int(position)
            report.examples.append(
                Mismatch(
                    left_ids[index], column, left_column[index].as_py(), right_column[index].as_py()
                )
            )
    return report


def _differs_exactly(left: pa.Array, right: pa.Array) -> np.ndarray:
    left_null = pc.is_null(left).to_numpy(zero_copy_only=False)
    right_null = pc.is_null(right).to_numpy(zero_copy_only=False)
    unequal = pc.fill_null(pc.not_equal(left, right), False).to_numpy(zero_copy_only=False)
    return (left_null != right_null) | unequal


def _differs_within_tolerance(left: pa.Array, right: pa.Array) -> tuple[np.ndarray, float]:
    left_null = pc.is_null(left).to_numpy(zero_copy_only=False)
    right_null = pc.is_null(right).to_numpy(zero_copy_only=False)
    a = pc.fill_null(left, 0.0).to_numpy(zero_copy_only=False).astype(float)
    b = pc.fill_null(right, 0.0).to_numpy(zero_copy_only=False).astype(float)
    difference = np.abs(a - b)
    allowed = np.maximum(FLOAT_REL_TOL * np.maximum(np.abs(a), np.abs(b)), FLOAT_ABS_TOL)
    both_present = ~left_null & ~right_null
    differs = (left_null != right_null) | (both_present & (difference > allowed))
    within = both_present & ~differs
    worst = float(difference[within].max()) if within.any() else 0.0
    return differs, worst


def _plain(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value
