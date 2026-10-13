"""Performance metrics that accept row weights and refuse to invent values.

Every metric takes an optional weight per prediction. Weights serve two
purposes: a bootstrap resample is expressed as integer multiplicities (a
patient drawn twice gives each of its predictions weight 2), and the
patient-weighted view gives each prediction weight 1 / (predictions of that
patient).

A metric that cannot be computed raises `NotEstimable` with a reason. Nothing
here returns a placeholder number.

Conventions:

- AUROC: the Mann-Whitney statistic, with tied scores counted as one half.
- Average precision: sum over distinct score thresholds of
  (recall_k - recall_{k-1}) * precision_k. This is the step-function definition
  used by scikit-learn's `average_precision_score`. It is not the area under a
  linearly interpolated precision-recall curve.
- Brier score: mean squared difference between predicted probability and outcome.
- Calibration intercept: the intercept of a logistic model with logit(p) as a
  fixed offset. Calibration slope: the coefficient of logit(p) in a logistic
  model with a free intercept. Perfect calibration gives 0 and 1.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np

from asoflab.errors import NotEstimable

MIN_PATIENTS = 10
UNSTABLE_BELOW_EVENTS = 20
UNSTABLE_BELOW_PATIENTS = 30
_LOGIT_CLIP = 1e-6


@dataclass(frozen=True)
class Estimate:
    """A metric value, or the reason there is none."""

    value: float | None
    reason: str | None = None

    @property
    def estimable(self) -> bool:
        return self.value is not None

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "estimable": self.estimable, "reason": self.reason}


def _weights(labels: np.ndarray, weights: np.ndarray | None) -> np.ndarray:
    return (
        np.ones(len(labels), dtype=float) if weights is None else np.asarray(weights, dtype=float)
    )


def _require_both_classes(labels: np.ndarray, weights: np.ndarray) -> tuple[float, float]:
    positive = float(weights[labels == 1].sum())
    negative = float(weights[labels == 0].sum())
    if positive <= 0 and negative <= 0:
        raise NotEstimable("the sample is empty")
    if positive <= 0:
        raise NotEstimable("the sample has no positive labels")
    if negative <= 0:
        raise NotEstimable("the sample has no negative labels")
    return positive, negative


class ScoredSample:
    """Labels and scores for one condition, with the sorting done once.

    A bootstrap evaluates the same scores thousands of times with different
    weights. Sorting does not depend on the weights, so it is done here once and
    each evaluation is then a single pass.
    """

    def __init__(self, labels: np.ndarray, scores: np.ndarray) -> None:
        self.labels = np.asarray(labels)
        self.scores = np.asarray(scores, dtype=float)
        self._is_positive = (self.labels == 1).astype(float)
        self._order = np.argsort(self.scores, kind="stable")
        ordered = self.scores[self._order]
        # First position of each run of tied scores, lowest score first.
        self._starts = np.flatnonzero(np.r_[True, ordered[1:] != ordered[:-1]])
        self._logit = _logit(self.scores)
        self._calibration_start: dict[str, np.ndarray] = {}

    def _grouped(self, weights: np.ndarray | None) -> tuple[np.ndarray, np.ndarray, float, float]:
        """Positive and negative weight per tied-score group, ascending, and their totals."""
        weights = _weights(self.labels, weights)
        positive_weight = (weights * self._is_positive)[self._order]
        negative_weight = (weights * (1.0 - self._is_positive))[self._order]
        group_positive = np.add.reduceat(positive_weight, self._starts)
        group_negative = np.add.reduceat(negative_weight, self._starts)
        positive, negative = float(group_positive.sum()), float(group_negative.sum())
        if positive <= 0 and negative <= 0:
            raise NotEstimable("the sample is empty")
        if positive <= 0:
            raise NotEstimable("the sample has no positive labels")
        if negative <= 0:
            raise NotEstimable("the sample has no negative labels")
        return group_positive, group_negative, positive, negative

    def auroc(self, weights: np.ndarray | None = None) -> float:
        group_positive, group_negative, positive, negative = self._grouped(weights)
        negative_below = np.cumsum(group_negative) - group_negative
        concordant = float(np.sum(group_positive * (negative_below + 0.5 * group_negative)))
        return concordant / (positive * negative)

    def average_precision(self, weights: np.ndarray | None = None) -> float:
        group_positive, group_negative, positive, _ = self._grouped(weights)
        # One threshold per distinct score, highest first.
        descending_positive = group_positive[::-1]
        true_positive = np.cumsum(descending_positive)
        predicted_positive = np.cumsum((group_positive + group_negative)[::-1])
        precision = np.divide(
            true_positive,
            predicted_positive,
            out=np.zeros_like(true_positive),
            where=predicted_positive > 0,
        )
        return float(np.sum(descending_positive * precision) / positive)

    def brier(self, weights: np.ndarray | None = None) -> float:
        weights = _weights(self.labels, weights)
        total = float(weights.sum())
        if total <= 0:
            raise NotEstimable("the sample is empty")
        return float(np.sum(weights * (self.scores - self._is_positive) ** 2) / total)

    def calibration_intercept(self, weights: np.ndarray | None = None) -> float:
        weights = _weights(self.labels, weights)
        _require_both_classes(self.labels, weights)
        design = np.ones((len(self.labels), 1))
        fitted = _fit_logistic(
            design,
            self._logit,
            self._is_positive,
            weights,
            self._calibration_start.get("intercept"),
        )
        self._calibration_start.setdefault("intercept", fitted)
        return float(fitted[0])

    def calibration_slope(self, weights: np.ndarray | None = None) -> float:
        weights = _weights(self.labels, weights)
        _require_both_classes(self.labels, weights)
        if float(np.ptp(self._logit[weights > 0])) == 0.0:
            raise NotEstimable("every prediction is the same, so a slope is undefined")
        design = np.column_stack([np.ones(len(self.labels)), self._logit])
        fitted = _fit_logistic(
            design,
            np.zeros(len(self.labels)),
            self._is_positive,
            weights,
            self._calibration_start.get("slope"),
        )
        self._calibration_start.setdefault("slope", fitted)
        return float(fitted[1])


def _logit(scores: np.ndarray) -> np.ndarray:
    clipped = np.clip(scores, _LOGIT_CLIP, 1.0 - _LOGIT_CLIP)
    logit: np.ndarray = np.log(clipped / (1.0 - clipped))
    return logit


def _fit_logistic(
    design: np.ndarray,
    offset: np.ndarray,
    labels: np.ndarray,
    weights: np.ndarray,
    start: np.ndarray | None = None,
) -> np.ndarray:
    """Weighted logistic regression by Newton's method. Small, unpenalised, one or two columns.

    `start` only speeds up convergence; the optimum of this convex problem does not depend on it.
    """
    coefficients = np.zeros(design.shape[1]) if start is None else start.copy()
    for _ in range(50):
        linear = np.clip(design @ coefficients + offset, -30.0, 30.0)
        fitted = 1.0 / (1.0 + np.exp(-linear))
        gradient = design.T @ (weights * (labels - fitted))
        curvature = (design * (weights * fitted * (1.0 - fitted))[:, None]).T @ design
        try:
            step = np.linalg.solve(curvature, gradient)
        except np.linalg.LinAlgError as error:
            raise NotEstimable("the calibration model could not be fitted") from error
        coefficients = coefficients + step
        if not np.all(np.isfinite(coefficients)) or np.max(np.abs(coefficients)) > 1e3:
            raise NotEstimable("the calibration model did not converge")
        if np.max(np.abs(step)) < 1e-10:
            return coefficients
    raise NotEstimable("the calibration model did not converge")


def auroc(labels: np.ndarray, scores: np.ndarray, weights: np.ndarray | None = None) -> float:
    return ScoredSample(labels, scores).auroc(weights)


def average_precision(
    labels: np.ndarray, scores: np.ndarray, weights: np.ndarray | None = None
) -> float:
    return ScoredSample(labels, scores).average_precision(weights)


def brier(labels: np.ndarray, scores: np.ndarray, weights: np.ndarray | None = None) -> float:
    return ScoredSample(labels, scores).brier(weights)


def calibration_intercept(
    labels: np.ndarray, scores: np.ndarray, weights: np.ndarray | None = None
) -> float:
    return ScoredSample(labels, scores).calibration_intercept(weights)


def calibration_slope(
    labels: np.ndarray, scores: np.ndarray, weights: np.ndarray | None = None
) -> float:
    return ScoredSample(labels, scores).calibration_slope(weights)


METRICS = {
    "auroc": auroc,
    "average_precision": average_precision,
    "brier": brier,
    "calibration_intercept": calibration_intercept,
    "calibration_slope": calibration_slope,
}
DISCRIMINATION_AND_ACCURACY = ("auroc", "average_precision", "brier")
HIGHER_IS_BETTER = {"auroc": True, "average_precision": True, "brier": False}


def calibration_curve(
    labels: np.ndarray, scores: np.ndarray, bins: int = 10
) -> list[dict[str, Any]]:
    """Observed event rate against mean prediction, in equal-count bins of the predictions."""
    if len(labels) == 0:
        return []
    order = np.argsort(scores, kind="stable")
    curve = []
    for chunk in np.array_split(order, min(bins, len(order))):
        if len(chunk):
            curve.append(
                {
                    "mean_predicted": float(scores[chunk].mean()),
                    "observed_rate": float(labels[chunk].mean()),
                    "predictions": len(chunk),
                    "events": int(labels[chunk].sum()),
                }
            )
    return curve


def sample_description(labels: np.ndarray, patients: np.ndarray) -> dict[str, Any]:
    events = int(labels.sum())
    n_patients = len(np.unique(patients))
    return {
        "predictions": len(labels),
        "patients": n_patients,
        "events": events,
        "non_events": len(labels) - events,
        "prevalence": events / len(labels) if len(labels) else None,
        "patients_with_an_event": len(np.unique(patients[labels == 1])),
    }


def instability(labels: np.ndarray, patients: np.ndarray) -> list[str]:
    """Reasons an estimate from this sample should be read with caution. Conventions, not laws."""
    description = sample_description(labels, patients)
    flags = []
    if description["events"] < UNSTABLE_BELOW_EVENTS:
        flags.append(f"fewer than {UNSTABLE_BELOW_EVENTS} events")
    if description["non_events"] < UNSTABLE_BELOW_EVENTS:
        flags.append(f"fewer than {UNSTABLE_BELOW_EVENTS} non-events")
    if description["patients"] < UNSTABLE_BELOW_PATIENTS:
        flags.append(f"fewer than {UNSTABLE_BELOW_PATIENTS} patients")
    return flags


def evaluate(
    labels: np.ndarray,
    scores: np.ndarray,
    patients: np.ndarray,
    weights: np.ndarray | None = None,
) -> dict[str, Estimate]:
    """Every metric for one sample, each either a value or a reason."""
    n_patients = len(np.unique(patients))
    results = {}
    for name, metric in METRICS.items():
        if n_patients < MIN_PATIENTS:
            results[name] = Estimate(
                None, f"only {n_patients} patients; at least {MIN_PATIENTS} are required"
            )
            continue
        try:
            results[name] = Estimate(metric(labels, scores, weights))
        except NotEstimable as error:
            results[name] = Estimate(None, error.reason)
    return results


def patient_weights(patients: np.ndarray) -> np.ndarray:
    """Weights that give every patient the same total influence."""
    _, inverse, counts = np.unique(patients, return_inverse=True, return_counts=True)
    return 1.0 / counts[inverse]
