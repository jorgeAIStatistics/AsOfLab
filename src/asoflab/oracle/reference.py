"""Reference oracle: section 5 of the temporal contract, written as plainly as possible.

This implementation is deliberately slow and obvious. It loops over every
prediction and every version, and it shares no selection code with the batch
or replay engines, so agreement between them is evidence rather than tautology.
Use it on small data, and to settle disagreements.
"""

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from asoflab.contract import (
    MICROSECONDS_PER_HOUR,
    Aggregation,
    Clock,
    Operation,
    PolicyName,
)
from asoflab.schema.models import (
    EventVersion,
    FeatureConfig,
    FeatureRow,
    FeatureSpec,
    PredictionRow,
    TimePolicy,
)
from asoflab.schema.time import to_us


def compute_features(
    events: Iterable[EventVersion],
    spine: Iterable[PredictionRow],
    config: FeatureConfig,
    policy: TimePolicy,
) -> list[FeatureRow]:
    """Features for every prediction in the spine. One output row per spine row, always."""
    versions_by_entity: dict[str, list[EventVersion]] = defaultdict(list)
    for version in events:
        versions_by_entity[version.entity_id].append(version)

    rows = []
    for prediction in spine:
        cutoff = prediction.prediction_time
        versions = versions_by_entity.get(prediction.entity_id, [])
        active = active_versions(versions, cutoff, policy)
        values: dict[str, float | int | None] = {}
        for feature in config.features:
            contributing = contributing_versions(active, feature, cutoff)
            values[feature.name] = aggregate(feature.aggregation, contributing, cutoff)
        rows.append(
            FeatureRow(
                prediction_id=prediction.prediction_id,
                entity_id=prediction.entity_id,
                encounter_id=prediction.encounter_id,
                prediction_time=cutoff,
                values=values,
            )
        )
    return rows


def known_time(version: EventVersion, policy: TimePolicy) -> datetime | None:
    """When `version` became known on the policy's clock. None means unknown."""
    if policy.clock is Clock.SOURCE:
        moment = version.source_available_time
    else:
        moment = version.pipeline_available_time
    if moment is None and policy.imputes and version.event_time is not None:
        # Explicit sensitivity mode only; such a run is never reported as strict.
        moment = version.event_time + timedelta(seconds=policy.impute_lag_seconds)
    return moment


def active_versions(
    versions: Sequence[EventVersion], cutoff: datetime, policy: TimePolicy
) -> list[EventVersion]:
    """Steps 1 and 2: for each logical event, the version that is in force at `cutoff`.

    A logical event with no usable version is absent from the result. A logical
    event whose active version is a retraction is present, as that retraction.
    """
    versions_by_event: dict[str, list[EventVersion]] = defaultdict(list)
    for version in versions:
        versions_by_event[version.logical_event_id].append(version)

    chosen = []
    for event_versions in versions_by_event.values():
        in_force = select_version(event_versions, cutoff, policy)
        if in_force is not None:
            chosen.append(in_force)
    return chosen


def select_version(
    versions: Sequence[EventVersion], cutoff: datetime, policy: TimePolicy
) -> EventVersion | None:
    """The version of one logical event that the policy reads at `cutoff`."""
    if policy.name is PolicyName.NAIVE_EVENT_TIME:
        # Ignores availability altogether: today's content for every cutoff.
        return highest_revision(versions)

    if policy.name is PolicyName.LATEST_REVISED:
        # Had the observation appeared at all by the cutoff? If so, read today's content.
        appeared = any(
            moment is not None and moment <= cutoff
            for moment in (known_time(version, policy) for version in versions)
        )
        return highest_revision(versions) if appeared else None

    # As-known policies: only versions known by the cutoff exist, as far as the
    # pipeline at that moment is concerned. Versions with unknown availability
    # are quarantined here.
    eligible = []
    for version in versions:
        moment = known_time(version, policy)
        if moment is not None and moment <= cutoff:
            eligible.append(version)
    return highest_revision(eligible) if eligible else None


def highest_revision(versions: Sequence[EventVersion]) -> EventVersion:
    """Source revision order decides, never arrival order."""
    return max(versions, key=lambda version: version.source_revision)


def contributing_versions(
    active: Sequence[EventVersion], feature: FeatureSpec, cutoff: datetime
) -> list[EventVersion]:
    """Step 3: active upserts of the feature's type whose event_time is in (cutoff - W, cutoff]."""
    window_start = cutoff - timedelta(seconds=feature.window_seconds)
    contributing = []
    for version in active:
        if version.operation is not Operation.UPSERT:
            continue
        if version.event_type != feature.event_type:
            continue
        assert version.event_time is not None  # guaranteed for upserts by the record model
        if window_start < version.event_time <= cutoff:
            contributing.append(version)
    return contributing


def aggregate(
    aggregation: Aggregation, contributing: Sequence[EventVersion], cutoff: datetime
) -> float | int | None:
    """Step 4. Each contributing logical event appears exactly once in `contributing`."""
    if aggregation is Aggregation.COUNT:
        return len(contributing)
    if not contributing:
        return None
    if aggregation is Aggregation.MEAN:
        return math.fsum(_value(version) for version in contributing) / len(contributing)
    latest = max(contributing, key=lambda version: (version.event_time, version.logical_event_id))
    if aggregation is Aggregation.LAST:
        return _value(latest)
    assert latest.event_time is not None
    return (to_us(cutoff) - to_us(latest.event_time)) / MICROSECONDS_PER_HOUR


def _value(version: EventVersion) -> float:
    assert version.value is not None  # guaranteed for upserts by the record model
    return version.value
