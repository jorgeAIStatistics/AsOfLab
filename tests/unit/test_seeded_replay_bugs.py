"""Seeded mistakes specific to incremental state, and the fixtures that expose them."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from asoflab.fixtures import load_case
from asoflab.replay import CrashPlan, SimulatedCrash, prepare_log
from asoflab.replay.engine import replay_partition
from asoflab.replay.log import LOG_COLUMNS
from asoflab.replay.runner import ReplayResult, collect_outputs, run_id_for
from asoflab.replay.state import EntityState, FeaturePlan
from asoflab.schema.models import TimePolicy
from asoflab.schema.tables import events_to_table, spine_to_table
from support import differences_from_expected

PIPELINE = TimePolicy.parse("pipeline_as_known")


class ForgetsExpiredAndRetracted(EntityState):
    """Frees all memory of a logical event once it is retracted or its value expires."""

    def apply(self, logical_event_id, revision, is_upsert, event_type, event_us, value, plan):  # type: ignore[no-untyped-def]
        applied = super().apply(
            logical_event_id, revision, is_upsert, event_type, event_us, value, plan
        )
        if applied and logical_event_id not in self.in_force:
            del self.revisions[logical_event_id]
        return applied

    def _evict(self, cutoff_us: int, plan: FeaturePlan) -> None:
        before = set(self.in_force)
        super()._evict(cutoff_us, plan)
        for logical_event_id in before - set(self.in_force):
            del self.revisions[logical_event_id]


class RestoresValuesOnly(EntityState):
    """After a restart, rebuilds the values but not the retraction tombstones."""

    def restore(self, logical_event_id, revision, is_upsert, event_type, event_us, value):  # type: ignore[no-untyped-def]
        if is_upsert:
            super().restore(logical_event_id, revision, is_upsert, event_type, event_us, value)


def replay_fixture(
    tmp_path: Path, name: str, *, state_factory=EntityState, crash=None, reorder=None
) -> pa.Table:  # type: ignore[no-untyped-def]
    case = load_case(name)
    prepared = prepare_log(
        events_to_table(case.events),
        spine_to_table(case.spine),
        case.config,
        PIPELINE,
        tmp_path / "prepared",
    )
    log_path = prepared.path(0)
    if reorder is not None:
        log_path = tmp_path / "reordered.parquet"
        pq.write_table(reorder(pq.read_table(prepared.path(0))).select(LOG_COLUMNS), log_path)
    arguments = {
        "log_path": log_path,
        "store_path": tmp_path / "store.sqlite",
        "config": case.config,
        "run_id": run_id_for(prepared, case.config),
        "log_sha256": prepared.partitions[0]["sha256"],
        "chunk_size": 1,
        "state_factory": state_factory,
    }
    if crash is not None:
        with pytest.raises(SimulatedCrash):
            replay_partition(crash=crash, **arguments)
    replay_partition(**arguments)
    result = ReplayResult(arguments["run_id"], [arguments["store_path"]], [])
    return collect_outputs(result, case.config)


def test_correct_state_passes_the_resurrection_fixture(tmp_path: Path) -> None:
    case = load_case("stale_revision_after_expiry")
    table = replay_fixture(tmp_path, case.name)
    assert not differences_from_expected(table, case.expected["pipeline_as_known"])


def test_forgetting_expired_or_retracted_events_resurrects_them(tmp_path: Path) -> None:
    case = load_case("stale_revision_after_expiry")
    table = replay_fixture(tmp_path, case.name, state_factory=ForgetsExpiredAndRetracted)
    problems = differences_from_expected(table, case.expected["pipeline_as_known"])
    assert any(problem.startswith("C.lac_count_6h: got 2") for problem in problems), problems


def test_restart_that_loses_tombstones_resurrects_a_retracted_event(tmp_path: Path) -> None:
    case = load_case("stale_revision_after_expiry")
    # Crash after the 20:00 trigger has been committed (items: 3 arrivals, A, B, 2 arrivals, C).
    crash = CrashPlan("after_commit", 4)
    healthy = replay_fixture(tmp_path / "ok", case.name, crash=crash)
    assert not differences_from_expected(healthy, case.expected["pipeline_as_known"])
    broken = replay_fixture(
        tmp_path / "bad", case.name, state_factory=RestoresValuesOnly, crash=crash
    )
    problems = differences_from_expected(broken, case.expected["pipeline_as_known"])
    assert any(problem.startswith("C.lac_count_6h: got 1") for problem in problems), problems


def test_processing_the_prediction_before_a_simultaneous_arrival_is_caught(tmp_path: Path) -> None:
    case = load_case("delayed_result")

    def triggers_first(log: pa.Table) -> pa.Table:
        return log.sort_by([("time_us", "ascending"), ("kind", "descending")])

    table = replay_fixture(tmp_path, case.name, reorder=triggers_first)
    problems = differences_from_expected(table, case.expected["pipeline_as_known"])
    assert any(problem.startswith("B.lac_count_6h: got 0") for problem in problems), problems
