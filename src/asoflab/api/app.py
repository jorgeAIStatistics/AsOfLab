"""Local read-only API for the inspection UI.

It serves what runs wrote to disk and computes eligibility on request with the
reference oracle. Nothing is mocked: if a file is missing the response says so.

Errors carry two fields: `message`, a plain-language account of the analytical
problem, and `technical`, the detail a developer needs.
"""

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from asoflab import CONTRACT_VERSION, __version__
from asoflab.errors import ContractViolation
from asoflab.oracle import compute_features as oracle_features
from asoflab.oracle.explain import explain_cutoff
from asoflab.schema.models import PredictionRow, TimePolicy
from asoflab.schema.tables import EVENT_SCHEMA, conform, table_to_events
from asoflab.schema.time import parse_utc
from asoflab.simulation.clinical import clinical_features

POLICIES = ("pipeline_as_known", "source_as_known", "latest_revised", "naive_event_time")
_NO_UI = """<!doctype html><meta charset="utf-8"><title>AsOfLab</title>
<body style="font: 16px/1.5 system-ui; max-width: 40rem; margin: 3rem auto; padding: 0 1rem">
<h1>AsOfLab API is running</h1>
<p>The inspection UI has not been built yet. From the repository root run:</p>
<pre>cd web &amp;&amp; npm ci &amp;&amp; npm run build</pre>
<p>Then restart <code>asoflab serve</code>. The API itself is available under <code>/api</code>.</p></body>"""


def problem(status: int, message: str, technical: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"message": message, "technical": technical})


def _json(path: Path) -> Any:
    return json.loads(path.read_text())


class RunStore:
    """Finds run directories under a root and loads their files, caching what it reads."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def directories(self) -> dict[str, Path]:
        return {path.parent.name: path.parent for path in sorted(self.root.glob("*/manifest.json"))}

    def directory(self, run_id: str) -> Path:
        directories = self.directories()
        if run_id not in directories:
            raise problem(
                404,
                "That run was not found. It may not have been generated on this machine yet.",
                f"No directory named {run_id!r} with a manifest.json under {self.root}.",
            )
        return directories[run_id]

    def document(self, run_id: str, name: str) -> Any:
        path = self.directory(run_id) / name
        if not path.exists():
            raise problem(
                404,
                "This run is incomplete: one of its result files is missing.",
                f"{path} does not exist.",
            )
        return _json(path)

    def table(self, run_id: str, relative: str) -> pa.Table:
        return _read_table(str(self.directory(run_id) / relative))


@lru_cache(maxsize=64)
def _read_table(path: str) -> pa.Table:
    if not Path(path).exists():
        raise problem(
            404,
            "The detailed data for this run are not on this machine. Summary results are still "
            "available; run the demo or experiment locally to inspect individual patients.",
            f"{path} does not exist. Bulk Parquet files are not committed to the repository.",
        )
    return pq.read_table(path)


def _rows(table: pa.Table) -> list[dict[str, Any]]:
    """Rows as JSON-ready dictionaries with ISO-8601 timestamps."""
    rows = table.to_pylist()
    for row in rows:
        for key, value in row.items():
            if isinstance(value, datetime):
                row[key] = value.isoformat()
    return rows


def _of_patient(table: pa.Table, entity_id: str) -> pa.Table:
    return table.filter(pc.equal(table.column("entity_id"), entity_id))


def create_app(
    runs_root: str | Path, results_root: str | Path, web_dist: str | Path | None = None
) -> FastAPI:
    runs = RunStore(Path(runs_root))
    results = Path(results_root)
    app = FastAPI(
        title="AsOfLab", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json"
    )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "asoflab_version": __version__,
            "contract_version": CONTRACT_VERSION,
        }

    @app.get("/api/runs")
    def list_runs() -> list[dict[str, Any]]:
        listing = []
        for run_id, directory in runs.directories().items():
            manifest = _json(directory / "manifest.json")
            cohort = (
                _json(directory / "statistics.json")["cohort"]
                if (directory / "statistics.json").exists()
                else {}
            )
            listing.append(
                {
                    "run_id": run_id,
                    "scenario": manifest.get("scenario"),
                    "seed": manifest.get("seed"),
                    "created_at": manifest.get("created_at"),
                    "synthetic_data": manifest.get("synthetic_data"),
                    "patients": cohort.get("patients"),
                    "predictions": cohort.get("predictions"),
                    "has_patient_data": (directory / "data" / "events.parquet").exists(),
                }
            )
        return listing

    @app.get("/api/runs/{run_id}/manifest")
    def manifest(run_id: str) -> Any:
        return runs.document(run_id, "manifest.json")

    @app.get("/api/runs/{run_id}/statistics")
    def statistics(run_id: str) -> Any:
        return runs.document(run_id, "statistics.json")

    @app.get("/api/runs/{run_id}/audit")
    def audit(run_id: str) -> Any:
        return runs.document(run_id, "audit.json")

    @app.get("/api/runs/{run_id}/patients")
    def patients(
        run_id: str,
        search: str = "",
        only_with_findings: bool = False,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        people = runs.table(run_id, "data/patients.parquet")
        predictions = runs.table(run_id, "predictions.parquet")
        findings = runs.table(run_id, "audit_findings.parquet")
        n_predictions = dict(zip(*_counts(predictions.column("entity_id")), strict=True))
        n_findings = dict(zip(*_counts(findings.column("entity_id")), strict=True))
        split = dict(
            zip(
                predictions.column("entity_id").to_pylist(),
                predictions.column("split").to_pylist(),
                strict=True,
            )
        )
        rows = []
        for row in people.to_pylist():
            entity_id = row["entity_id"]
            if search and search.lower() not in entity_id.lower():
                continue
            if only_with_findings and not n_findings.get(entity_id):
                continue
            rows.append(
                {
                    **row,
                    "predictions": n_predictions.get(entity_id, 0),
                    "findings": n_findings.get(entity_id, 0),
                    "split": split.get(entity_id),
                }
            )
        rows.sort(key=lambda row: (-row["findings"], row["entity_id"]))
        return {"total": len(rows), "shown": min(len(rows), limit), "patients": rows[:limit]}

    @app.get("/api/runs/{run_id}/patients/{entity_id}")
    def patient(run_id: str, entity_id: str) -> dict[str, Any]:
        people = _of_patient(runs.table(run_id, "data/patients.parquet"), entity_id)
        if people.num_rows == 0:
            raise problem(
                404, "That patient is not in this run.", f"entity_id {entity_id!r} not found."
            )
        predictions = _of_patient(runs.table(run_id, "predictions.parquet"), entity_id).sort_by(
            "prediction_time"
        )
        features = {
            slug: _rows(
                _of_patient(runs.table(run_id, f"features/{slug}.parquet"), entity_id).sort_by(
                    "prediction_time"
                )
            )
            for slug in POLICIES
        }
        events = _of_patient(runs.table(run_id, "data/events.parquet"), entity_id)
        outcomes = _of_patient(runs.table(run_id, "data/outcomes.parquet"), entity_id)
        return {
            "synthetic_data": True,
            "patient": people.to_pylist()[0],
            "encounters": _rows(
                _of_patient(runs.table(run_id, "data/encounters.parquet"), entity_id)
            ),
            "outcomes": _rows(outcomes),
            "event_versions": events.num_rows,
            "predictions": _rows(predictions),
            "features": features,
            "feature_names": clinical_features().names,
            "findings": _rows(_of_patient(runs.table(run_id, "audit_findings.parquet"), entity_id)),
        }

    @app.get("/api/runs/{run_id}/patients/{entity_id}/timeline")
    def timeline(
        run_id: str, entity_id: str, cutoff: str, policy: str = "pipeline_as_known"
    ) -> dict[str, Any]:
        """Eligibility of every version of this patient's events at an arbitrary cutoff, from the oracle."""
        try:
            moment = parse_utc(cutoff)
            chosen = TimePolicy.parse(policy)
        except ContractViolation as error:
            raise problem(
                400,
                "The requested time or policy could not be understood. Times need an explicit UTC offset.",
                str(error),
            ) from error
        events = _of_patient(runs.table(run_id, "data/events.parquet"), entity_id)
        if events.num_rows == 0:
            raise problem(
                404,
                "This patient has no recorded observations in this run.",
                f"{entity_id!r}: 0 events.",
            )
        versions = table_to_events(conform(events, EVENT_SCHEMA, "events"))
        config = clinical_features()
        statuses = explain_cutoff(versions, moment, config, chosen)
        probe = [PredictionRow(prediction_id="probe", entity_id=entity_id, prediction_time=moment)]
        values = {
            slug: oracle_features(versions, probe, config, TimePolicy.parse(slug))[0].values
            for slug in POLICIES
        }
        counts: dict[str, int] = {}
        for status in statuses:
            counts[status.status] = counts.get(status.status, 0) + 1
        return {
            "synthetic_data": True,
            "entity_id": entity_id,
            "cutoff": moment.isoformat(),
            "policy": chosen.slug(),
            "strictness": chosen.strictness.value,
            "faithful_replay": chosen.faithful_replay,
            "computed_by": "reference oracle",
            "status_counts": counts,
            "versions": [status.to_dict() for status in statuses],
            "features": values,
            "feature_names": config.names,
        }

    @app.get("/api/runs/{run_id}/comparison")
    def comparison(
        run_id: str,
        only_changed: bool = True,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        """Naive and as-known features and predicted risks side by side, one row per prediction."""
        names = clinical_features().names
        reference = runs.table(run_id, "features/pipeline_as_known.parquet")
        naive = runs.table(run_id, "features/naive_event_time.parquet")
        predictions = runs.table(run_id, "predictions.parquet").sort_by("prediction_id")
        changed_columns = []
        for name in names:
            a, b = reference.column(name), naive.column(name)
            same = pc.or_(
                pc.fill_null(pc.equal(a, b), False), pc.and_(pc.is_null(a), pc.is_null(b))
            )
            changed_columns.append(pc.invert(same))
        n_changed = pc.cast(changed_columns[0], pa.int32())
        for column in changed_columns[1:]:
            n_changed = pc.add(n_changed, pc.cast(column, pa.int32()))
        keep = pc.greater(n_changed, 0) if only_changed else pa.array([True] * reference.num_rows)
        indices = pc.indices_nonzero(keep)
        total = len(indices)
        page = indices.slice(offset, limit)
        reference_page, naive_page = reference.take(page), naive.take(page)
        prediction_page = predictions.take(page)
        changed_page = n_changed.take(page).to_pylist()
        rows = []
        for index, base in enumerate(_rows(prediction_page)):
            as_known = {name: reference_page.column(name)[index].as_py() for name in names}
            as_naive = {name: naive_page.column(name)[index].as_py() for name in names}
            rows.append(
                {
                    "prediction_id": base["prediction_id"],
                    "entity_id": base["entity_id"],
                    "prediction_time": base["prediction_time"],
                    "split": base["split"],
                    "label": base["label"],
                    "label_status": base["label_status"],
                    "risk_as_known": base["p_as_known_model__pipeline_as_known"],
                    "risk_naive": base["p_as_known_model__naive_event_time"],
                    "features_changed": changed_page[index],
                    "changed": [name for name in names if as_known[name] != as_naive[name]],
                    "as_known": as_known,
                    "naive": as_naive,
                }
            )
        return {
            "synthetic_data": True,
            "reference_policy": "pipeline_as_known",
            "candidate_policy": "naive_event_time",
            "risk_model": "frozen model trained on as-known features",
            "feature_names": names,
            "predictions_total": reference.num_rows,
            "predictions_matching": total,
            "offset": offset,
            "rows": rows,
        }

    @app.get("/api/engineering")
    def engineering() -> dict[str, Any]:
        """Saved validation and benchmark results from the repository's results directory."""

        def load(relative: str) -> Any:
            path = results / relative
            return _json(path) if path.exists() else None

        benchmarks = (
            sorted((results / "benchmarks").glob("*.json"))
            if (results / "benchmarks").exists()
            else []
        )
        return {
            "seeded_bugs": load("validation/seeded_bugs.json"),
            "recovery": load("validation/recovery.json"),
            "benchmarks": [{"file": path.name, **_json(path)} for path in benchmarks],
            "experiment_summary": load("experiments/summary.json"),
            "commands": {
                "tests": "uv run pytest -q",
                "seeded_bugs": "uv run python scripts/seeded_bug_report.py --trials 10",
                "recovery": "uv run python scripts/recovery_report.py",
                "benchmarks": "uv run python benchmarks/run_benchmarks.py --profile standard",
                "demo": "uv run asoflab demo",
            },
        }

    dist = Path(web_dist) if web_dist else None
    if dist and (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
    else:

        @app.get("/", response_class=HTMLResponse)
        def no_ui() -> str:
            return _NO_UI

    return app


def _counts(column: pa.ChunkedArray) -> tuple[list[str], list[int]]:
    counted = pc.value_counts(column)
    return counted.field("values").to_pylist(), counted.field("counts").to_pylist()
