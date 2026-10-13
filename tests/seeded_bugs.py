"""Deliberately wrong implementations of the as-known contract.

Each function below makes one central timestamp or revision mistake that real
pipelines make. They exist so the test suite can show that it catches them:
tests/unit/test_seeded_bugs.py checks that every bug here is exposed by at
least one handwritten fixture, and tests/property/test_bug_discovery.py checks
that the random generator can find each one unaided.

Nothing in `src/` imports this module.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta

from asoflab.contract import Aggregation, Clock, Operation
from asoflab.oracle.reference import aggregate
from asoflab.schema.models import (
    EventVersion,
    FeatureConfig,
    FeatureRow,
    FeatureSpec,
    PredictionRow,
    TimePolicy,
)

Contributing = Callable[
    [Sequence[EventVersion], FeatureSpec, datetime, TimePolicy], list[EventVersion]
]


def _known(version: EventVersion, policy: TimePolicy) -> datetime | None:
    if policy.clock is Clock.SOURCE:
        return version.source_available_time
    return version.pipeline_available_time


def _by_event(versions: Iterable[EventVersion]) -> list[list[EventVersion]]:
    groups: dict[str, list[EventVersion]] = defaultdict(list)
    for version in versions:
        groups[version.logical_event_id].append(version)
    return list(groups.values())


def _top(versions: Sequence[EventVersion]) -> EventVersion:
    return max(versions, key=lambda version: version.source_revision)


def _is_upsert_of(version: EventVersion, feature: FeatureSpec) -> bool:
    return version.operation is Operation.UPSERT and version.event_type == feature.event_type


def _in_window(version: EventVersion, feature: FeatureSpec, cutoff: datetime) -> bool:
    assert version.event_time is not None
    return cutoff - timedelta(seconds=feature.window_seconds) < version.event_time <= cutoff


def _known_by(
    versions: Sequence[EventVersion], cutoff: datetime, policy: TimePolicy
) -> list[EventVersion]:
    return [v for v in versions if (k := _known(v, policy)) is not None and k <= cutoff]


def _correct(versions, feature, cutoff, policy):  # type: ignore[no-untyped-def]
    """The right answer, in the same shape as the bugs, to validate this harness."""
    chosen = [_top(e) for group in _by_event(versions) if (e := _known_by(group, cutoff, policy))]
    return [v for v in chosen if _is_upsert_of(v, feature) and _in_window(v, feature, cutoff)]


def _latest_then_filter(versions, feature, cutoff, policy):  # type: ignore[no-untyped-def]
    """Take today's latest version, then test whether *it* was available.

    A correction that arrives after the cutoff erases the earlier version that
    was genuinely known.
    """
    chosen = [_top(group) for group in _by_event(versions)]
    chosen = [v for v in chosen if (k := _known(v, policy)) is not None and k <= cutoff]
    return [v for v in chosen if _is_upsert_of(v, feature) and _in_window(v, feature, cutoff)]


def _window_before_resolve(versions, feature, cutoff, policy):  # type: ignore[no-untyped-def]
    """Filter versions by event-time window first, then resolve among the survivors.

    A correction that moves an observation out of the window is discarded and
    the superseded version comes back.
    """
    survivors = [
        v
        for v in _known_by(versions, cutoff, policy)
        if v.event_time is not None and _in_window(v, feature, cutoff)
    ]
    chosen = [_top(group) for group in _by_event(survivors)]
    return [v for v in chosen if _is_upsert_of(v, feature)]


def _ignore_availability(versions, feature, cutoff, policy):  # type: ignore[no-untyped-def]
    """Use event time alone. The classic leak."""
    chosen = [_top(group) for group in _by_event(versions)]
    return [v for v in chosen if _is_upsert_of(v, feature) and _in_window(v, feature, cutoff)]


def _first_seen_gate_latest_value(versions, feature, cutoff, policy):  # type: ignore[no-untyped-def]
    """Respect when the observation first appeared, but read today's value."""
    chosen = [_top(group) for group in _by_event(versions) if _known_by(group, cutoff, policy)]
    return [v for v in chosen if _is_upsert_of(v, feature) and _in_window(v, feature, cutoff)]


def _ignore_retractions(versions, feature, cutoff, policy):  # type: ignore[no-untyped-def]
    """Drop retraction records before resolving, so withdrawn observations stay."""
    kept = [v for v in _known_by(versions, cutoff, policy) if v.operation is Operation.UPSERT]
    chosen = [_top(group) for group in _by_event(kept)]
    return [v for v in chosen if _is_upsert_of(v, feature) and _in_window(v, feature, cutoff)]


def _retraction_payload_read(versions, feature, cutoff, policy):  # type: ignore[no-untyped-def]
    """Forget to check the operation: a retraction that carries a value is read."""
    chosen = [_top(e) for group in _by_event(versions) if (e := _known_by(group, cutoff, policy))]
    return [
        v
        for v in chosen
        if v.event_type == feature.event_type
        and v.event_time is not None
        and v.value is not None
        and _in_window(v, feature, cutoff)
    ]


def _arrival_order_resolution(versions, feature, cutoff, policy):  # type: ignore[no-untyped-def]
    """Let the most recently *arrived* version win, instead of the highest revision."""
    chosen = []
    for group in _by_event(versions):
        eligible = _known_by(group, cutoff, policy)
        if eligible:
            chosen.append(max(eligible, key=lambda v: (_known(v, policy), v.source_revision)))
    return [v for v in chosen if _is_upsert_of(v, feature) and _in_window(v, feature, cutoff)]


def _first_version_wins(versions, feature, cutoff, policy):  # type: ignore[no-untyped-def]
    """Never apply corrections: keep the lowest revision."""
    chosen = []
    for group in _by_event(versions):
        eligible = _known_by(group, cutoff, policy)
        if eligible:
            chosen.append(min(eligible, key=lambda v: v.source_revision))
    return [v for v in chosen if _is_upsert_of(v, feature) and _in_window(v, feature, cutoff)]


def _count_every_version(versions, feature, cutoff, policy):  # type: ignore[no-untyped-def]
    """Skip revision resolution: every known upsert row is an observation."""
    return [
        v
        for v in _known_by(versions, cutoff, policy)
        if _is_upsert_of(v, feature) and _in_window(v, feature, cutoff)
    ]


def _exclusive_availability(versions, feature, cutoff, policy):  # type: ignore[no-untyped-def]
    """Require availability strictly before the cutoff."""
    chosen = []
    for group in _by_event(versions):
        eligible = [v for v in group if (k := _known(v, policy)) is not None and k < cutoff]
        if eligible:
            chosen.append(_top(eligible))
    return [v for v in chosen if _is_upsert_of(v, feature) and _in_window(v, feature, cutoff)]


def _inclusive_window_start(versions, feature, cutoff, policy):  # type: ignore[no-untyped-def]
    """Use [T - W, T] instead of (T - W, T]."""
    chosen = [_top(e) for group in _by_event(versions) if (e := _known_by(group, cutoff, policy))]
    start = cutoff - timedelta(seconds=feature.window_seconds)
    return [
        v
        for v in chosen
        if _is_upsert_of(v, feature)
        and v.event_time is not None
        and start <= v.event_time <= cutoff
    ]


def _exclusive_window_end(versions, feature, cutoff, policy):  # type: ignore[no-untyped-def]
    """Use (T - W, T) instead of (T - W, T]."""
    chosen = [_top(e) for group in _by_event(versions) if (e := _known_by(group, cutoff, policy))]
    start = cutoff - timedelta(seconds=feature.window_seconds)
    return [
        v
        for v in chosen
        if _is_upsert_of(v, feature) and v.event_time is not None and start < v.event_time < cutoff
    ]


def _run(
    contributing: Contributing,
    *,
    all_entities: bool = False,
    drop_empty: bool = False,
    tie_lowest_id: bool = False,
) -> Callable[..., list[FeatureRow]]:
    def compute(
        events: Iterable[EventVersion],
        spine: Iterable[PredictionRow],
        config: FeatureConfig,
        policy: TimePolicy,
    ) -> list[FeatureRow]:
        events = list(events)
        rows = []
        for prediction in spine:
            cutoff = prediction.prediction_time
            mine = [v for v in events if all_entities or v.entity_id == prediction.entity_id]
            values: dict[str, float | int | None] = {}
            used = 0
            for feature in config.features:
                chosen = contributing(mine, feature, cutoff, policy)
                used += len(chosen)
                if tie_lowest_id and feature.aggregation is Aggregation.LAST and chosen:
                    newest = max(v.event_time for v in chosen)  # type: ignore[type-var]
                    tied = [v for v in chosen if v.event_time == newest]
                    values[feature.name] = min(tied, key=lambda v: v.logical_event_id).value
                else:
                    values[feature.name] = aggregate(feature.aggregation, chosen, cutoff)
            if drop_empty and used == 0:
                continue
            rows.append(
                FeatureRow(
                    prediction.prediction_id,
                    prediction.entity_id,
                    prediction.encounter_id,
                    cutoff,
                    values,
                )
            )
        return rows

    return compute


# The control: must agree with every fixture, proving the harness itself is sound.
CONTROL = _run(_correct)

SEEDED_BUGS: dict[str, Callable[..., list[FeatureRow]]] = {
    "latest_then_filter": _run(_latest_then_filter),
    "window_before_resolve": _run(_window_before_resolve),
    "ignore_availability": _run(_ignore_availability),
    "first_seen_gate_latest_value": _run(_first_seen_gate_latest_value),
    "ignore_retractions": _run(_ignore_retractions),
    "retraction_payload_read": _run(_retraction_payload_read),
    "arrival_order_resolution": _run(_arrival_order_resolution),
    "first_version_wins": _run(_first_version_wins),
    "count_every_version": _run(_count_every_version),
    "exclusive_availability": _run(_exclusive_availability),
    "inclusive_window_start": _run(_inclusive_window_start),
    "exclusive_window_end": _run(_exclusive_window_end),
    "cross_entity_read": _run(_correct, all_entities=True),
    "drop_empty_predictions": _run(_correct, drop_empty=True),
    "last_tie_lowest_id": _run(_correct, tie_lowest_id=True),
}
