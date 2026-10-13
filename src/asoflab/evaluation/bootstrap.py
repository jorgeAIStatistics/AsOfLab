"""Paired, patient-clustered bootstrap.

Predictions from one patient are correlated, so the unit that is resampled is
the patient. A resample draws patients with replacement and carries every
prediction of a drawn patient along, as many times as the patient was drawn.
The same resample is applied to every condition being compared, which is what
makes the differences paired.

What the intervals cover: sampling variability in the *test patients*, for
models that are held fixed. They do not include the variability of fitting the
models, of choosing the training patients, or of the simulation itself.

Interval method: percentile. Resamples in which a statistic cannot be computed
(for example a resample with no positive labels) are counted, never silently
dropped: if fewer than `min_completed_fraction` of the requested resamples
produced a value, no interval is reported and the reason is given.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from asoflab.errors import NotEstimable

Statistic = Callable[[np.ndarray], float]


@dataclass(frozen=True)
class Interval:
    """A point estimate with its bootstrap interval, or the reason there is none."""

    point: float | None
    lower: float | None
    upper: float | None
    requested: int
    completed: int
    reason: str | None = None

    @property
    def estimable(self) -> bool:
        return self.lower is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "point": self.point,
            "lower": self.lower,
            "upper": self.upper,
            "estimable": self.estimable,
            "bootstrap_requested": self.requested,
            "bootstrap_completed": self.completed,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class BootstrapSettings:
    replicates: int = 2000
    seed: int = 20261013
    level: float = 0.95
    min_completed_fraction: float = 0.95
    method: str = "percentile"

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_resampled": "patient",
            "paired_across_conditions": True,
            "replicates_requested": self.replicates,
            "seed": self.seed,
            "level": self.level,
            "interval_method": self.method,
            "min_completed_fraction": self.min_completed_fraction,
            "conditional_on_fitted_models": True,
        }


def cluster_bootstrap(
    patients: np.ndarray,
    statistics: dict[str, Statistic],
    settings: BootstrapSettings,
    base_weights: np.ndarray | None = None,
    differences: dict[str, tuple[str, str]] | None = None,
) -> dict[str, Interval]:
    """Bootstrap intervals for several statistics computed on the same resamples.

    Each statistic is a function of a weight per row. `base_weights` (default
    all ones) are multiplied by the number of times each row's patient was drawn.

    `differences` maps a name to (minuend, subtrahend), both names in
    `statistics`. Each difference is formed inside every resample, so both sides
    always see the same patients: that is the pairing.
    """
    differences = differences or {}
    _, patient_index = np.unique(patients, return_inverse=True)
    n_patients = int(patient_index.max()) + 1 if len(patient_index) else 0
    base = np.ones(len(patients)) if base_weights is None else np.asarray(base_weights, dtype=float)
    names = [*statistics, *differences]

    def evaluate(weights: np.ndarray) -> tuple[dict[str, float], dict[str, str]]:
        values: dict[str, float] = {}
        reasons: dict[str, str] = {}
        for name, statistic in statistics.items():
            try:
                values[name] = float(statistic(weights))
            except NotEstimable as error:
                reasons[name] = error.reason
        for name, (minuend, subtrahend) in differences.items():
            if minuend in values and subtrahend in values:
                values[name] = values[minuend] - values[subtrahend]
            else:
                reasons[name] = reasons.get(minuend) or reasons.get(subtrahend) or "not estimable"
        return values, reasons

    points, point_reasons = evaluate(base)
    if n_patients < 2:
        reason = f"{n_patients} patient(s): a patient-level bootstrap needs at least two"
        return {
            name: Interval(points.get(name), None, None, settings.replicates, 0, reason)
            for name in names
        }

    rng = np.random.default_rng(settings.seed)
    draws = {name: np.full(settings.replicates, np.nan) for name in names}
    for replicate in range(settings.replicates):
        times_drawn = np.bincount(
            rng.integers(0, n_patients, size=n_patients), minlength=n_patients
        )
        values, _ = evaluate(base * times_drawn[patient_index])
        for name, value in values.items():
            draws[name][replicate] = (
                value  # a statistic missing here is a resample that did not complete
            )

    tail = (1.0 - settings.level) / 2.0
    intervals = {}
    for name in names:
        completed_values = draws[name][np.isfinite(draws[name])]
        completed = len(completed_values)
        if name not in points:
            intervals[name] = Interval(
                None, None, None, settings.replicates, completed, point_reasons[name]
            )
        elif completed < settings.min_completed_fraction * settings.replicates:
            intervals[name] = Interval(
                points[name],
                None,
                None,
                settings.replicates,
                completed,
                f"only {completed} of {settings.replicates} resamples could be evaluated, "
                f"below the required {settings.min_completed_fraction:.0%}",
            )
        else:
            lower, upper = np.quantile(completed_values, [tail, 1.0 - tail])
            intervals[name] = Interval(
                points[name], float(lower), float(upper), settings.replicates, completed
            )
    return intervals
