"""Properties every engine must have, checked on generated archives.

These do not compare engines with each other. Each engine is compared with
itself after a change to the input that the contract says must not matter.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from asoflab.audit.parity import compare_feature_tables
from asoflab.schema.models import TimePolicy
from strategies import Archive, archives, as_known_policies, known_time, policies
from support import ENGINES, save_counterexample

ENGINE_NAMES = sorted(ENGINES)


def assert_same(
    engine: str, before, after, archive: Archive, policy: TimePolicy, name: str
) -> None:  # type: ignore[no-untyped-def]
    report = compare_feature_tables(
        before, after, archive.config, left_name="before", right_name="after"
    )
    if not report.equivalent:
        path = save_counterexample(
            f"{name}_{engine}",
            archive.events,
            archive.spine,
            archive.config,
            policy,
            {"engine": engine, "examples": [example.to_dict() for example in report.examples]},
        )
        raise AssertionError(f"{engine}: {report.describe()} Counterexample saved to {path}.")


@pytest.mark.parametrize("engine", ENGINE_NAMES)
@given(archive=archives(), policy=policies, data=st.data())
def test_input_row_order_does_not_matter(
    engine: str, archive: Archive, policy: TimePolicy, data: st.DataObject
) -> None:
    run = ENGINES[engine]
    before = run(archive.events, archive.spine, archive.config, policy)
    shuffled_events = data.draw(st.permutations(archive.events))
    shuffled_spine = data.draw(st.permutations(archive.spine))
    after = run(shuffled_events, shuffled_spine, archive.config, policy)
    assert_same(engine, before, after, archive, policy, "row_order")


@pytest.mark.parametrize("engine", ENGINE_NAMES)
@given(archive=archives(), policy=policies)
def test_exact_duplicates_cannot_double_count(
    engine: str, archive: Archive, policy: TimePolicy
) -> None:
    run = ENGINES[engine]
    before = run(archive.events, archive.spine, archive.config, policy)
    after = run(archive.events + archive.events, archive.spine, archive.config, policy)
    assert_same(engine, before, after, archive, policy, "duplicates")


@pytest.mark.parametrize("engine", ENGINE_NAMES)
@given(archive=archives(), policy=as_known_policies, cutoff=st.integers(0, 24))
def test_versions_known_only_later_change_nothing_earlier(
    engine: str, archive: Archive, policy: TimePolicy, cutoff: int
) -> None:
    """Appending versions unavailable until after T leaves as-known outputs through T unchanged."""
    from strategies import at

    horizon = at(cutoff)
    early_spine = [row for row in archive.spine if row.prediction_time <= horizon]
    if not early_spine:
        return
    known_by_then = [
        version
        for version in archive.events
        if (moment := known_time(version, policy)) is not None and moment <= horizon
    ]
    run = ENGINES[engine]
    with_future = run(archive.events, early_spine, archive.config, policy)
    without_future = run(known_by_then, early_spine, archive.config, policy)
    assert_same(engine, without_future, with_future, archive, policy, "future_append")


@pytest.mark.parametrize("engine", ENGINE_NAMES)
@given(archive=archives(), policy=policies)
def test_every_prediction_gets_exactly_one_row(
    engine: str, archive: Archive, policy: TimePolicy
) -> None:
    table = ENGINES[engine](archive.events, archive.spine, archive.config, policy)
    assert table.column("prediction_id").to_pylist() == sorted(
        row.prediction_id for row in archive.spine
    )
    by_id = {row.prediction_id: row for row in archive.spine}
    for row in table.to_pylist():
        assert row["entity_id"] == by_id[row["prediction_id"]].entity_id
        assert row["prediction_time"] == by_id[row["prediction_id"]].prediction_time


@pytest.mark.parametrize("engine", ENGINE_NAMES)
@given(archive=archives(), policy=policies)
def test_other_entities_events_never_matter(
    engine: str, archive: Archive, policy: TimePolicy
) -> None:
    """Removing every other entity's events leaves one entity's features unchanged."""
    entity = archive.spine[0].entity_id
    own_spine = [row for row in archive.spine if row.entity_id == entity]
    own_events = [version for version in archive.events if version.entity_id == entity]
    run = ENGINES[engine]
    assert_same(
        engine,
        run(own_events, own_spine, archive.config, policy),
        run(archive.events, own_spine, archive.config, policy),
        archive,
        policy,
        "entity_isolation",
    )


@given(
    archive=archives(max_logical_events=10, max_predictions=10),
    policy=policies,
    n_partitions=st.integers(1, 4),
    chunk_size=st.integers(1, 7),
)
def test_replay_layout_does_not_matter(
    archive: Archive, policy: TimePolicy, n_partitions: int, chunk_size: int
) -> None:
    """Chunk size and partition count change how the work is cut up, never the answer."""
    run = ENGINES["replay"]
    baseline = run(archive.events, archive.spine, archive.config, policy)
    other = run(
        archive.events,
        archive.spine,
        archive.config,
        policy,
        n_partitions=n_partitions,
        chunk_size=chunk_size,
    )
    assert other.equals(baseline), "replay output must be bit-identical across layouts"
