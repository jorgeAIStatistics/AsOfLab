"""The baseline model: regularised logistic regression with imputation and scaling.

Everything that learns from data (imputation medians, missing-value
indicators, scaling, coefficients, the regularisation strength) is fitted on
training data, and the regularisation strength is chosen on validation data.
The test set is used once, to evaluate.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

REGULARISATION_GRID = (0.001, 0.01, 0.1, 1.0, 10.0)


@dataclass
class FittedModel:
    pipeline: Pipeline
    feature_names: list[str]
    chosen_c: float
    validation_log_loss: dict[float, float]

    def predict(self, features: np.ndarray) -> np.ndarray:
        probabilities: np.ndarray = self.pipeline.predict_proba(features)[:, 1]
        return probabilities

    def describe(self) -> dict[str, Any]:
        classifier = self.pipeline[-1]
        imputer = self.pipeline[0]
        names = list(self.feature_names) + [
            f"{self.feature_names[index]}__missing" for index in imputer.indicator_.features_
        ]
        return {
            "model": "logistic regression, L2 penalty",
            "preprocessing": "median imputation with missing-value indicators, then standardisation",
            "regularisation_grid_C": list(REGULARISATION_GRID),
            "selected_C": self.chosen_c,
            "selection_rule": "lowest log loss on the validation patients",
            "validation_log_loss_by_C": {
                str(c): loss for c, loss in self.validation_log_loss.items()
            },
            "intercept": float(classifier.intercept_[0]),
            "standardised_coefficients": dict(
                zip(names, map(float, classifier.coef_[0]), strict=True)
            ),
        }


def _build(c: float) -> Pipeline:
    return make_pipeline(
        SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True),
        StandardScaler(),
        LogisticRegression(C=c, max_iter=2000),
    )


def fit_model(
    train_features: np.ndarray,
    train_labels: np.ndarray,
    validation_features: np.ndarray,
    validation_labels: np.ndarray,
    feature_names: list[str],
) -> FittedModel:
    """Fit on train for each candidate strength, keep the one with the lowest validation log loss."""
    if len(np.unique(train_labels)) < 2:
        raise ValueError("the training labels contain a single class; a model cannot be fitted")
    losses: dict[float, float] = {}
    candidates: dict[float, Pipeline] = {}
    for c in REGULARISATION_GRID:
        pipeline = _build(c).fit(train_features, train_labels)
        candidates[c] = pipeline
        predicted = pipeline.predict_proba(validation_features)[:, 1]
        losses[c] = float(log_loss(validation_labels, predicted, labels=[0, 1]))
    chosen = min(losses, key=lambda c: (losses[c], c))
    return FittedModel(candidates[chosen], list(feature_names), chosen, losses)
