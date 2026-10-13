"""Benchmark harness: time the three engines on synthetic workloads and require equal answers.

    uv run python benchmarks/run_benchmarks.py --profile smoke
    uv run python benchmarks/run_benchmarks.py --profile standard

What is measured, per engine and workload, in a fresh process each time:

- prepare:  work an engine must do before it can compute (replay: sort and
            compact the archive into partition logs; oracle: load rows into
            record objects; batch: nothing).
- compute:  the feature computation itself.
- export:   collecting outputs and writing the feature Parquet file.
- end to end: all of the above inside the worker process, excluding
            interpreter start-up and imports.

Workload generation and writing the input Parquet files are timed once per
workload and reported separately.

A speed comparison is only meaningful between runs that give the same answer.
Every run's output is compared with the batch backend's, and `speedup` fields
are filled only when the outputs are equivalent. Runs that fail, time out, or
are skipped stay in the results with a reason.

These are measurements of a single-machine simulator on synthetic data. They
say nothing about any distributed system.
"""

import argparse
import contextlib
import json
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from asoflab.audit.environment import describe_environment, utc_now  # noqa: E402
from asoflab.audit.parity import compare_feature_tables  # noqa: E402
from asoflab.schema.tables import feature_schema, read_parquet, write_parquet  # noqa: E402
from asoflab.simulation.workload import WorkloadSpec, generate_workload, workload_features  # noqa: E402

POLICY = "pipeline_as_known"
SAMPLE_SECONDS = 0.02

PROFILES: dict[str, dict[str, Any]] = {
    "smoke": {
        "sizes": [10_000],
        "skews": ["uniform"],
        "repetitions": 2,
        "replay_layouts": [(1, 1), (4, 1), (4, 2)],
        "oracle_max_events": {"uniform": 10_000, "zipf": 10_000},
        "unbucketed_max_events": 10_000,
        "timeout_seconds": 300,
    },
    "standard": {
        "sizes": [10_000, 100_000, 1_000_000],
        "skews": ["uniform", "zipf"],
        "repetitions": 3,
        # (partitions, workers)
        "replay_layouts": [(1, 1), (8, 1), (8, 2), (8, 4), (8, 8)],
        # Under skew the oracle's cost grows with the square of the largest entity.
        "oracle_max_events": {"uniform": 100_000, "zipf": 10_000},
        "unbucketed_max_events": 1_000_000,
        "timeout_seconds": 900,
    },
}


def measure(job: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Run one worker process, sampling the memory of its whole process tree."""
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "benchmarks" / "worker.py"), json.dumps(job)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    peak = {"bytes": 0}
    done = threading.Event()

    def sample() -> None:
        try:
            parent = psutil.Process(process.pid)
        except psutil.NoSuchProcess:
            return
        while not done.is_set():
            total = 0
            try:
                for member in [parent, *parent.children(recursive=True)]:
                    with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                        total += member.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break
            peak["bytes"] = max(peak["bytes"], total)
            time.sleep(SAMPLE_SECONDS)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    started = time.perf_counter()
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        for member in psutil.Process(process.pid).children(recursive=True):
            member.kill()
        process.kill()
        process.communicate()
        done.set()
        return {
            "status": "timeout",
            "reason": f"exceeded {timeout:.0f} s",
            "peak_rss_bytes": peak["bytes"],
        }
    finally:
        done.set()
        sampler.join(timeout=1)
    wall = time.perf_counter() - started
    if process.returncode != 0:
        return {
            "status": "failed",
            "reason": stderr.strip().splitlines()[-1][:300] if stderr.strip() else "no output",
        }
    result: dict[str, Any] = json.loads(stdout.strip().splitlines()[-1])
    return {
        "status": "ok",
        "wall_seconds_with_startup": wall,
        "peak_rss_bytes": peak["bytes"],
        **result,
    }


def run_configuration(
    base: dict[str, Any], repetitions: int, timeout: float, scratch: Path, label: str
) -> tuple[dict[str, Any], Path | None]:
    """One warm-up run, then `repetitions` measured runs. Returns the summary and the last output file."""
    record: dict[str, Any] = {
        "phases_seconds": {"prepare": [], "compute": [], "export": []},
        "end_to_end_seconds": [],
        "wall_seconds_with_startup": [],
        "peak_rss_bytes": [],
        "status": "ok",
        "reason": None,
    }
    last_output = None
    for repetition in range(-1, repetitions):  # -1 is the unmeasured warm-up
        out = scratch / f"{label}-{repetition}.parquet"
        work = scratch / f"{label}-{repetition}-work"
        outcome = measure({**base, "out": str(out), "work": str(work)}, timeout)
        if outcome["status"] != "ok":
            record["status"], record["reason"] = outcome["status"], outcome["reason"]
            break
        if repetition < 0:
            continue
        for phase, seconds in outcome["phases_seconds"].items():
            record["phases_seconds"][phase].append(seconds)
        record["end_to_end_seconds"].append(outcome["end_to_end_seconds"])
        record["wall_seconds_with_startup"].append(outcome["wall_seconds_with_startup"])
        record["peak_rss_bytes"].append(outcome["peak_rss_bytes"])
        record["rows_out"] = outcome["rows_out"]
        last_output = out
    if record["end_to_end_seconds"]:
        record["median_end_to_end_seconds"] = statistics.median(record["end_to_end_seconds"])
        record["min_end_to_end_seconds"] = min(record["end_to_end_seconds"])
        record["max_end_to_end_seconds"] = max(record["end_to_end_seconds"])
        record["median_peak_rss_bytes"] = statistics.median(record["peak_rss_bytes"])
    return record, last_output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--profile", choices=sorted(PROFILES), default="smoke")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=20261)
    arguments = parser.parse_args()
    profile = PROFILES[arguments.profile]
    out_path = arguments.out or ROOT / "results" / "benchmarks" / f"{arguments.profile}.json"
    config = workload_features()
    features = config.canonical()
    workloads, runs = [], []

    with tempfile.TemporaryDirectory(prefix="asoflab-bench-") as temporary:
        scratch = Path(temporary)
        for skew in profile["skews"]:
            for size in profile["sizes"]:
                workload_id = f"{skew}-{size}"
                print(f"== workload {workload_id}", flush=True)
                spec = WorkloadSpec(n_events=size, seed=arguments.seed, skew=skew)
                mark = time.perf_counter()
                events, spine = generate_workload(spec)
                generation = time.perf_counter() - mark
                mark = time.perf_counter()
                events_path = write_parquet(events, scratch / workload_id / "events.parquet")
                spine_path = write_parquet(spine, scratch / workload_id / "spine.parquet")
                writing = time.perf_counter() - mark
                entity_counts = events.column("entity_id").value_counts()
                largest = max(entity_counts.field("counts").to_pylist())
                workloads.append(
                    {
                        "workload_id": workload_id,
                        "skew": skew,
                        "target_events": size,
                        "spec": spec.to_dict(),
                        "event_versions": events.num_rows,
                        "logical_events": len(set(events.column("logical_event_id").to_pylist())),
                        "entities": len(entity_counts),
                        "largest_entity_share_of_versions": largest / events.num_rows,
                        "predictions": spine.num_rows,
                        "features": len(config.features),
                        "input_bytes": {
                            "events_parquet": events_path.stat().st_size,
                            "spine_parquet": spine_path.stat().st_size,
                        },
                        "generation_seconds": generation,
                        "write_seconds": writing,
                    }
                )
                del events, spine
                base = {
                    "events": str(events_path),
                    "spine": str(spine_path),
                    "features": features,
                    "policy": POLICY,
                }

                configurations: list[dict[str, Any]] = [
                    {"engine": "batch", "partitions": None, "workers": 1}
                ]
                if size <= profile.get("unbucketed_max_events", 0):
                    # The same query without the time-bucket join key, to measure what the key buys.
                    configurations.append(
                        {"engine": "batch", "variant": "no_time_buckets", "time_buckets": False,
                         "partitions": None, "workers": 1}
                    )  # fmt: skip
                configurations += [
                    {
                        "engine": "replay",
                        "partitions": partitions,
                        "workers": workers,
                        "chunk_size": 50_000,
                    }
                    for partitions, workers in profile["replay_layouts"]
                ]
                configurations.append({"engine": "oracle", "partitions": None, "workers": 1})

                reference_table = None
                for configuration in configurations:
                    variant = configuration.get("variant", "default")
                    label = f"{workload_id}-{configuration['engine']}-{variant}-p{configuration['partitions']}-w{configuration['workers']}"
                    entry: dict[str, Any] = {
                        "workload_id": workload_id,
                        "engine": configuration["engine"],
                        "variant": variant,
                        "workers": configuration["workers"],
                        "partitions": configuration["partitions"],
                        "chunk_size": configuration.get("chunk_size"),
                        "reference_engine": "batch",
                        "equivalent_to_reference": None,
                        "throughput_events_per_second": None,
                        "speedup_vs_single_worker": None,
                    }
                    if (
                        configuration["engine"] == "oracle"
                        and size > profile["oracle_max_events"][skew]
                    ):
                        entry.update(
                            status="skipped",
                            reason="The oracle compares every prediction with every version of its entity. "
                            "It is a correctness reference and was not attempted at this size.",
                        )
                        runs.append(entry)
                        print(f"   {label}: skipped", flush=True)
                        continue
                    record, output = run_configuration(
                        {**base, **configuration},
                        profile["repetitions"],
                        profile["timeout_seconds"],
                        scratch,
                        label,
                    )
                    entry.update(record)
                    if output is not None:
                        table = read_parquet(output, feature_schema(config), "features")
                        if configuration["engine"] == "batch" and variant == "default":
                            reference_table = table
                            entry["equivalent_to_reference"] = True
                        elif reference_table is not None:
                            report = compare_feature_tables(reference_table, table, config)
                            entry["equivalent_to_reference"] = report.equivalent
                            if not report.equivalent:
                                entry["reason"] = report.describe()
                        entry["throughput_events_per_second"] = (
                            workloads[-1]["event_versions"] / entry["median_end_to_end_seconds"]
                        )
                    runs.append(entry)
                    shown = entry.get("median_end_to_end_seconds")
                    print(
                        f"   {label}: {entry['status']}"
                        + (
                            f" median {shown:.2f}s equivalent={entry['equivalent_to_reference']}"
                            if shown
                            else f" ({entry['reason']})"
                        ),
                        flush=True,
                    )

    # Speedups only between equivalent outputs, relative to one worker on the same partition layout.
    for entry in runs:
        if (
            entry["engine"] != "replay"
            or entry.get("status") != "ok"
            or not entry["equivalent_to_reference"]
        ):
            continue
        baseline = next(
            (
                other
                for other in runs
                if other["workload_id"] == entry["workload_id"]
                and other["engine"] == "replay"
                and other["partitions"] == entry["partitions"]
                and other["workers"] == 1
                and other.get("status") == "ok"
                and other["equivalent_to_reference"]
            ),
            None,
        )
        if baseline is not None:
            entry["speedup_vs_single_worker"] = (
                baseline["median_end_to_end_seconds"] / entry["median_end_to_end_seconds"]
            )
        prepare = statistics.median(entry["phases_seconds"]["prepare"])
        rest = entry["median_end_to_end_seconds"] - prepare
        entry["amortised_seconds_per_replay"] = {
            f"{n}_replays_of_one_prepared_log": prepare / n + rest for n in (1, 5, 20)
        }

    document = {
        "schema_version": 1,
        "kind": "benchmark",
        "generated_at": utc_now(),
        "command": f"uv run python benchmarks/run_benchmarks.py --profile {arguments.profile}",
        "profile": arguments.profile,
        "synthetic_data": True,
        "environment": describe_environment(),
        "settings": {
            "policy": POLICY,
            "seed": arguments.seed,
            "repetitions": profile["repetitions"],
            "warmup": "one unmeasured run of each configuration before the measured repetitions",
            "cache": "operating-system file cache warm: inputs are written and then read by the warm-up run",
            "isolation": "each run in a fresh process",
            "timeout_seconds": profile["timeout_seconds"],
            "duckdb_memory_limit": "DuckDB default (not set)",
            "duckdb_threads": "DuckDB default (all logical CPUs)",
            "replay_chunk_size": 50_000,
            "peak_memory_method": f"resident set size summed over the worker's process tree, sampled every {int(SAMPLE_SECONDS * 1000)} ms; "
            "short peaks between samples can be missed",
        },
        "workloads": workloads,
        "runs": runs,
        "notes": [
            "Single machine, synthetic data, local files. Not a measurement of any distributed system.",
            "The oracle is a correctness reference written for clarity, not speed. Its times are shown for scale only.",
            "end_to_end excludes interpreter start-up; wall_seconds_with_startup includes it.",
            "Speedups are reported only where the outputs were equivalent to the batch backend's.",
            "Replay prepare (sorting and compaction) is paid once per prepared log. "
            "amortised_seconds_per_replay states the number of replays it is spread over.",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(document, indent=2) + "\n")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
