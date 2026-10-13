"""The Feast half of the comparison. Runs inside the isolated Feast environment.

    python feast_side.py <work directory> <ttl seconds> <offline store type>

Reads `feature_rows.parquet` (patient_id, event_timestamp, created, value) and
`entity_rows.parquet` (prediction_id, patient_id, event_timestamp) from the work
directory, retrieves the feature with Feast twice (default behaviour, and with
`filter_by_created_timestamp=True`), and writes `feast_result.json`.

Nothing from AsOfLab is imported here: the two sides only exchange files.
"""

import json
import sys
from datetime import timedelta
from pathlib import Path

import feast
import pandas as pd
from feast import Entity, FeatureStore, FeatureView, Field, FileSource, RepoConfig, ValueType
from feast.types import Float64


def main() -> None:
    work, ttl_seconds, store_type = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    repo = work / f"feast_repo_{store_type}"
    repo.mkdir(exist_ok=True)
    store = FeatureStore(
        config=RepoConfig(
            project="asoflab_comparison",
            provider="local",
            registry=str(repo / "registry.db"),
            offline_store={"type": store_type},
            online_store={"type": "sqlite", "path": str(repo / "online.db")},
            entity_key_serialization_version=3,
        )
    )
    patient = Entity(name="patient", join_keys=["patient_id"], value_type=ValueType.STRING)
    source = FileSource(
        name="observations",
        path=str(work / "feature_rows.parquet"),
        timestamp_field="event_timestamp",
        created_timestamp_column="created",
    )
    view = FeatureView(
        name="observation",
        entities=[patient],
        ttl=timedelta(seconds=ttl_seconds),
        schema=[Field(name="value", dtype=Float64)],
        source=source,
        online=False,
    )
    store.apply([patient, view])

    entity_rows = pd.read_parquet(work / "entity_rows.parquet")
    result: dict[str, object] = {"feast_version": feast.__version__, "offline_store": store_type, "modes": {}}
    for mode, flag in (("default", False), ("filter_by_created_timestamp", True)):
        frame = store.get_historical_features(
            entity_df=entity_rows, features=["observation:value"], filter_by_created_timestamp=flag
        ).to_df()
        values = {
            row.prediction_id: (None if pd.isna(row.value) else float(row.value))
            for row in frame.itertuples(index=False)
        }
        result["modes"][mode] = {"rows_returned": len(frame), "values": values}  # type: ignore[index]
    (work / f"feast_result_{store_type}.json").write_text(json.dumps(result))


if __name__ == "__main__":
    main()
