"""The statistical demonstration, end to end, on one simulated cohort.

Question: for one prediction task, how much does the measured performance
change when features are built with timing and revision mistakes?

Fixed before any result was computed:

- Target: a deterioration event with event time in (T, T + 12 h], same encounter.
- Eligible population: predictions issued every 4 h during an encounter while
  the patient is still at risk (no earlier event in that encounter).
- Unit of analysis: the prediction. Metrics weight predictions equally by
  default; a patient-weighted view is reported alongside.
- Uncertainty: paired bootstrap over patients, conditional on the fitted models.
- Reference: features as known to the pipeline (`pipeline_as_known`).

Two experiments, which answer different questions:

A. Frozen model. One model is trained on as-known features. Its parameters,
   the prediction rows, the labels and the test patients are held fixed. Only
   the policy used to build the test features changes. This measures how
   sensitive the *evaluation* is to the data pipeline. It is not a comparison
   of models.

B. Training pipelines. Two models are trained on the same patients and labels,
   one from naive features and one from as-known features. Each is evaluated
   both ways. The naive pipeline's own retrospective number (naive features in,
   naive features out) is what a team would report; the same model on as-known
   features is what deployment would deliver.
"""

from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from asoflab import CONTRACT_VERSION, __version__
from asoflab.audit.differential import differential_audit
from asoflab.audit.manifest import MANIFEST_NAME, build_manifest, stable_hash, write_json
from asoflab.audit.parity import compare_feature_tables
from asoflab.batch import compute_features
from asoflab.engines import run_engine
from asoflab.evaluation.bootstrap import BootstrapSettings, cluster_bootstrap
from asoflab.evaluation.labels import (
    assign_labels,
    count_premature_negatives,
    label_coverage,
    labels_recorded_at_horizon,
)
from asoflab.evaluation.metrics import (
    DISCRIMINATION_AND_ACCURACY,
    METRICS,
    MIN_PATIENTS,
    ScoredSample,
    calibration_curve,
    instability,
    patient_weights,
    sample_description,
)
from asoflab.evaluation.model import FittedModel, fit_model
from asoflab.evaluation.splits import (
    TEST,
    TRAIN,
    VALIDATION,
    chronological_split,
    patient_split,
    split_counts,
    split_hash,
)
from asoflab.schema.models import TimePolicy
from asoflab.schema.tables import content_hash, write_parquet
from asoflab.schema.validation import validate_inputs
from asoflab.simulation.clinical import (
    CLINICAL_GENERATOR_VERSION,
    SimulatedCohort,
    SimulationConfig,
    clinical_features,
    simulate,
)

REFERENCE = "pipeline_as_known"
REVISED = "latest_revised"
NAIVE = "naive_event_time"
POLICY_SLUGS = (REFERENCE, "source_as_known", REVISED, NAIVE)


@dataclass(frozen=True)
class AnalysisSettings:
    bootstrap: BootstrapSettings = field(default_factory=BootstrapSettings)
    subgroup_replicates: int = 500
    split_seed: int = 1
    chronological_cutoff_day: int = 120
    data_cut_day: int = 150
    replay_parity: bool = True
    oracle_sample_patients: int = 25
    max_audit_examples: int = 25

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Frame:
    """Everything aligned row by row, sorted by prediction_id."""

    prediction_ids: np.ndarray
    patients: np.ndarray
    labels: np.ndarray  # 1.0, 0.0, or NaN when the label is unknown
    features: dict[str, np.ndarray]
    names: list[str]

    @property
    def labelled(self) -> np.ndarray:
        mask: np.ndarray = np.isfinite(self.labels)
        return mask


def run_experiment(
    config: SimulationConfig, settings: AnalysisSettings, out_root: str | Path
) -> Path:
    """Simulate, compute features under every policy, evaluate, audit, and write one run directory."""
    feature_config = clinical_features()
    identity = {
        "generator_version": CLINICAL_GENERATOR_VERSION,
        "generator_config_hash": config.config_hash(),
        "feature_config_hash": feature_config.config_hash(),
        "contract_version": CONTRACT_VERSION,
        "asoflab_version": __version__,
        "analysis_settings_hash": stable_hash(settings.to_dict()),
    }
    run_id = f"{config.scenario.name}-seed{config.seed}-{stable_hash(identity)[:8]}"
    directory = Path(out_root) / run_id
    if (directory / MANIFEST_NAME).exists():
        raise FileExistsError(
            f"{directory} already holds a completed run. Runs are never overwritten."
        )

    cohort = simulate(config)
    cohort.write(directory / "data")
    policies = {slug: TimePolicy.parse(slug) for slug in POLICY_SLUGS}
    validation = validate_inputs(cohort.events, cohort.spine, policies[REFERENCE])

    tables = {
        slug: compute_features(cohort.events, cohort.spine, feature_config, policy)
        for slug, policy in policies.items()
    }
    for slug, table in tables.items():
        write_parquet(table, directory / "features" / f"{slug}.parquet")

    label_spec = config.label_spec()
    labels = assign_labels(cohort.spine, cohort.outcomes, cohort.follow_up, label_spec).sort_by(
        "prediction_id"
    )
    write_parquet(labels, directory / "labels.parquet")
    frame = Frame(
        prediction_ids=np.array(labels.column("prediction_id").to_pylist()),
        patients=np.array(labels.column("entity_id").to_pylist()),
        labels=np.array(
            [
                np.nan if value is None else float(value)
                for value in labels.column("label").to_pylist()
            ]
        ),
        features={slug: _matrix(table, feature_config.names) for slug, table in tables.items()},
        names=feature_config.names,
    )
    for table in tables.values():
        assert table.column("prediction_id").to_pylist() == frame.prediction_ids.tolist()

    assignment = patient_split(frame.patients.tolist(), settings.split_seed)
    parts = np.array([assignment[patient] for patient in frame.patients])

    model_as_known = _fit(frame, REFERENCE, parts)
    model_naive = _fit(frame, NAIVE, parts)
    test = (parts == TEST) & frame.labelled

    experiment_a = _experiment_a(frame, model_as_known, test, tables, settings)
    experiment_b = _experiment_b(frame, model_as_known, model_naive, test, settings)
    chronological = _chronological(cohort, frame, labels, config, settings)
    subgroups = _subgroups(cohort, frame, model_as_known, test, settings)
    maturity = _label_maturity(cohort, config, settings, frame, labels, model_as_known, test)

    write_parquet(
        _predictions_table(labels, parts, frame, model_as_known, model_naive),
        directory / "predictions.parquet",
    )
    write_parquet(
        pa.table(
            {
                "entity_id": sorted(assignment),
                "split": [assignment[key] for key in sorted(assignment)],
            }
        ),
        directory / "splits.parquet",
    )

    audit = _audit(cohort, frame, tables, policies, validation, feature_config, settings, directory)
    statistics = {
        "run_id": run_id,
        "synthetic_data": True,
        "scenario": config.scenario.model_dump(),
        "seed": config.seed,
        "target": _target_description(config),
        "cohort": {
            "patients": cohort.patients.num_rows,
            "encounters": cohort.encounters.num_rows,
            "event_versions": cohort.events.num_rows,
            "predictions": cohort.spine.num_rows,
            "outcome_events": cohort.outcomes.num_rows,
        },
        "label_coverage": label_coverage(labels),
        "splits": {
            "patient_holdout": {
                "seed": settings.split_seed,
                "hash": split_hash(assignment),
                "counts": split_counts(parts.tolist(), frame.patients.tolist()),
            }
        },
        "missing_feature_rates": {
            slug: _missing_rates(frame.features[slug], frame.names) for slug in POLICY_SLUGS
        },
        "weighting": "Metrics weight predictions equally unless marked patient-weighted.",
        "bootstrap": settings.bootstrap.to_dict(),
        "experiment_a": experiment_a,
        "experiment_b": experiment_b,
        "chronological": chronological,
        "subgroups": subgroups,
        "label_maturity": maturity,
        "limits": LIMITS,
    }
    write_json(directory / "statistics.json", statistics)
    write_json(directory / "audit.json", audit)

    manifest = build_manifest(
        run_id=run_id,
        kind="clinical_experiment",
        directory=directory,
        synthetic=True,
        identity=identity,
        details={
            "generator": {
                "version": CLINICAL_GENERATOR_VERSION,
                "config": config.model_dump(mode="json"),
            },
            "seed": config.seed,
            "scenario": config.scenario.name,
            "data_hashes": cohort.hashes(),
            "schema_hashes": {
                name: stable_hash(str(getattr(cohort, name).schema))
                for name in SimulatedCohort.TABLES
            },
            "feature_config": feature_config.canonical(),
            "time_policies": [
                {
                    "policy": slug,
                    "strictness": policy.strictness.value,
                    "faithful_replay": policy.faithful_replay,
                    "feature_table_hash": content_hash(tables[slug], ["prediction_id"]),
                }
                for slug, policy in policies.items()
            ],
            "reference_policy": REFERENCE,
            "label_spec": label_spec.model_dump(),
            "label_cutoff": None,
            "label_cutoff_note": "Main analysis uses the completed study: every outcome ascertained.",
            "splits": {
                "patient_holdout": {"seed": settings.split_seed, "hash": split_hash(assignment)},
                "chronological": chronological.get("split"),
            },
            "analysis_settings": settings.to_dict(),
        },
        output_files=[
            "statistics.json",
            "audit.json",
            "labels.parquet",
            "predictions.parquet",
            "splits.parquet",
        ],
    )
    write_json(directory / MANIFEST_NAME, manifest)
    return directory


LIMITS = [
    "All data are simulated. Nothing here is evidence about any real patient, hospital, or model.",
    "The size and direction of every difference depend on the simulation's settings, which were "
    "fixed in advance and are not estimates of any real system.",
    "Bootstrap intervals describe sampling variability in the test patients for models held "
    "fixed. They exclude the variability of model fitting and of the simulation itself.",
    "A lower score under the as-known policy does not mean a worse engine. It means the naive "
    "number was measuring something the deployed pipeline could not have had.",
    "Timestamp rules cannot establish clinical appropriateness, causality, or data validity.",
    "Subgroup results are descriptive. They support no claim about fairness, safety, or benefit.",
]


def _matrix(table: pa.Table, names: list[str]) -> np.ndarray:
    columns = [
        np.asarray(table.column(name).to_numpy(zero_copy_only=False), dtype=float) for name in names
    ]
    return np.column_stack(columns)


def _missing_rates(matrix: np.ndarray, names: list[str]) -> dict[str, float]:
    return {name: float(np.mean(np.isnan(matrix[:, index]))) for index, name in enumerate(names)}


def _fit(frame: Frame, slug: str, parts: np.ndarray) -> FittedModel:
    train = (parts == TRAIN) & frame.labelled
    validation = (parts == VALIDATION) & frame.labelled
    return fit_model(
        frame.features[slug][train],
        frame.labels[train].astype(int),
        frame.features[slug][validation],
        frame.labels[validation].astype(int),
        frame.names,
    )


def _evaluate(
    labels: np.ndarray,
    patients: np.ndarray,
    scores: dict[str, np.ndarray],
    differences: dict[str, tuple[str, str]],
    settings: BootstrapSettings,
    *,
    metrics: tuple[str, ...] = tuple(METRICS),
    base_weights: np.ndarray | None = None,
    labels_by_condition: dict[str, np.ndarray] | None = None,
) -> dict[str, Any]:
    """Point estimates and patient-bootstrap intervals for every condition and paired difference.

    Conditions normally share `labels` and differ in their scores. `labels_by_condition`
    lets a condition use different labels for the same rows, which is how the effect of
    a labelling mistake is measured.
    """
    labels_by_condition = labels_by_condition or {}
    samples = {
        name: ScoredSample(labels_by_condition.get(name, labels), values)
        for name, values in scores.items()
    }
    statistics = {
        f"{condition}|{metric}": getattr(sample, metric)
        for condition, sample in samples.items()
        for metric in metrics
    }
    paired = {
        f"{name}|{metric}": (f"{minuend}|{metric}", f"{subtrahend}|{metric}")
        for name, (minuend, subtrahend) in differences.items()
        for metric in DISCRIMINATION_AND_ACCURACY
    }
    n_patients = len(np.unique(patients))
    if n_patients < MIN_PATIENTS:
        reason = f"only {n_patients} patients; at least {MIN_PATIENTS} are required"
        empty = {"point": None, "lower": None, "upper": None, "estimable": False, "reason": reason,
                 "bootstrap_requested": settings.replicates, "bootstrap_completed": 0}  # fmt: skip
        return {
            "conditions": {name: {metric: dict(empty) for metric in metrics} for name in scores},
            "differences": {
                name: {"minuend": pair[0], "subtrahend": pair[1],
                       **{metric: dict(empty) for metric in DISCRIMINATION_AND_ACCURACY}}
                for name, pair in differences.items()
            },
        }  # fmt: skip
    intervals = cluster_bootstrap(patients, statistics, settings, base_weights, paired)
    return {
        "conditions": {
            name: {metric: intervals[f"{name}|{metric}"].to_dict() for metric in metrics}
            for name in scores
        },
        "differences": {
            name: {
                "minuend": pair[0],
                "subtrahend": pair[1],
                **{
                    metric: intervals[f"{name}|{metric}"].to_dict()
                    for metric in DISCRIMINATION_AND_ACCURACY
                },
            }
            for name, pair in differences.items()
        },
    }


def _experiment_a(
    frame: Frame,
    model: FittedModel,
    test: np.ndarray,
    tables: dict[str, pa.Table],
    settings: AnalysisSettings,
) -> dict[str, Any]:
    labels = frame.labels[test].astype(int)
    patients = frame.patients[test]
    scores = {slug: model.predict(frame.features[slug][test]) for slug in POLICY_SLUGS}
    differences = {
        f"{slug}_minus_{REFERENCE}": (slug, REFERENCE) for slug in POLICY_SLUGS if slug != REFERENCE
    }
    # Sequential decomposition of the naive-versus-as-known gap.
    differences["revision_effect"] = (REVISED, REFERENCE)
    differences["availability_effect"] = (NAIVE, REVISED)
    result = _evaluate(labels, patients, scores, differences, settings.bootstrap)
    weighted = _evaluate(
        labels,
        patients,
        scores,
        differences,
        settings.bootstrap,
        metrics=DISCRIMINATION_AND_ACCURACY,
        base_weights=patient_weights(patients),
    )
    return {
        "question": "With the model, prediction rows, labels and test patients held fixed, how much "
        "does the measured performance change when only the feature-time policy changes?",
        "interpretation": "Pipeline sensitivity of the evaluation. Not a comparison between "
        "independently trained models.",
        "model_trained_under": REFERENCE,
        "model": model.describe(),
        "evaluated_on": "held-out test patients",
        "sample": sample_description(labels, patients),
        "instability_flags": instability(labels, patients),
        "features_identical_to_reference": {
            slug: bool(tables[slug].equals(tables[REFERENCE])) for slug in POLICY_SLUGS
        },
        "prediction_weighted": result,
        "patient_weighted": weighted,
        "decomposition_note": "revision_effect = latest_revised - pipeline_as_known (only the "
        "revision read changes). availability_effect = naive_event_time - latest_revised (only "
        "whether availability is respected changes). Their sum is the naive-minus-as-known gap. "
        "The split depends on the order in which the two changes are applied.",
        "calibration_curves": {
            slug: calibration_curve(labels, scores[slug]) for slug in POLICY_SLUGS
        },
    }


def _experiment_b(
    frame: Frame,
    model_as_known: FittedModel,
    model_naive: FittedModel,
    test: np.ndarray,
    settings: AnalysisSettings,
) -> dict[str, Any]:
    labels = frame.labels[test].astype(int)
    patients = frame.patients[test]
    cells = {
        "naive_trained__naive_evaluated": model_naive.predict(frame.features[NAIVE][test]),
        "naive_trained__as_known_evaluated": model_naive.predict(frame.features[REFERENCE][test]),
        "as_known_trained__as_known_evaluated": model_as_known.predict(
            frame.features[REFERENCE][test]
        ),
        "as_known_trained__naive_evaluated": model_as_known.predict(frame.features[NAIVE][test]),
    }
    differences = {
        "optimism_of_the_naive_pipeline": (
            "naive_trained__naive_evaluated",
            "naive_trained__as_known_evaluated",
        ),
        "as_known_training_minus_naive_training_at_deployment": (
            "as_known_trained__as_known_evaluated",
            "naive_trained__as_known_evaluated",
        ),
        "reported_naive_minus_reported_as_known": (
            "naive_trained__naive_evaluated",
            "as_known_trained__as_known_evaluated",
        ),
    }
    return {
        "question": "If the whole pipeline is built naively, how far is the number it reports from "
        "what the same model would achieve on features it could really have had, and does "
        "training on as-known features help at deployment?",
        "design": "Two models, same patients, same labels, same split, same model family and "
        "selection rule. Only the policy that prepared the training and validation features differs.",
        "deployment_faithful_cell": "any cell evaluated on as-known features",
        "models": {
            "naive_trained": model_naive.describe(),
            "as_known_trained": model_as_known.describe(),
        },
        "sample": sample_description(labels, patients),
        "prediction_weighted": _evaluate(labels, patients, cells, differences, settings.bootstrap),
        "can_attribute": "The contrasts isolate the effect of the feature-time policy on this "
        "cohort for these two fitted models.",
        "cannot_attribute": "Intervals are conditional on the two fitted models. They do not show "
        "that one training pipeline is better in general; that needs the variation across "
        "simulated cohorts reported in the cross-seed summary, and would need real data to say "
        "anything about a real system.",
    }


def _chronological(
    cohort: SimulatedCohort,
    frame: Frame,
    labels: pa.Table,
    config: SimulationConfig,
    settings: AnalysisSettings,
) -> dict[str, Any]:
    """Train on what was label-mature at a cutoff date; test on patients first seen afterwards."""
    cutoff = config.study_start + timedelta(days=settings.chronological_cutoff_day)
    parts_list = chronological_split(labels, cutoff, settings.split_seed)
    parts = np.array(parts_list)
    known_at_cutoff = assign_labels(
        cohort.spine, cohort.outcomes, cohort.follow_up, config.label_spec(), as_of=cutoff
    ).sort_by("prediction_id")
    then = np.array(
        [np.nan if v is None else float(v) for v in known_at_cutoff.column("label").to_pylist()]
    )
    fitting = np.isin(parts, [TRAIN, VALIDATION])
    disagreements = int(np.sum(then[fitting] != frame.labels[fitting]))
    split = {
        "cutoff": cutoff.isoformat(),
        "hash": split_hash(parts_list),
        "counts": split_counts(parts_list, frame.patients.tolist()),
        "rules": [
            "A prediction is used for fitting only if its label was mature at the cutoff: "
            "prediction time + horizon + maximum ascertainment lag <= cutoff.",
            "Fitting labels are computed from outcomes ascertained by the cutoff.",
            "Test predictions are those after the cutoff from patients first seen after the cutoff.",
            "A patient seen before the cutoff who returns afterwards is excluded from the test set.",
        ],
        "fitting_labels_that_differ_from_final_labels": disagreements,
    }
    result: dict[str, Any] = {
        "question": "How would a model built from what was known at the cutoff date have performed "
        "on the patients who arrived afterwards?",
        "differs_from_patient_holdout": "The patient holdout asks about new patients from the same "
        "period. This asks about later patients. The simulation has no drift over calendar time, "
        "so no systematic difference between the two is expected here; the design is shown "
        "because real data would need it.",
        "split": split,
    }
    train = (parts == TRAIN) & np.isfinite(then)
    validation = (parts == VALIDATION) & np.isfinite(then)
    test = (parts == TEST) & frame.labelled
    if len(np.unique(then[train])) < 2 or len(np.unique(then[validation])) < 2:
        result["estimable"] = False
        result["reason"] = "the fitting data available at the cutoff contain a single class"
        return result
    model = fit_model(
        frame.features[REFERENCE][train],
        then[train].astype(int),
        frame.features[REFERENCE][validation],
        then[validation].astype(int),
        frame.names,
    )
    test_labels = frame.labels[test].astype(int)
    scores = {slug: model.predict(frame.features[slug][test]) for slug in POLICY_SLUGS}
    differences = {
        f"{slug}_minus_{REFERENCE}": (slug, REFERENCE) for slug in POLICY_SLUGS if slug != REFERENCE
    }
    result.update(
        {
            "estimable": True,
            "model": model.describe(),
            "sample": sample_description(test_labels, frame.patients[test]),
            "instability_flags": instability(test_labels, frame.patients[test]),
            "prediction_weighted": _evaluate(
                test_labels,
                frame.patients[test],
                scores,
                differences,
                settings.bootstrap,
                metrics=DISCRIMINATION_AND_ACCURACY,
            ),
        }
    )
    return result


def _subgroups(
    cohort: SimulatedCohort,
    frame: Frame,
    model: FittedModel,
    test: np.ndarray,
    settings: AnalysisSettings,
) -> dict[str, Any]:
    """Prespecified, descriptive subgroup summaries under the reference policy."""
    attributes = {row["entity_id"]: row for row in cohort.patients.to_pylist()}
    subgroup_settings = BootstrapSettings(
        replicates=settings.subgroup_replicates,
        seed=settings.bootstrap.seed,
        level=settings.bootstrap.level,
        min_completed_fraction=settings.bootstrap.min_completed_fraction,
    )
    scores = model.predict(frame.features[REFERENCE])
    result: dict[str, Any] = {
        "prespecified": ["site", "age_band"],
        "purpose": "Descriptive only. No claim of fairness, clinical safety, or causal benefit.",
        "policy": REFERENCE,
        "bootstrap_replicates": settings.subgroup_replicates,
        "groups": {},
    }
    for attribute in ("site", "age_band"):
        values = np.array([attributes[patient][attribute] for patient in frame.patients])
        result["groups"][attribute] = {}
        for level in sorted(set(values.tolist())):
            mask = test & (values == level)
            labels = frame.labels[mask].astype(int)
            patients = frame.patients[mask]
            evaluated = _evaluate(
                labels,
                patients,
                {REFERENCE: scores[mask]},
                {},
                subgroup_settings,
                metrics=DISCRIMINATION_AND_ACCURACY,
            )
            flags = instability(labels, patients)
            result["groups"][attribute][level] = {
                "sample": sample_description(labels, patients),
                "unstable": bool(flags),
                "instability_flags": flags,
                "metrics": evaluated["conditions"][REFERENCE],
            }
    return result


def _label_maturity(
    cohort: SimulatedCohort,
    config: SimulationConfig,
    settings: AnalysisSettings,
    frame: Frame,
    labels: pa.Table,
    model: FittedModel,
    test: np.ndarray,
) -> dict[str, Any]:
    """Pending and unknown labels at a data cut, and the cost of labelling without waiting."""
    spec = config.label_spec()
    cut = config.study_start + timedelta(days=settings.data_cut_day)
    at_cut = assign_labels(cohort.spine, cohort.outcomes, cohort.follow_up, spec, as_of=cut)

    # Same rows, same scores, two sets of labels: the mature label of record, and the label an
    # impatient pipeline would have written down the moment each horizon ended.
    impatient = labels_recorded_at_horizon(
        labels.select(cohort.spine.column_names), cohort.outcomes, cohort.follow_up, spec
    )
    impatient_labels = np.array([np.nan if value is None else float(value) for value in impatient])
    usable = test & np.isfinite(impatient_labels)
    mature = frame.labels[usable].astype(int)
    hasty = impatient_labels[usable].astype(int)
    scores = model.predict(frame.features[REFERENCE][usable])
    comparison = _evaluate(
        mature,
        frame.patients[usable],
        {"mature_labels": scores, "labels_recorded_at_horizon": scores},
        {"recorded_at_horizon_minus_mature": ("labels_recorded_at_horizon", "mature_labels")},
        settings.bootstrap,
        metrics=DISCRIMINATION_AND_ACCURACY,
        labels_by_condition={"labels_recorded_at_horizon": hasty},
    )
    return {
        "purpose": "Shows pending and unknown labels, and what is lost by labelling too early. "
        "The main analysis uses the completed study with mature labels.",
        "data_cut": cut.isoformat(),
        "horizon_hours": spec.horizon_seconds / 3600,
        "max_ascertainment_lag_hours": spec.max_ascertainment_lag_seconds / 3600,
        "coverage_at_data_cut": label_coverage(at_cut),
        "unknown_labels_set_to_zero": 0,
        "premature_negatives_if_labelled_at_the_horizon": count_premature_negatives(
            cohort.spine, cohort.outcomes, spec
        ),
        "premature_negatives_meaning": "Predictions whose event happened inside the horizon but had "
        "not been ascertained when the horizon ended. A pipeline that labels as soon as the "
        "horizon elapses records each of them as a negative.",
        "evaluation_against_impatient_labels": {
            "design": "Frozen as-known model, as-known features, held-out test patients. Only the "
            "labels differ: mature labels of record versus labels recorded at T + H.",
            "test_predictions": int(usable.sum()),
            "positives_with_mature_labels": int(mature.sum()),
            "positives_with_labels_recorded_at_horizon": int(hasty.sum()),
            "prevalence_with_mature_labels": float(mature.mean()),
            "prevalence_with_labels_recorded_at_horizon": float(hasty.mean()),
            "prediction_weighted": comparison,
        },
    }


def _predictions_table(
    labels: pa.Table,
    parts: np.ndarray,
    frame: Frame,
    model_as_known: FittedModel,
    model_naive: FittedModel,
) -> pa.Table:
    columns: dict[str, Any] = {name: labels.column(name) for name in labels.column_names}
    columns["split"] = pa.array(parts.tolist(), pa.string())
    for slug in POLICY_SLUGS:
        columns[f"p_as_known_model__{slug}"] = model_as_known.predict(frame.features[slug])
    for slug in (NAIVE, REFERENCE):
        columns[f"p_naive_model__{slug}"] = model_naive.predict(frame.features[slug])
    return pa.table(columns)


def _target_description(config: SimulationConfig) -> dict[str, Any]:
    parameters = config.parameters
    return {
        "definition": f"A simulated deterioration event with event time in (T, T + "
        f"{parameters.horizon_hours} h], in the same encounter as the prediction at time T.",
        "horizon_hours": parameters.horizon_hours,
        "prediction_schedule": f"Every {parameters.prediction_interval_hours} h from hour "
        f"{parameters.first_prediction_hour} of an encounter until it ends.",
        "eligible_population": "Predictions made while the patient is at risk: no earlier event in "
        "the encounter. Follow-up covers the full horizon for every prediction.",
        "unit_of_analysis": "prediction",
        "estimand": "Discrimination, average precision and Brier score of a fixed model's "
        "predictions for the eligible predictions of held-out patients, under a stated feature-time policy.",
        "average_precision_convention": "Step-function average precision: sum over thresholds of "
        "(recall_k - recall_{k-1}) * precision_k. Not the trapezoidal area under the PR curve.",
    }


def _audit(
    cohort: SimulatedCohort,
    frame: Frame,
    tables: dict[str, pa.Table],
    policies: dict[str, TimePolicy],
    validation: Any,
    feature_config: Any,
    settings: AnalysisSettings,
    directory: Path,
) -> dict[str, Any]:
    naive_audit = differential_audit(
        cohort.events, cohort.spine, feature_config, policies[REFERENCE], policies[NAIVE],
        max_examples=settings.max_audit_examples,
    )  # fmt: skip
    revised_audit = differential_audit(
        cohort.events,
        cohort.spine,
        feature_config,
        policies[REFERENCE],
        policies[REVISED],
        max_examples=0,
    )
    write_parquet(naive_audit.findings, directory / "audit_findings.parquet")

    parity: dict[str, Any] = {"replay_vs_batch": {}, "oracle_vs_batch_on_a_patient_sample": {}}
    if settings.replay_parity:
        for slug, policy in policies.items():
            replayed = run_engine(
                "replay", cohort.events, cohort.spine, feature_config, policy, n_partitions=4
            )
            parity["replay_vs_batch"][slug] = compare_feature_tables(
                tables[slug], replayed, feature_config, left_name="batch", right_name="replay"
            ).to_dict()
    sample = sorted(set(frame.patients.tolist()))[: settings.oracle_sample_patients]
    events_sample = cohort.events.filter(
        pc.is_in(cohort.events.column("entity_id"), pa.array(sample))
    )
    spine_sample = cohort.spine.filter(pc.is_in(cohort.spine.column("entity_id"), pa.array(sample)))
    for slug, policy in policies.items():
        batch_sample = tables[slug].filter(
            pc.is_in(tables[slug].column("entity_id"), pa.array(sample))
        )
        oracle_sample = run_engine("oracle", events_sample, spine_sample, feature_config, policy)
        parity["oracle_vs_batch_on_a_patient_sample"][slug] = {
            "patients": len(sample),
            **compare_feature_tables(
                oracle_sample, batch_sample, feature_config, left_name="oracle", right_name="batch"
            ).to_dict(),
        }

    def changed(slug: str) -> dict[str, Any]:
        mine, reference = frame.features[slug], frame.features[REFERENCE]
        differs = ~((mine == reference) | (np.isnan(mine) & np.isnan(reference)))
        return {
            "predictions_with_any_feature_changed": int(differs.any(axis=1).sum()),
            "share_of_predictions": float(differs.any(axis=1).mean()),
            "by_feature": {
                name: int(differs[:, index].sum()) for index, name in enumerate(frame.names)
            },
        }

    return {
        "synthetic_data": True,
        "validation": validation.to_dict(),
        "policies": {
            slug: {"strictness": policy.strictness.value, "faithful_replay": policy.faithful_replay}
            for slug, policy in policies.items()
        },
        "feature_differences_from_reference": {
            slug: changed(slug) for slug in POLICY_SLUGS if slug != REFERENCE
        },
        "parity": parity,
        "differential": naive_audit.to_dict(),
        "differential_latest_revised": {
            key: value for key, value in revised_audit.to_dict().items() if key != "examples"
        },
    }
