"""The read-only API serves real run outputs, and explains failures in plain language."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from asoflab.api.app import create_app
from asoflab.evaluation.bootstrap import BootstrapSettings
from asoflab.evaluation.experiment import AnalysisSettings, run_experiment
from asoflab.simulation.clinical import SCENARIOS, SimulationConfig

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> tuple[TestClient, str]:
    root = tmp_path_factory.mktemp("runs")
    settings = AnalysisSettings(bootstrap=BootstrapSettings(replicates=60), subgroup_replicates=30)
    directory = run_experiment(
        SimulationConfig(scenario=SCENARIOS["combined"], seed=20261, n_patients=200), settings, root
    )
    return TestClient(create_app(root, Path("results"), None)), directory.name


def test_runs_are_listed_with_their_provenance(client: tuple[TestClient, str]) -> None:
    api, run_id = client
    listing = api.get("/api/runs").json()
    assert [run["run_id"] for run in listing] == [run_id]
    assert listing[0]["synthetic_data"] is True
    assert listing[0]["has_patient_data"] is True
    assert api.get(f"/api/runs/{run_id}/manifest").json()["run_id"] == run_id
    assert "experiment_a" in api.get(f"/api/runs/{run_id}/statistics").json()
    assert "differential" in api.get(f"/api/runs/{run_id}/audit").json()
    assert api.get("/api/health").json()["status"] == "ok"


def test_timeline_reports_the_oracles_decision_at_any_cutoff(
    client: tuple[TestClient, str],
) -> None:
    api, run_id = client
    listing = api.get(
        f"/api/runs/{run_id}/patients", params={"only_with_findings": True, "limit": 5}
    ).json()
    assert listing["patients"]
    assert listing["patients"][0]["findings"] > 0
    entity_id = listing["patients"][0]["entity_id"]
    patient = api.get(f"/api/runs/{run_id}/patients/{entity_id}").json()
    assert patient["predictions"]
    assert patient["findings"]
    finding = patient["findings"][0]
    cutoff = next(
        p["prediction_time"]
        for p in patient["predictions"]
        if p["prediction_id"] == finding["prediction_id"]
    )

    as_known = api.get(
        f"/api/runs/{run_id}/patients/{entity_id}/timeline",
        params={"cutoff": cutoff, "policy": "pipeline_as_known"},
    ).json()
    naive = api.get(
        f"/api/runs/{run_id}/patients/{entity_id}/timeline",
        params={"cutoff": cutoff, "policy": "naive_event_time"},
    ).json()
    assert as_known["computed_by"] == "reference oracle"
    assert as_known["faithful_replay"] is True
    assert naive["faithful_replay"] is False
    assert len(as_known["versions"]) == patient["event_versions"]
    assert all(version["reason"] for version in as_known["versions"])
    # The oracle's answer at a real prediction time equals the stored batch features for that prediction.
    stored = next(
        row
        for row in patient["features"]["pipeline_as_known"]
        if row["prediction_id"] == finding["prediction_id"]
    )
    for name in as_known["feature_names"]:
        got, want = as_known["features"]["pipeline_as_known"][name], stored[name]
        assert (got is None and want is None) or got == pytest.approx(want, rel=1e-9)
    assert as_known["versions"] != naive["versions"], (
        "the two policies explain this cutoff differently"
    )


def test_comparison_lists_predictions_whose_features_differ(client: tuple[TestClient, str]) -> None:
    api, run_id = client
    page = api.get(f"/api/runs/{run_id}/comparison", params={"limit": 20}).json()
    assert page["synthetic_data"] is True
    assert 0 < page["predictions_matching"] <= page["predictions_total"]
    for row in page["rows"]:
        assert row["features_changed"] == len(row["changed"]) > 0
        assert all(row["as_known"][name] != row["naive"][name] for name in row["changed"])
    everything = api.get(
        f"/api/runs/{run_id}/comparison", params={"only_changed": False, "limit": 1}
    ).json()
    assert everything["predictions_matching"] == everything["predictions_total"]


def test_problems_are_explained_in_plain_language_with_technical_detail(
    client: tuple[TestClient, str],
) -> None:
    api, run_id = client
    missing = api.get("/api/runs/no-such-run/statistics")
    assert missing.status_code == 404
    assert set(missing.json()["detail"]) == {"message", "technical"}
    naive_time = api.get(
        f"/api/runs/{run_id}/patients/P000000/timeline", params={"cutoff": "2025-03-01T09:00:00"}
    )
    assert naive_time.status_code == 400
    assert "UTC offset" in naive_time.json()["detail"]["message"]
    assert "E_TS_NAIVE" in naive_time.json()["detail"]["technical"]
    assert api.get(f"/api/runs/{run_id}/patients/NOBODY").status_code == 404


def test_engineering_endpoint_returns_saved_results_or_explicit_absence(
    client: tuple[TestClient, str],
) -> None:
    api, _ = client
    body = api.get("/api/engineering").json()
    assert set(body) >= {"seeded_bugs", "recovery", "benchmarks", "experiment_summary", "commands"}
    assert isinstance(body["benchmarks"], list)
