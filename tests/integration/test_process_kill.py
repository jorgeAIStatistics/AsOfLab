"""Kill the replay process for real, then resume it.

The in-process crash tests stop the engine with an exception. Here the process
is ended with os._exit at the chosen point, with no cleanup of any kind, which
leaves SQLite to recover on the next open exactly as it would after a crash.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from asoflab.audit.parity import compare_feature_tables
from asoflab.batch import compute_features
from asoflab.schema.models import TimePolicy
from asoflab.schema.tables import SPINE_SCHEMA, feature_schema, read_parquet, write_parquet
from asoflab.simulation.workload import WorkloadSpec, generate_workload, workload_features

pytestmark = pytest.mark.slow


def asoflab(*arguments: str, crash_at: str | None = None) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items() if key != "ASOFLAB_CRASH_AT"}
    if crash_at:
        environment["ASOFLAB_CRASH_AT"] = crash_at
    return subprocess.run(
        [sys.executable, "-m", "asoflab.cli", *arguments],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("crash_at", ["mid_chunk:1", "before_commit:2", "after_commit:0"])
def test_killed_replay_resumes_to_the_same_outputs(tmp_path: Path, crash_at: str) -> None:
    events, spine = generate_workload(
        WorkloadSpec(n_events=4000, seed=11, skew="zipf", events_per_entity=50)
    )
    config = workload_features()
    events_path = write_parquet(events, tmp_path / "events.parquet")
    spine_path = write_parquet(spine, tmp_path / "spine.parquet")
    config_path = tmp_path / "features.json"
    config_path.write_text(json.dumps(config.canonical()))
    prepared, stores, out = (
        tmp_path / "prepared",
        tmp_path / "stores",
        tmp_path / "features.parquet",
    )

    done = asoflab(
        "replay",
        "prepare",
        "--events",
        str(events_path),
        "--spine",
        str(spine_path),
        "--config",
        str(config_path),
        "--out",
        str(prepared),
        "--partitions",
        "2",
    )
    assert done.returncode == 0, done.stderr
    run = [
        "replay",
        "run",
        "--prepared",
        str(prepared),
        "--config",
        str(config_path),
        "--stores",
        str(stores),
        "--chunk-size",
        "400",
    ]

    killed = asoflab(*run, crash_at=crash_at)
    assert killed.returncode == 86, (
        f"expected the process to be killed, got {killed.returncode}: {killed.stderr}"
    )

    resumed = asoflab(*run)
    assert resumed.returncode == 0, resumed.stderr
    stats = json.loads(resumed.stdout)["partitions"]
    # Each of the three cases stops after at least one chunk has been committed.
    assert any(part["resumed_from_offset"] > 0 for part in stats), (
        "the resume must use a checkpoint"
    )

    exported = asoflab(
        "replay",
        "export",
        "--prepared",
        str(prepared),
        "--config",
        str(config_path),
        "--stores",
        str(stores),
        "--out",
        str(out),
    )
    assert exported.returncode == 0, exported.stderr

    replayed = read_parquet(out, feature_schema(config), "features")
    assert replayed.num_rows == read_parquet(spine_path, SPINE_SCHEMA, "spine").num_rows
    reference = compute_features(events, spine, config, TimePolicy.parse("pipeline_as_known"))
    report = compare_feature_tables(
        reference, replayed, config, left_name="batch", right_name="replay"
    )
    assert report.equivalent, report.describe()
