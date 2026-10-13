"""Minimal reproduction: which row does Feast return when two rows share an event timestamp?

    integrations/feast/.venv/bin/python integrations/feast/repro_created_tiebreak.py

Feast's documentation says the created-timestamp column deduplicates rows that
share an event timestamp, the highest created timestamp winning. This script
builds N entities, each with exactly two feature rows at the SAME event
timestamp: value 1.0 created first, value 2.0 created an hour later. Every
entity row is at a time after both, so the documented answer is 2.0 for every
entity, with or without `filter_by_created_timestamp`.

Two layouts are tried: every entity measured at the same instant, and each
entity measured one second after the previous one (so that only the two rows
of one entity tie).

It uses only Feast and pandas. Nothing from AsOfLab is involved.
"""

import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import feast
import pandas as pd
from feast import Entity, FeatureStore, FeatureView, Field, FileSource, RepoConfig, ValueType
from feast.types import Float64

EVENT = datetime(2025, 3, 1, 8, 0, tzinfo=UTC)


def later_created_wins(n_entities: int, store_type: str, flag: bool, distinct_event_times: bool) -> dict[str, int]:
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        ids = [f"e{index:05d}" for index in range(n_entities)]
        events = [EVENT + timedelta(seconds=index if distinct_event_times else 0) for index in range(n_entities)]
        rows = pd.DataFrame(
            {
                "patient_id": ids + ids,
                "event_timestamp": events + events,
                "created": [EVENT + timedelta(minutes=120)] * n_entities + [EVENT + timedelta(minutes=180)] * n_entities,
                "value": [1.0] * n_entities + [2.0] * n_entities,
            }
        ).sample(frac=1.0, random_state=0)  # row order in the file should not matter
        rows.to_parquet(work / "rows.parquet")
        store = FeatureStore(
            config=RepoConfig(
                project="tiebreak",
                provider="local",
                registry=str(work / "registry.db"),
                offline_store={"type": store_type},
                online_store={"type": "sqlite", "path": str(work / "online.db")},
                entity_key_serialization_version=3,
            )
        )
        patient = Entity(name="patient", join_keys=["patient_id"], value_type=ValueType.STRING)
        view = FeatureView(
            name="observation",
            entities=[patient],
            ttl=timedelta(days=1),
            schema=[Field(name="value", dtype=Float64)],
            source=FileSource(
                name="rows", path=str(work / "rows.parquet"), timestamp_field="event_timestamp",
                created_timestamp_column="created",
            ),
            online=False,
        )
        store.apply([patient, view])
        entity_rows = pd.DataFrame({"patient_id": ids, "event_timestamp": [EVENT + timedelta(hours=6)] * n_entities})
        frame = store.get_historical_features(
            entity_df=entity_rows, features=["observation:value"], filter_by_created_timestamp=flag
        ).to_df()
        return {
            "entities": n_entities,
            "rows_returned": len(frame),
            "later_created_value_returned": int((frame["value"] == 2.0).sum()),
            "earlier_created_value_returned": int((frame["value"] == 1.0).sum()),
        }


def main() -> None:
    import dask

    results = {
        "what": "Each entity has two feature rows with the same event timestamp. The documentation says the "
        "row with the highest created timestamp wins. Counts of which row Feast returned.",
        "feast_version": feast.__version__,
        "pandas_version": pd.__version__,
        "dask_version": dask.__version__,
        "command": "integrations/feast/.venv/bin/python integrations/feast/repro_created_tiebreak.py",
        "reported_upstream": False,
        "cases": [],
    }
    for store_type in ("dask", "duckdb"):
        for distinct in (False, True):
            for n_entities in (5, 50, 500, 5000):
                for flag in (False, True):
                    try:
                        outcome = later_created_wins(n_entities, store_type, flag, distinct)
                    except Exception as error:  # report, do not hide
                        outcome = {"entities": n_entities, "error": f"{type(error).__name__}: {str(error)[:200]}"}
                    case = {
                        "offline_store": store_type,
                        "event_times": "one per entity" if distinct else "identical for all entities",
                        "filter_by_created_timestamp": flag,
                        **outcome,
                    }
                    results["cases"].append(case)
                    print(case)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
