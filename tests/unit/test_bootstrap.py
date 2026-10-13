"""The patient-clustered bootstrap: pairing, clustering, determinism, and honest failure."""

import numpy as np
import pytest

from asoflab.errors import NotEstimable
from asoflab.evaluation.bootstrap import BootstrapSettings, cluster_bootstrap
from asoflab.evaluation.metrics import auroc


def _weighted_mean(values: np.ndarray):  # type: ignore[no-untyped-def]
    def statistic(weights: np.ndarray) -> float:
        if weights.sum() <= 0:
            raise NotEstimable("empty")
        return float(np.sum(weights * values) / weights.sum())

    return statistic


def clustered_sample(
    n_patients: int, per_patient: int, between_sd: float, within_sd: float, seed: int
):  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(seed)
    patients = np.repeat(np.arange(n_patients), per_patient)
    values = np.repeat(rng.normal(0, between_sd, n_patients), per_patient) + rng.normal(
        0, within_sd, n_patients * per_patient
    )
    return patients, values


def test_interval_width_matches_the_cluster_standard_error_not_the_row_one() -> None:
    """With strong within-patient correlation, ignoring clustering understates uncertainty badly."""
    patients, values = clustered_sample(
        n_patients=400, per_patient=25, between_sd=1.0, within_sd=0.5, seed=1
    )
    settings = BootstrapSettings(replicates=2000, seed=7)
    clustered = cluster_bootstrap(patients, {"mean": _weighted_mean(values)}, settings)["mean"]
    cluster_means = values.reshape(400, 25).mean(axis=1)
    analytic_cluster_se = cluster_means.std(ddof=1) / np.sqrt(400)
    naive_row_se = values.std(ddof=1) / np.sqrt(len(values))
    assert clustered.lower is not None
    assert clustered.upper is not None
    bootstrap_se = (clustered.upper - clustered.lower) / (2 * 1.96)
    assert bootstrap_se == pytest.approx(analytic_cluster_se, rel=0.08)
    assert bootstrap_se > 4 * naive_row_se, (
        "treating rows as independent would be several times too narrow"
    )
    # Resampling rows instead of patients reproduces the too-narrow answer.
    by_row = cluster_bootstrap(np.arange(len(values)), {"mean": _weighted_mean(values)}, settings)[
        "mean"
    ]
    assert (by_row.upper - by_row.lower) / (2 * 1.96) == pytest.approx(naive_row_se, rel=0.1)  # type: ignore[operator]


def test_the_same_seed_gives_the_same_interval_and_another_seed_does_not() -> None:
    patients, values = clustered_sample(60, 5, 1.0, 1.0, seed=2)
    statistic = {"mean": _weighted_mean(values)}
    first = cluster_bootstrap(patients, statistic, BootstrapSettings(replicates=300, seed=11))[
        "mean"
    ]
    again = cluster_bootstrap(patients, statistic, BootstrapSettings(replicates=300, seed=11))[
        "mean"
    ]
    other = cluster_bootstrap(patients, statistic, BootstrapSettings(replicates=300, seed=12))[
        "mean"
    ]
    assert first == again
    assert (first.lower, first.upper) != (other.lower, other.upper)


def test_paired_differences_use_the_same_resample_for_both_sides() -> None:
    """Two conditions that differ by a constant have a difference with no sampling variability at all."""
    patients, values = clustered_sample(80, 6, 1.0, 1.0, seed=3)
    shifted = values + 0.25
    statistics = {"a": _weighted_mean(values), "b": _weighted_mean(shifted)}
    result = cluster_bootstrap(
        patients,
        statistics,
        BootstrapSettings(replicates=400, seed=5),
        differences={"b_minus_a": ("b", "a")},
    )
    difference = result["b_minus_a"]
    assert difference.point == pytest.approx(0.25)
    assert difference.lower == pytest.approx(0.25)
    assert difference.upper == pytest.approx(0.25)
    assert result["a"].upper - result["a"].lower > 0.1  # type: ignore[operator]


def test_identical_conditions_give_a_difference_of_exactly_zero() -> None:
    rng = np.random.default_rng(4)
    patients = np.repeat(np.arange(120), 4)
    labels = (rng.random(480) < 0.3).astype(int)
    scores = rng.random(480)
    statistics = {
        "one": lambda weights: auroc(labels, scores, weights),
        "same": lambda weights: auroc(labels, scores.copy(), weights),
    }
    result = cluster_bootstrap(
        patients,
        statistics,
        BootstrapSettings(replicates=200, seed=1),
        differences={"difference": ("one", "same")},
    )["difference"]
    assert (result.point, result.lower, result.upper) == (0.0, 0.0, 0.0)


def test_resamples_that_cannot_be_evaluated_are_counted_and_can_void_the_interval() -> None:
    # Only two of thirty patients have a positive label: many resamples will have none.
    patients = np.repeat(np.arange(30), 3)
    labels = np.zeros(90, dtype=int)
    labels[[0, 3]] = 1
    scores = np.linspace(0.05, 0.95, 90)
    settings = BootstrapSettings(replicates=500, seed=3)
    result = cluster_bootstrap(
        patients, {"auroc": lambda weights: auroc(labels, scores, weights)}, settings
    )["auroc"]
    assert result.point is not None, "the full sample has both classes"
    assert result.requested == 500
    assert 0 < result.completed < 0.95 * 500, "a visible share of resamples had no positive label"
    assert result.lower is None
    assert result.upper is None
    assert "resamples could be evaluated" in (result.reason or "")
    lenient = cluster_bootstrap(
        patients,
        {"auroc": lambda weights: auroc(labels, scores, weights)},
        BootstrapSettings(replicates=500, seed=3, min_completed_fraction=0.5),
    )["auroc"]
    assert lenient.estimable
    assert lenient.completed == result.completed, "the count is reported either way"


def test_single_class_sample_reports_a_reason_and_no_numbers() -> None:
    patients = np.repeat(np.arange(40), 2)
    result = cluster_bootstrap(
        patients,
        {"auroc": lambda weights: auroc(np.zeros(80, dtype=int), np.linspace(0, 1, 80), weights)},
        BootstrapSettings(replicates=50, seed=1),
    )["auroc"]
    assert (result.point, result.lower, result.upper, result.completed) == (None, None, None, 0)
    assert "no positive" in (result.reason or "")


def test_one_patient_cannot_be_bootstrapped() -> None:
    result = cluster_bootstrap(
        np.zeros(10, dtype=int),
        {"mean": _weighted_mean(np.arange(10.0))},
        BootstrapSettings(replicates=50),
    )["mean"]
    assert result.point == pytest.approx(4.5)
    assert not result.estimable
    assert "at least two" in (result.reason or "")


def test_a_difference_is_not_estimable_when_either_side_is_not() -> None:
    patients = np.repeat(np.arange(40), 2)
    scores = np.linspace(0, 1, 80)
    mixed = (np.arange(80) % 3 == 0).astype(int)
    statistics = {
        "fine": lambda weights: auroc(mixed, scores, weights),
        "single_class": lambda weights: auroc(np.zeros(80, dtype=int), scores, weights),
    }
    result = cluster_bootstrap(
        patients,
        statistics,
        BootstrapSettings(replicates=50, seed=1),
        differences={"gap": ("fine", "single_class")},
    )
    assert result["fine"].estimable
    assert (result["gap"].point, result["gap"].lower) == (None, None)
    assert "no positive" in (result["gap"].reason or "")
