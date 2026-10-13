"""The statistical demonstration end to end, on small cohorts."""

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from asoflab.evaluation.bootstrap import BootstrapSettings
from asoflab.evaluation.experiment import POLICY_SLUGS, AnalysisSettings, run_experiment
from asoflab.simulation.clinical import SCENARIOS, SimulationConfig

pytestmark = pytest.mark.slow

SETTINGS = AnalysisSettings(
    bootstrap=BootstrapSettings(replicates=120, seed=9), subgroup_replicates=40
)


def run(
    tmp_path: Path, scenario: str, n_patients: int = 400, seed: int = 20261
) -> tuple[Path, dict, dict, dict]:
    config = SimulationConfig(scenario=SCENARIOS[scenario], seed=seed, n_patients=n_patients)
    directory = run_experiment(config, SETTINGS, tmp_path)
    load = lambda name: json.loads((directory / name).read_text())  # noqa: E731
    return directory, load("statistics.json"), load("audit.json"), load("manifest.json")


def intervals(block: dict) -> list[dict]:
    found = []
    for group in ("conditions", "differences"):
        for entry in block[group].values():
            found.extend(value for value in entry.values() if isinstance(value, dict))
    return found


def test_negative_control_is_clean(tmp_path: Path) -> None:
    """With no delays and no revisions, every policy must give the same features and the same numbers."""
    _, statistics, audit, _ = run(tmp_path, "negative_control")
    a = statistics["experiment_a"]
    assert all(a["features_identical_to_reference"].values())
    for name, difference in a["prediction_weighted"]["differences"].items():
        for metric in ("auroc", "average_precision", "brier"):
            assert (
                difference[metric]["point"],
                difference[metric]["lower"],
                difference[metric]["upper"],
            ) == (
                0.0,
                0.0,
                0.0,
            ), f"{name} {metric}"
    for difference in statistics["experiment_b"]["prediction_weighted"]["differences"].values():
        assert difference["auroc"]["point"] == 0.0
    assert audit["differential"]["findings_total"] == 0
    assert all(
        entry["predictions_with_any_feature_changed"] == 0
        for entry in audit["feature_differences_from_reference"].values()
    )
    assert statistics["label_maturity"]["premature_negatives_if_labelled_at_the_horizon"] == 0


def test_combined_scenario_reports_everything_with_uncertainty(tmp_path: Path) -> None:
    directory, statistics, audit, manifest = run(tmp_path, "combined")
    assert statistics["synthetic_data"] is True
    assert manifest["synthetic_data"] is True

    # Counts that every table of results must carry.
    sample = statistics["experiment_a"]["sample"]
    assert sample["patients"] > 0
    assert sample["predictions"] > sample["patients"]
    assert 0 < sample["events"] < sample["predictions"]
    coverage = statistics["label_coverage"]
    assert coverage["unknown"] == 0
    assert coverage["coverage"] == 1.0

    # Every reported estimate says how many resamples were requested and completed.
    for block in (
        statistics["experiment_a"]["prediction_weighted"],
        statistics["experiment_a"]["patient_weighted"],
        statistics["experiment_b"]["prediction_weighted"],
    ):
        for interval in intervals(block):
            assert interval["bootstrap_requested"] == 120
            assert 0 <= interval["bootstrap_completed"] <= 120
            if interval["estimable"]:
                assert (
                    interval["lower"] <= interval["point"] <= interval["upper"]
                    or interval["lower"] <= interval["upper"]
                )
            else:
                assert interval["reason"]
    assert statistics["bootstrap"]["unit_resampled"] == "patient"
    assert statistics["bootstrap"]["conditional_on_fitted_models"] is True

    # The four policies, labelled for what they are.
    assert set(statistics["experiment_a"]["prediction_weighted"]["conditions"]) == set(POLICY_SLUGS)
    faithful = {entry["policy"]: entry["faithful_replay"] for entry in manifest["time_policies"]}
    assert faithful == {
        "pipeline_as_known": True,
        "source_as_known": True,
        "latest_revised": False,
        "naive_event_time": False,
    }

    # The decomposition adds up: naive - as-known = revision effect + availability effect.
    differences = statistics["experiment_a"]["prediction_weighted"]["differences"]
    total = differences["naive_event_time_minus_pipeline_as_known"]["auroc"]["point"]
    parts = (
        differences["revision_effect"]["auroc"]["point"]
        + differences["availability_effect"]["auroc"]["point"]
    )
    assert total == pytest.approx(parts, abs=1e-12)

    # Engines agree on this run's data, and the audit found timing differences to explain.
    assert all(entry["equivalent"] for entry in audit["parity"]["replay_vs_batch"].values())
    assert all(
        entry["equivalent"]
        for entry in audit["parity"]["oracle_vs_batch_on_a_patient_sample"].values()
    )
    assert audit["differential"]["findings_total"] > 0
    example = audit["differential"]["examples"][0]
    assert {
        "prediction_id",
        "logical_event_id",
        "expected",
        "actual",
        "severity",
        "remediation",
    } <= set(example)

    # Subgroups carry patient and event counts and an instability flag.
    for groups in statistics["subgroups"]["groups"].values():
        for entry in groups.values():
            assert {"patients", "events"} <= set(entry["sample"])
            assert isinstance(entry["unstable"], bool)

    # Stable identifiers on the saved predictions.
    predictions = pq.read_table(directory / "predictions.parquet")
    assert predictions.num_rows == statistics["cohort"]["predictions"]
    assert len(set(predictions.column("prediction_id").to_pylist())) == predictions.num_rows
    assert set(manifest["data_hashes"]) == {
        "patients",
        "encounters",
        "events",
        "spine",
        "outcomes",
        "follow_up",
    }
    assert manifest["environment"]["libraries"]["duckdb"]
    assert "not evidence" in manifest["note"]


def test_unknown_labels_are_reported_and_never_become_negatives(tmp_path: Path) -> None:
    _, statistics, _, _ = run(tmp_path, "delayed_outcomes")
    maturity = statistics["label_maturity"]
    by_status = maturity["coverage_at_data_cut"]["by_status"]
    assert by_status["pending_horizon"] > 0, "predictions near the data cut have no label yet"
    assert (
        maturity["coverage_at_data_cut"]["unknown"]
        == by_status["pending_horizon"]
        + by_status["pending_ascertainment"]
        + by_status["unknown_follow_up"]
    )
    assert maturity["unknown_labels_set_to_zero"] == 0
    hasty = maturity["evaluation_against_impatient_labels"]
    assert maturity["premature_negatives_if_labelled_at_the_horizon"] > 0
    assert (
        hasty["positives_with_labels_recorded_at_horizon"] < hasty["positives_with_mature_labels"]
    )
    chronological = statistics["chronological"]["split"]
    assert chronological["fitting_labels_that_differ_from_final_labels"] == 0, (
        "mature labels do not change later"
    )
    assert "excluded_label_not_mature_at_cutoff" in chronological["counts"]


def test_the_same_configuration_reproduces_the_same_statistics(tmp_path: Path) -> None:
    first_dir, _first, _, first_manifest = run(tmp_path / "one", "combined", n_patients=250)
    second_dir, _second, _, second_manifest = run(tmp_path / "two", "combined", n_patients=250)
    assert first_dir.name == second_dir.name, "the run id is a function of the inputs"
    assert (first_dir / "statistics.json").read_bytes() == (
        second_dir / "statistics.json"
    ).read_bytes()
    assert first_manifest["data_hashes"] == second_manifest["data_hashes"]
    assert first_manifest["time_policies"] == second_manifest["time_policies"]
    other_dir, _, _, _ = run(tmp_path / "three", "combined", n_patients=250, seed=20262)
    assert other_dir.name != first_dir.name


def test_a_completed_run_is_never_overwritten(tmp_path: Path) -> None:
    run(tmp_path, "negative_control", n_patients=200)
    with pytest.raises(FileExistsError, match="never overwritten"):
        run(tmp_path, "negative_control", n_patients=200)
