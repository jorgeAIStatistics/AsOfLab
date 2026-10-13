"""Run a prepared replay across partitions, optionally in parallel, and collect the outputs.

Parallelism is across partitions only. Every event and every trigger of an
entity is in exactly one partition, so workers share nothing and need no
coordination. That is also the limit of this execution model: a feature that
reads across entities cannot be computed this way, and the feature schema
rejects such features.
"""

import hashlib
import json
import multiprocessing
import sqlite3
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa

from asoflab.contract import CONTRACT_VERSION
from asoflab.replay.engine import DEFAULT_CHUNK_SIZE, CrashPlan, ReplayStats, replay_partition
from asoflab.replay.log import PreparedLog, prepare_log
from asoflab.replay.store import ReplayStore, decode_payload
from asoflab.schema.models import FeatureConfig, TimePolicy
from asoflab.schema.tables import TableSource, feature_schema
from asoflab.schema.time import from_us


@dataclass
class ReplayResult:
    run_id: str
    store_paths: list[Path]
    stats: list[ReplayStats]

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "partitions": [stats.to_dict() for stats in self.stats]}


def run_id_for(prepared: PreparedLog, config: FeatureConfig) -> str:
    """A run is identified by what was computed: same log, features and contract, same id.

    Recomputing under a different policy or feature set is therefore a different
    run and can never overwrite this one.
    """
    identity = {
        "contract_version": CONTRACT_VERSION,
        "log": prepared.fingerprint,
        "feature_config_hash": config.config_hash(),
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return f"run-{digest[:16]}"


def _replay_one(arguments: dict[str, Any]) -> ReplayStats:
    crash = arguments.pop("crash")
    if crash is None:
        crash = CrashPlan.from_environment()
    return replay_partition(crash=crash, **arguments)


def run_replay(
    prepared: PreparedLog,
    store_directory: str | Path,
    config: FeatureConfig,
    *,
    workers: int = 1,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    crash: CrashPlan | None = None,
) -> ReplayResult:
    """Replay every partition. Safe to call again after an interruption: it resumes."""
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if prepared.feature_config_hash != config.config_hash():
        raise ValueError("This log was prepared for a different feature set.")
    store_directory = Path(store_directory)
    run_id = run_id_for(prepared, config)
    jobs = []
    store_paths = []
    for index, partition in enumerate(prepared.partitions):
        store_path = store_directory / f"part-{index:05d}.sqlite"
        store_paths.append(store_path)
        jobs.append(
            {
                "log_path": prepared.path(index),
                "store_path": store_path,
                "config": config,
                "run_id": run_id,
                "log_sha256": partition["sha256"],
                "chunk_size": chunk_size,
                "crash": crash,
            }
        )
    if workers == 1 or len(jobs) == 1:
        stats = [_replay_one(job) for job in jobs]
    else:
        # "spawn" starts each worker from a clean interpreter on every platform.
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=min(workers, len(jobs)), mp_context=context) as pool:
            stats = list(pool.map(_replay_one, jobs))
    return ReplayResult(run_id, store_paths, stats)


def collect_outputs(result: ReplayResult, config: FeatureConfig) -> pa.Table:
    """Read every partition's committed outputs into one wide table sorted by prediction_id."""
    rows: list[tuple[Any, ...]] = []
    for store_path in result.store_paths:
        store = ReplayStore(
            store_path,
            run_id=result.run_id,
            feature_config_hash=config.config_hash(),
            log_sha256=_stored_log_hash(store_path),
        )
        try:
            rows.extend(store.outputs())
        finally:
            store.close()
    rows.sort(key=lambda row: row[0])
    payloads = [decode_payload(row[4]) for row in rows]
    columns: dict[str, list[Any]] = {
        "prediction_id": [row[0] for row in rows],
        "entity_id": [row[1] for row in rows],
        "encounter_id": [row[2] for row in rows],
        "prediction_time": [from_us(row[3]) for row in rows],
    }
    for position, name in enumerate(config.names):
        columns[name] = [payload[position] for payload in payloads]
    return pa.table(columns, schema=feature_schema(config))


def _stored_log_hash(store_path: Path) -> str:
    connection = sqlite3.connect(store_path)
    try:
        row = connection.execute("SELECT value FROM meta WHERE key = 'log_sha256'").fetchone()
    finally:
        connection.close()
    return str(row[0])


def replay_features(
    events: TableSource,
    spine: TableSource,
    config: FeatureConfig,
    policy: TimePolicy,
    *,
    n_partitions: int = 1,
    workers: int = 1,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    work_directory: str | Path | None = None,
) -> pa.Table:
    """Prepare, replay and collect in one call. Uses a temporary directory unless one is given."""
    if work_directory is not None:
        return _replay_in(
            Path(work_directory), events, spine, config, policy, n_partitions, workers, chunk_size
        )
    with tempfile.TemporaryDirectory(prefix="asoflab-replay-") as temporary:
        return _replay_in(
            Path(temporary), events, spine, config, policy, n_partitions, workers, chunk_size
        )


def _replay_in(
    directory: Path,
    events: TableSource,
    spine: TableSource,
    config: FeatureConfig,
    policy: TimePolicy,
    n_partitions: int,
    workers: int,
    chunk_size: int,
) -> pa.Table:
    prepared = prepare_log(
        events, spine, config, policy, directory / "prepared", n_partitions=n_partitions
    )
    result = run_replay(
        prepared, directory / "stores", config, workers=workers, chunk_size=chunk_size
    )
    return collect_outputs(result, config)
