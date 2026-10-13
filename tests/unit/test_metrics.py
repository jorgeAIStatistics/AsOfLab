"""The metric implementations, checked against scikit-learn and against duplicated rows."""

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from asoflab.errors import NotEstimable
from asoflab.evaluation.metrics import (
    MIN_PATIENTS,
    auroc,
    average_precision,
    brier,
    calibration_curve,
    calibration_intercept,
    calibration_slope,
    evaluate,
    instability,
    patient_weights,
)

# Scores on a coarse grid so that ties between positives and negatives are common.
scored_samples = st.lists(
    st.tuples(st.integers(0, 1), st.integers(0, 20).map(lambda k: k / 20), st.integers(0, 3)),
    min_size=4,
    max_size=40,
).filter(lambda rows: {row[0] for row in rows if row[2] > 0} == {0, 1})


def _arrays(rows):  # type: ignore[no-untyped-def]
    labels = np.array([row[0] for row in rows])
    scores = np.array([row[1] for row in rows], dtype=float)
    weights = np.array([row[2] for row in rows], dtype=float)
    return labels, scores, weights


@given(rows=scored_samples)
def test_weighted_metrics_match_scikit_learn(rows) -> None:  # type: ignore[no-untyped-def]
    labels, scores, weights = _arrays(rows)
    assert auroc(labels, scores, weights) == pytest.approx(
        roc_auc_score(labels, scores, sample_weight=weights)
    )
    assert average_precision(labels, scores, weights) == pytest.approx(
        average_precision_score(labels, scores, sample_weight=weights)
    )
    assert brier(labels, scores, weights) == pytest.approx(
        brier_score_loss(labels, scores, sample_weight=weights)
    )


@given(rows=scored_samples)
def test_an_integer_weight_means_exactly_that_many_copies(rows) -> None:  # type: ignore[no-untyped-def]
    """This is what lets a bootstrap resample be expressed as weights."""
    labels, scores, weights = _arrays(rows)
    copies = weights.astype(int)
    repeated_labels, repeated_scores = np.repeat(labels, copies), np.repeat(scores, copies)
    for metric in (auroc, average_precision, brier):
        assert metric(labels, scores, weights) == pytest.approx(
            metric(repeated_labels, repeated_scores)
        )


def test_hand_worked_values() -> None:
    labels = np.array([0, 0, 1, 1])
    scores = np.array([0.1, 0.4, 0.35, 0.8])
    # Positive-negative pairs: (0.35, 0.1) won, (0.35, 0.4) lost, (0.8, 0.1) won, (0.8, 0.4) won.
    assert auroc(labels, scores) == pytest.approx(3 / 4)
    # Thresholds 0.8, 0.4, 0.35: precision 1, 1/2, 2/3 with recall steps 1/2, 0, 1/2.
    assert average_precision(labels, scores) == pytest.approx(0.5 * 1.0 + 0.5 * (2 / 3))
    assert brier(labels, scores) == pytest.approx((0.01 + 0.16 + 0.4225 + 0.04) / 4)
    assert auroc(np.array([0, 1]), np.array([0.5, 0.5])) == pytest.approx(0.5), (
        "a tie counts one half"
    )


@pytest.mark.parametrize(
    "metric", [auroc, average_precision, calibration_intercept, calibration_slope]
)
@pytest.mark.parametrize(
    ("labels", "reason"), [([1, 1, 1], "no negative"), ([0, 0, 0], "no positive")]
)
def test_single_class_samples_are_not_estimable(metric, labels, reason) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(NotEstimable, match=reason):
        metric(np.array(labels), np.array([0.2, 0.5, 0.7]))


def test_zero_weight_can_remove_a_class() -> None:
    labels, scores = np.array([0, 1, 1]), np.array([0.2, 0.5, 0.7])
    with pytest.raises(NotEstimable, match="no negative"):
        auroc(labels, scores, np.array([0.0, 1.0, 2.0]))
    with pytest.raises(NotEstimable, match="empty"):
        brier(labels, scores, np.zeros(3))


def test_too_few_patients_gives_a_reason_not_a_number() -> None:
    labels = np.array([0, 1] * 4)
    scores = np.linspace(0.1, 0.9, 8)
    few = np.repeat(np.arange(MIN_PATIENTS - 1), 2)[:8]
    results = evaluate(labels, scores, few)
    assert all(
        not estimate.estimable and "patients" in (estimate.reason or "")
        for estimate in results.values()
    )
    enough = evaluate(
        np.array([0, 1] * MIN_PATIENTS),
        np.linspace(0.1, 0.9, 2 * MIN_PATIENTS),
        np.repeat(np.arange(MIN_PATIENTS), 2),
    )
    assert enough["auroc"].estimable
    assert enough["auroc"].reason is None
    single_class = evaluate(
        np.zeros(2 * MIN_PATIENTS, dtype=int),
        np.linspace(0.1, 0.9, 2 * MIN_PATIENTS),
        np.repeat(np.arange(MIN_PATIENTS), 2),
    )
    assert not single_class["auroc"].estimable
    assert "no positive" in (single_class["auroc"].reason or "")
    assert single_class["brier"].estimable, "the Brier score is defined for one class"


def test_calibration_measures_recover_a_known_miscalibration() -> None:
    rng = np.random.default_rng(5)
    true_logit = rng.normal(-1.0, 1.2, size=40_000)
    labels = (rng.random(40_000) < 1 / (1 + np.exp(-true_logit))).astype(int)
    # Predictions that are too extreme by a factor of 2 and shifted by +0.5 on the logit scale.
    reported = 1 / (1 + np.exp(-(2.0 * true_logit + 0.5)))
    assert calibration_slope(labels, reported) == pytest.approx(0.5, abs=0.02)
    perfect = 1 / (1 + np.exp(-true_logit))
    assert calibration_slope(labels, perfect) == pytest.approx(1.0, abs=0.03)
    assert calibration_intercept(labels, perfect) == pytest.approx(0.0, abs=0.03)
    # Independent check of the slope against scikit-learn's unpenalised fit.
    logit = np.log(reported / (1 - reported)).reshape(-1, 1)
    reference = LogisticRegression(C=np.inf, max_iter=1000).fit(logit, labels)
    assert calibration_slope(labels, reported) == pytest.approx(
        float(reference.coef_[0][0]), rel=1e-4
    )


def test_calibration_curve_bins_account_for_every_prediction() -> None:
    rng = np.random.default_rng(1)
    scores = rng.random(1003)
    labels = (rng.random(1003) < scores).astype(int)
    curve = calibration_curve(labels, scores, bins=10)
    assert sum(point["predictions"] for point in curve) == 1003
    assert sum(point["events"] for point in curve) == int(labels.sum())
    means = [point["mean_predicted"] for point in curve]
    assert means == sorted(means)


def test_patient_weights_give_each_patient_the_same_total() -> None:
    patients = np.array(["a", "a", "a", "b", "c", "c"])
    weights = patient_weights(patients)
    assert [round(float(weights[patients == p].sum()), 12) for p in "abc"] == [1.0, 1.0, 1.0]


def test_small_samples_are_flagged_as_unstable() -> None:
    labels = np.array([1] * 5 + [0] * 200)
    flags = instability(labels, np.arange(205))
    assert flags == ["fewer than 20 events"]
    assert "fewer than 30 patients" in instability(labels, np.repeat(np.arange(5), 41))
