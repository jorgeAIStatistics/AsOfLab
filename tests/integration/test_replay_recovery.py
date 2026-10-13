"""Crash and restart behaviour of the replay engine's local recovery contract.

"Crash" here means the engine stops at a chosen point without finishing: in
the middle of a chunk, after writing a chunk but before committing it, or just
after the commit. The claim under test is narrow and local: after any such
stop, running again yields exactly the committed outputs an uninterrupted run
would have produced, and an issued prediction is never changed.
"""

import sqlite3
from pathlib import Path

import pyarrow as pa
import pytest
from hypothesis import given
from hypothesis import strategies as st

from asoflab.fixtures import FixtureCase, load_case
from asoflab.replay import CrashPlan, SimulatedCrash, collect_outputs, prepare_log, run_replay
from asoflab.replay.engine import CRASH_POINTS
from asoflab.replay.store import DeterminismError, ReplayStore, StoreMismatch
from asoflab.schema.models import FeatureConfig, TimePolicy
from asoflab.schema.tables import events_to_table, spine_to_table
from strategies import Archive, archives, policies
from support import differences_from_expected

PIPELINE = TimePolicy.parse("pipeline_as_known")
RECOVERY_FIXTURES = [
    "late_event_affects_future_only",
    "stale_revision_after_expiry",
    "future_append_invariance",
    "upsert_after_retract",
    "revision_changes_event_time",
]


def prepare(
    directory: Path, events, spine, config: FeatureConfig, policy: TimePolicy, n_partitions: int = 1
):  # type: ignore[no-untyped-def]
    return prepare_log(
        events_to_table(events),
        spine_to_table(spine),
        config,
        policy,
        directory / "prepared",
        n_partitions=n_partitions,
    )


def run_with_crashes(
    prepared, stores: Path, config: FeatureConfig, crashes, chunk_sizes
) -> tuple[pa.Table, int]:  # type: ignore[no-untyped-def]
    """Run, crash at each planned point in turn, then run to completion. Returns (outputs, crashes hit)."""
    hit = 0
    for crash, chunk_size in zip(crashes, chunk_sizes, strict=False):
        try:
            run_replay(prepared, stores, config, chunk_size=chunk_size, crash=crash)
        except SimulatedCrash:
            hit += 1
    result = run_replay(prepared, stores, config, chunk_size=chunk_sizes[-1])
    return collect_outputs(result, config), hit


def uninterrupted(directory: Path, case_or_archive, policy: TimePolicy) -> pa.Table:  # type: ignore[no-untyped-def]
    prepared = prepare(
        directory, case_or_archive.events, case_or_archive.spine, case_or_archive.config, policy
    )
    return collect_outputs(
        run_replay(prepared, directory / "stores", case_or_archive.config), case_or_archive.config
    )


@pytest.mark.parametrize("name", RECOVERY_FIXTURES)
@pytest.mark.parametrize("point", CRASH_POINTS)
def test_a_crash_at_any_chunk_leaves_the_same_committed_outputs(
    tmp_path: Path, name: str, point: str
) -> None:
    case: FixtureCase = load_case(name)
    expected = uninterrupted(tmp_path / "clean", case, PIPELINE)
    assert not differences_from_expected(expected, case.expected["pipeline_as_known"])
    n_items = len(case.events) + len(case.spine)
    crashes_hit = 0
    for chunk in range(n_items):
        directory = tmp_path / f"{point}-{chunk}"
        prepared = prepare(directory, case.events, case.spine, case.config, PIPELINE)
        table, hit = run_with_crashes(
            prepared, directory / "stores", case.config, [CrashPlan(point, chunk)], [1, 1]
        )
        crashes_hit += hit
        assert table.equals(expected), f"{name}: crash at {point} in chunk {chunk}"
    assert crashes_hit == n_items, (
        "with one item per chunk, every planned crash must actually happen"
    )


@given(
    archive=archives(max_logical_events=10, max_predictions=10),
    policy=policies,
    crashes=st.lists(
        st.tuples(st.sampled_from(CRASH_POINTS), st.integers(0, 6)), min_size=1, max_size=4
    ),
    chunk_sizes=st.lists(st.integers(1, 6), min_size=5, max_size=5),
)
def test_repeated_crashes_and_changing_chunk_sizes_do_not_change_the_result(
    tmp_path_factory: pytest.TempPathFactory,
    archive: Archive,
    policy: TimePolicy,
    crashes,
    chunk_sizes,
) -> None:
    directory = tmp_path_factory.mktemp("recovery")
    expected = uninterrupted(directory / "clean", archive, policy)
    prepared = prepare(directory / "crashed", archive.events, archive.spine, archive.config, policy)
    table, _ = run_with_crashes(
        prepared,
        directory / "crashed" / "stores",
        archive.config,
        [CrashPlan(point, chunk) for point, chunk in crashes],
        chunk_sizes,
    )
    assert table.equals(expected)


def test_an_issued_prediction_is_not_rewritten_by_a_late_arrival(tmp_path: Path) -> None:
    """Stop right after prediction A is committed, look at it, then let the late event arrive."""
    case = load_case("late_event_affects_future_only")
    prepared = prepare(tmp_path, case.events, case.spine, case.config, PIPELINE)
    stores = tmp_path / "stores"
    # Log order: e2 arrives 07:05, trigger A 09:00, e1 arrives 11:00, trigger B, trigger C.
    with pytest.raises(SimulatedCrash):
        run_replay(prepared, stores, case.config, chunk_size=1, crash=CrashPlan("after_commit", 1))

    connection = sqlite3.connect(stores / "part-00000.sqlite")
    issued = connection.execute("SELECT prediction_id, payload FROM outputs").fetchall()
    connection.close()
    assert [row[0] for row in issued] == ["A"]

    final = collect_outputs(run_replay(prepared, stores, case.config, chunk_size=1), case.config)
    assert not differences_from_expected(final, case.expected["pipeline_as_known"])
    connection = sqlite3.connect(stores / "part-00000.sqlite")
    after = connection.execute("SELECT payload FROM outputs WHERE prediction_id = 'A'").fetchone()
    connection.close()
    assert after[0] == issued[0][1], "prediction A must be byte-for-byte what was first issued"
    by_id = {row["prediction_id"]: row for row in final.to_pylist()}
    assert by_id["A"]["lac_count_6h"] == 1, "A never sees the event that arrived two hours later"
    assert by_id["B"]["lac_count_6h"] == 2, "B does, because the event is still inside its window"


def test_rewinding_a_checkpoint_without_its_state_is_detected(tmp_path: Path) -> None:
    """Unsupported recovery path: the resume point is moved back but the state is not.

    Reprocessing prediction A against state that already contains the late event
    would give a different answer. The store must refuse rather than overwrite.
    """
    case = load_case("late_event_affects_future_only")
    prepared = prepare(tmp_path, case.events, case.spine, case.config, PIPELINE)
    stores = tmp_path / "stores"
    with pytest.raises(SimulatedCrash):
        run_replay(prepared, stores, case.config, chunk_size=1, crash=CrashPlan("after_commit", 2))
    connection = sqlite3.connect(stores / "part-00000.sqlite")
    connection.execute("DELETE FROM checkpoints WHERE sequence > 1")  # back to before trigger A
    connection.commit()
    connection.close()
    with pytest.raises(DeterminismError, match="already issued"):
        run_replay(prepared, stores, case.config, chunk_size=1)
    connection = sqlite3.connect(stores / "part-00000.sqlite")
    payload = connection.execute(
        "SELECT payload FROM outputs WHERE prediction_id = 'A'"
    ).fetchone()[0]
    connection.close()
    assert payload == "[1.0,1,1.0,2.0]", "the issued prediction is untouched"


def test_a_finished_run_can_be_invoked_again_without_effect(tmp_path: Path) -> None:
    case = load_case("future_append_invariance")
    prepared = prepare(tmp_path, case.events, case.spine, case.config, PIPELINE)
    first = run_replay(prepared, tmp_path / "stores", case.config, chunk_size=2)
    again = run_replay(prepared, tmp_path / "stores", case.config, chunk_size=3)
    assert again.run_id == first.run_id
    assert all(stats.already_finished and stats.items == 0 for stats in again.stats)
    assert collect_outputs(again, case.config).equals(collect_outputs(first, case.config))


def test_a_store_is_never_reused_for_a_different_run(tmp_path: Path) -> None:
    case = load_case("correction_keeps_earlier_version")
    as_known = prepare(tmp_path / "a", case.events, case.spine, case.config, PIPELINE)
    naive = prepare(
        tmp_path / "b", case.events, case.spine, case.config, TimePolicy.parse("naive_event_time")
    )
    stores = tmp_path / "stores"
    run_replay(as_known, stores, case.config)
    with pytest.raises(StoreMismatch, match="different run"):
        run_replay(naive, stores, case.config)
    with pytest.raises(FileExistsError, match="never overwritten"):
        prepare(tmp_path / "a", case.events, case.spine, case.config, PIPELINE)


def test_outputs_are_keyed_by_run_prediction_and_feature_definition(tmp_path: Path) -> None:
    store = ReplayStore(
        tmp_path / "s.sqlite", run_id="run-1", feature_config_hash="f1", log_sha256="x"
    )
    row = ("A", "p1", None, 1, "[1.0]")
    store.commit_chunk([row], [], 1, False)
    store.commit_chunk([row], [], 2, False)  # identical content: accepted, nothing changes
    assert store.counts()["outputs"] == 1
    with pytest.raises(DeterminismError):
        store.commit_chunk([("A", "p1", None, 1, "[2.0]")], [], 3, False)
    assert store.latest_checkpoint().next_offset == 2, "the failed commit left no trace"
    assert store.outputs() == [row]
    store.close()


def test_parallel_workers_and_partition_layouts_agree_with_the_batch_backend(
    tmp_path: Path,
) -> None:
    from asoflab.audit.parity import compare_feature_tables
    from asoflab.batch import compute_features
    from asoflab.simulation.workload import WorkloadSpec, generate_workload, workload_features

    events, spine = generate_workload(
        WorkloadSpec(n_events=6000, seed=3, skew="zipf", events_per_entity=40)
    )
    config = workload_features()
    reference = compute_features(events, spine, config, PIPELINE)
    outputs = {}
    for n_partitions, workers, chunk_size in [
        (1, 1, 50_000),
        (4, 1, 997),
        (4, 3, 250),
        (7, 2, 10_000),
    ]:
        directory = tmp_path / f"p{n_partitions}-w{workers}"
        prepared = prepare_log(
            events, spine, config, PIPELINE, directory / "prepared", n_partitions=n_partitions
        )
        entities_per_file = [
            set(
                __import__("pyarrow.parquet")
                .parquet.read_table(prepared.path(i))
                .column("entity_id")
                .to_pylist()
            )
            for i in range(n_partitions)
        ]
        for i, mine in enumerate(entities_per_file):
            for other in entities_per_file[i + 1 :]:
                assert not (mine & other), "an entity's records must all be in one partition"
        result = run_replay(
            prepared, directory / "stores", config, workers=workers, chunk_size=chunk_size
        )
        outputs[(n_partitions, workers)] = collect_outputs(result, config)
        assert sum(stats.triggers for stats in result.stats) == spine.num_rows
    baseline = outputs[(1, 1)]
    for layout, table in outputs.items():
        assert table.equals(baseline), f"layout {layout} changed the output"
    report = compare_feature_tables(
        reference, baseline, config, left_name="batch", right_name="replay"
    )
    assert report.equivalent, report.describe()
