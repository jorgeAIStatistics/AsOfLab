"""Incremental replay of one partition: a local, single-process stream simulator.

This is a simulator, not a distributed stream processor. It reads a prepared
log in order, keeps version-aware state per entity, emits one feature row per
trigger, and commits in chunks so that it can be stopped and resumed.

Scheduling uses the time each item became known (the log order). Window
membership uses event time. Those two clocks are never mixed.
"""

import os
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from asoflab.replay.log import ARRIVAL, LOG_COLUMNS
from asoflab.replay.state import EntityState, FeaturePlan
from asoflab.replay.store import OutputRow, ReplayStore, StateRow, encode_payload
from asoflab.schema.models import FeatureConfig

DEFAULT_CHUNK_SIZE = 50_000
CRASH_POINTS = ("mid_chunk", "before_commit", "after_commit")
CRASH_ENV = "ASOFLAB_CRASH_AT"


class SimulatedCrash(Exception):
    """Raised by a CrashPlan to stop the engine at a chosen point, as a crash would."""


@dataclass(frozen=True)
class CrashPlan:
    """Stop at `point` while processing chunk number `chunk` (counted from 0 in this process).

    `hard=True` ends the process immediately with os._exit, skipping all cleanup,
    which is the closest a test can get to a real crash.
    """

    point: str
    chunk: int
    hard: bool = False

    def __post_init__(self) -> None:
        if self.point not in CRASH_POINTS:
            raise ValueError(f"Unknown crash point {self.point!r}. Choose from {CRASH_POINTS}.")

    def maybe(self, point: str, chunk: int) -> None:
        if point == self.point and chunk == self.chunk:
            if self.hard:
                os._exit(86)
            raise SimulatedCrash(f"simulated crash at {point} in chunk {chunk}")

    @classmethod
    def from_environment(cls) -> "CrashPlan | None":
        """Read 'point:chunk' from ASOFLAB_CRASH_AT. Used by subprocess crash tests."""
        text = os.environ.get(CRASH_ENV)
        if not text:
            return None
        point, _, chunk = text.partition(":")
        return cls(point, int(chunk), hard=True)


@dataclass
class ReplayStats:
    partition: str = ""
    resumed_from_offset: int = 0
    items: int = 0
    arrivals: int = 0
    triggers: int = 0
    stale_or_duplicate_arrivals: int = 0
    chunks: int = 0
    entities: int = 0
    revision_entries: int = 0
    peak_values_in_memory: int = 0
    already_finished: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def replay_partition(
    log_path: str | Path,
    store_path: str | Path,
    config: FeatureConfig,
    *,
    run_id: str,
    log_sha256: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    crash: CrashPlan | None = None,
    state_factory: Callable[[], EntityState] = EntityState,
) -> ReplayStats:
    """Replay one partition log into its store, resuming from the last checkpoint if there is one."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")
    plan = FeaturePlan(config)
    store = ReplayStore(
        store_path, run_id=run_id, feature_config_hash=config.config_hash(), log_sha256=log_sha256
    )
    stats = ReplayStats(partition=Path(log_path).name)
    try:
        checkpoint = store.latest_checkpoint()
        stats.resumed_from_offset = checkpoint.next_offset
        if checkpoint.finished:
            stats.already_finished = True
            return stats

        # Rebuild in-memory state from the durable revision index.
        states: dict[str, EntityState] = {}
        for (
            logical_event_id,
            entity_id,
            revision,
            is_upsert,
            event_type,
            event_us,
            value,
        ) in store.revision_state():
            state = states.get(entity_id)
            if state is None:
                state = states[entity_id] = state_factory()
            state.restore(logical_event_id, revision, is_upsert, event_type, event_us, value)

        offset = checkpoint.next_offset
        values_in_memory = sum(len(state.in_force) for state in states.values())
        for chunk_number, chunk in enumerate(_read_chunks(Path(log_path), offset, chunk_size)):
            outputs: list[OutputRow] = []
            changed: dict[str, StateRow] = {}
            times = chunk["time_us"]
            kinds = chunk["kind"]
            entities = chunk["entity_id"]
            items = chunk["item_id"]
            revisions = chunk["source_revision"]
            upserts = chunk["is_upsert"]
            types = chunk["event_type"]
            event_times = chunk["event_us"]
            values = chunk["value"]
            encounters = chunk["encounter_id"]
            n_items = len(times)
            halfway = n_items // 2

            for index in range(n_items):
                if crash is not None and index == halfway:
                    crash.maybe("mid_chunk", chunk_number)
                entity_id = entities[index]
                state = states.get(entity_id)
                if state is None:
                    state = states[entity_id] = state_factory()
                if kinds[index] == ARRIVAL:
                    stats.arrivals += 1
                    held_before = len(state.in_force)
                    applied = state.apply(
                        items[index],
                        revisions[index],
                        upserts[index],
                        types[index],
                        event_times[index],
                        values[index],
                        plan,
                    )
                    if applied:
                        changed[items[index]] = (
                            items[index],
                            entity_id,
                            revisions[index],
                            upserts[index],
                            types[index],
                            event_times[index],
                            values[index],
                        )
                    else:
                        stats.stale_or_duplicate_arrivals += 1
                else:
                    stats.triggers += 1
                    held_before = len(state.in_force)
                    row = state.features_at(times[index], plan)
                    outputs.append(
                        (
                            items[index],
                            entity_id,
                            encounters[index],
                            times[index],
                            encode_payload(row),
                        )
                    )
                values_in_memory += len(state.in_force) - held_before
                if values_in_memory > stats.peak_values_in_memory:
                    stats.peak_values_in_memory = values_in_memory

            offset += n_items
            stats.items += n_items
            stats.chunks += 1
            before_commit = None
            if crash is not None:
                active_crash, number = crash, chunk_number
                before_commit = lambda: active_crash.maybe("before_commit", number)  # noqa: B023, E731
            store.commit_chunk(outputs, changed.values(), offset, False, before_commit)
            if crash is not None:
                crash.maybe("after_commit", chunk_number)

        store.commit_chunk([], [], offset, True)
        stats.entities = len(states)
        stats.revision_entries = sum(len(state.revisions) for state in states.values())
        return stats
    finally:
        store.close()


def _read_chunks(path: Path, start: int, chunk_size: int) -> Iterator[dict[str, list[Any]]]:
    """Yield the log from row `start` in chunks of exactly `chunk_size` rows (the last may be shorter)."""
    file = pq.ParquetFile(path)
    groups = []
    skipped = 0
    for index in range(file.num_row_groups):
        rows = file.metadata.row_group(index).num_rows
        if skipped + rows <= start:
            skipped += rows
        else:
            groups.append(index)
    to_drop = start - skipped
    pending: list[pa.RecordBatch] = []
    pending_rows = 0
    for batch in file.iter_batches(batch_size=chunk_size, row_groups=groups, columns=LOG_COLUMNS):
        if to_drop:
            dropped = min(to_drop, batch.num_rows)
            batch = batch.slice(dropped)
            to_drop -= dropped
        if batch.num_rows == 0:
            continue
        pending.append(batch)
        pending_rows += batch.num_rows
        while pending_rows >= chunk_size:
            table = pa.Table.from_batches(pending)
            yield _columns(table.slice(0, chunk_size))
            rest = table.slice(chunk_size)
            pending = rest.to_batches() if rest.num_rows else []
            pending_rows = rest.num_rows
    if pending_rows:
        yield _columns(pa.Table.from_batches(pending))


def _columns(table: pa.Table) -> dict[str, list[Any]]:
    return {name: table.column(name).to_pylist() for name in LOG_COLUMNS}
