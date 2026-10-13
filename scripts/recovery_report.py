"""Kill the replay process at chosen points, resume it, and record what happened.

Writes results/validation/recovery.json. For every case the replay runs in its
own process, is ended with os._exit at the named point (no cleanup of any kind),
is started again, and its final committed outputs are compared with those of
an uninterrupted run.

    uv run python scripts/recovery_report.py

Scope: one machine, one local SQLite file per partition. This demonstrates the
local recovery contract in src/asoflab/replay/store.py. It is not a claim of
end-to-end exactly-once delivery.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

from asoflab.audit.environment import describe_environment, utc_now  # noqa: E402
from asoflab.replay.engine import CRASH_ENV, CRASH_POINTS  # noqa: E402
from asoflab.schema.tables import feature_schema, read_parquet, write_parquet  # noqa: E402
from asoflab.simulation.workload import WorkloadSpec, generate_workload, workload_features  # noqa: E402

CHUNK_SIZE = 2000
PARTITIONS = 2


def asoflab(*arguments: str, crash_at: str | None = None) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items() if key != CRASH_ENV}
    if crash_at:
        environment[CRASH_ENV] = crash_at
    return subprocess.run(
        [sys.executable, "-m", "asoflab.cli", *arguments],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def replay(directory: Path, inputs: dict[str, str], crash_at: str | None) -> dict[str, Any]:
    prepared, stores, out = (
        directory / "prepared",
        directory / "stores",
        directory / "features.parquet",
    )
    done = asoflab("replay", "prepare", "--events", inputs["events"], "--spine", inputs["spine"],
                   "--config", inputs["config"], "--out", str(prepared), "--partitions", str(PARTITIONS))  # fmt: skip
    assert done.returncode == 0, done.stderr
    run = ["replay", "run", "--prepared", str(prepared), "--config", inputs["config"],
           "--stores", str(stores), "--chunk-size", str(CHUNK_SIZE)]  # fmt: skip
    record: dict[str, Any] = {}
    if crash_at:
        killed = asoflab(*run, crash_at=crash_at)
        record["exit_code"] = killed.returncode
    resumed = asoflab(*run)
    assert resumed.returncode == 0, resumed.stderr
    stats = json.loads(resumed.stdout)["partitions"]
    record["resumed_from_offset"] = max(part["resumed_from_offset"] for part in stats)
    exported = asoflab("replay", "export", "--prepared", str(prepared), "--config", inputs["config"],
                       "--stores", str(stores), "--out", str(out))  # fmt: skip
    assert exported.returncode == 0, exported.stderr
    record["output"] = out
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--events", type=int, default=20_000)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results" / "validation" / "recovery.json"
    )
    arguments = parser.parse_args()
    spec = WorkloadSpec(n_events=arguments.events, seed=20261, skew="zipf", events_per_entity=100)
    config = workload_features()

    with tempfile.TemporaryDirectory(prefix="asoflab-recovery-") as temporary:
        scratch = Path(temporary)
        events, spine = generate_workload(spec)
        config_path = scratch / "features.json"
        config_path.write_text(json.dumps(config.canonical()))
        inputs = {
            "events": str(write_parquet(events, scratch / "events.parquet")),
            "spine": str(write_parquet(spine, scratch / "spine.parquet")),
            "config": str(config_path),
        }
        clean = replay(scratch / "uninterrupted", inputs, None)
        reference = read_parquet(clean["output"], feature_schema(config), "features")

        cases = []
        for point in CRASH_POINTS:
            for chunk in (0, 2, 5):
                record = replay(scratch / f"{point}-{chunk}", inputs, f"{point}:{chunk}")
                table = read_parquet(record["output"], feature_schema(config), "features")
                cases.append(
                    {
                        "crash_point": point,
                        "chunk": chunk,
                        "how": "process ended with os._exit(86): no cleanup, no rollback call",
                        "exit_code": record["exit_code"],
                        "resumed_from_offset": record["resumed_from_offset"],
                        "predictions": table.num_rows,
                        "outputs_equal_uninterrupted": bool(table.equals(reference)),
                    }
                )
                print(f"{point}:{chunk} exit {record['exit_code']} resumed at {record['resumed_from_offset']} "
                      f"equal {cases[-1]['outputs_equal_uninterrupted']}")  # fmt: skip

    report = {
        "generated_at": utc_now(),
        "command": "uv run python scripts/recovery_report.py",
        "synthetic_data": True,
        "environment": describe_environment(),
        "scope": "One machine, one local SQLite file per partition. Each case kills the replay process "
        "outright and restarts it. This demonstrates the local recovery contract only; it is not "
        "end-to-end exactly-once delivery and says nothing about external sinks or other machines.",
        "workload": {
            **spec.to_dict(),
            "event_versions": events.num_rows,
            "predictions": spine.num_rows,
            "partitions": PARTITIONS,
            "chunk_size": CHUNK_SIZE,
        },
        "crash_points": {
            "mid_chunk": "half-way through processing a chunk, before anything is written",
            "before_commit": "after the chunk's outputs, state changes and checkpoint are written, before COMMIT",
            "after_commit": "immediately after COMMIT returns",
        },
        "cases": cases,
        "all_recovered": all(
            case["outputs_equal_uninterrupted"] and case["exit_code"] == 86 for case in cases
        ),
    }
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {arguments.out}; all recovered: {report['all_recovered']}")


if __name__ == "__main__":
    main()
