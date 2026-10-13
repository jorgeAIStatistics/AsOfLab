"""One measured engine run, in its own process. Prints a JSON result on the last line of stdout.

Called by run_benchmarks.py. Running each measurement in a fresh process keeps
memory figures attributable to that run and stops one run's caches helping the next.
"""

import json
import sys
import time
from pathlib import Path

from asoflab.schema.models import FeatureConfig, TimePolicy
from asoflab.schema.tables import (
    EVENT_SCHEMA,
    SPINE_SCHEMA,
    feature_rows_to_table,
    read_parquet,
    write_parquet,
)


def main() -> None:
    job = json.loads(sys.argv[1])
    events, spine, out = Path(job["events"]), Path(job["spine"]), Path(job["out"])
    config = FeatureConfig.model_validate(job["features"])
    policy = TimePolicy.parse(job["policy"])
    phases = {"prepare": 0.0, "compute": 0.0, "export": 0.0}
    extra: dict[str, object] = {}

    started = time.perf_counter()
    if job["engine"] == "batch":
        from asoflab.batch import compute_features

        mark = time.perf_counter()
        table = compute_features(
            events, spine, config, policy, time_buckets=job.get("time_buckets", True)
        )
        phases["compute"] = time.perf_counter() - mark
    elif job["engine"] == "oracle":
        from asoflab.oracle import compute_features as oracle_features
        from asoflab.schema.tables import table_to_events, table_to_spine

        mark = time.perf_counter()
        event_records = table_to_events(read_parquet(events, EVENT_SCHEMA, "events"))
        spine_records = table_to_spine(read_parquet(spine, SPINE_SCHEMA, "spine"))
        phases["prepare"] = time.perf_counter() - mark  # loading rows into record objects
        mark = time.perf_counter()
        table = feature_rows_to_table(
            oracle_features(event_records, spine_records, config, policy), config
        )
        phases["compute"] = time.perf_counter() - mark
    elif job["engine"] == "replay":
        from asoflab.replay import collect_outputs, prepare_log, run_replay

        work = Path(job["work"])
        mark = time.perf_counter()
        prepared = prepare_log(
            events, spine, config, policy, work / "prepared", n_partitions=job["partitions"]
        )
        phases["prepare"] = time.perf_counter() - mark  # sort and compact into partition logs
        mark = time.perf_counter()
        result = run_replay(
            prepared, work / "stores", config, workers=job["workers"], chunk_size=job["chunk_size"]
        )
        phases["compute"] = time.perf_counter() - mark
        mark = time.perf_counter()
        table = collect_outputs(result, config)
        phases["export"] = time.perf_counter() - mark
        extra["replay_stats"] = result.to_dict()["partitions"]
    else:
        raise SystemExit(f"unknown engine {job['engine']!r}")

    mark = time.perf_counter()
    write_parquet(table, out)
    phases["export"] += time.perf_counter() - mark
    print(
        json.dumps(
            {
                "phases_seconds": phases,
                "end_to_end_seconds": time.perf_counter() - started,
                "rows_out": table.num_rows,
                **extra,
            }
        )
    )


if __name__ == "__main__":
    main()
