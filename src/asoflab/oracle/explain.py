"""Why each version was or was not used at a cutoff.

Built on the reference oracle's own selection functions, so the explanation
shown in the audit and the timeline view is the oracle's actual decision and
not a separate reimplementation of it.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from asoflab.contract import Operation, PolicyName
from asoflab.oracle.reference import known_time, select_version
from asoflab.schema.models import EventVersion, FeatureConfig, TimePolicy

USED = "used"
RETRACTION_IN_FORCE = "retraction_in_force"
OUTSIDE_WINDOW = "outside_window"
EVENT_AFTER_CUTOFF = "event_after_cutoff"
NOT_YET_AVAILABLE = "not_yet_available"
SUPERSEDED = "superseded"
UNKNOWN_AVAILABILITY = "unknown_availability"
NOT_REFERENCED = "not_referenced"

_REASONS = {
    USED: "In force at the cutoff and inside a lookback window.",
    RETRACTION_IN_FORCE: "This retraction is in force, so the observation is withdrawn.",
    OUTSIDE_WINDOW: "In force at the cutoff, but older than every lookback window for its type.",
    EVENT_AFTER_CUTOFF: "The measurement itself happened after the cutoff.",
    NOT_YET_AVAILABLE: "Not available on this policy's clock until after the cutoff.",
    SUPERSEDED: "Replaced by a higher source revision that this policy reads at the cutoff.",
    UNKNOWN_AVAILABILITY: "Availability time is unknown, so the version is quarantined.",
    NOT_REFERENCED: "No feature reads this event type.",
}


@dataclass(frozen=True)
class VersionStatus:
    version: EventVersion
    known_time: datetime | None
    status: str
    reason: str
    used_by: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.version.model_dump(mode="json"),
            "known_time": self.known_time.isoformat() if self.known_time else None,
            "status": self.status,
            "reason": self.reason,
            "used_by": self.used_by,
        }


def explain_cutoff(
    versions: Sequence[EventVersion],
    cutoff: datetime,
    config: FeatureConfig,
    policy: TimePolicy,
) -> list[VersionStatus]:
    """Status of every version of one entity for a prediction at `cutoff`."""
    versions_by_event: dict[str, list[EventVersion]] = defaultdict(list)
    for version in versions:
        versions_by_event[version.logical_event_id].append(version)

    statuses = []
    for event_versions in versions_by_event.values():
        chosen = select_version(event_versions, cutoff, policy)
        for version in event_versions:
            known = known_time(version, policy) if policy.clock is not None else None
            if version is chosen:
                status, used_by = _status_of_chosen(version, cutoff, config)
            else:
                status, used_by = _status_of_unchosen(known, cutoff, policy), []
            statuses.append(VersionStatus(version, known, status, _REASONS[status], used_by))
    statuses.sort(key=lambda item: (item.version.logical_event_id, item.version.source_revision))
    return statuses


def _status_of_chosen(
    version: EventVersion, cutoff: datetime, config: FeatureConfig
) -> tuple[str, list[str]]:
    if version.operation is Operation.RETRACT:
        return RETRACTION_IN_FORCE, []
    assert version.event_time is not None
    readers = [feature for feature in config.features if feature.event_type == version.event_type]
    if not readers:
        return NOT_REFERENCED, []
    if version.event_time > cutoff:
        return EVENT_AFTER_CUTOFF, []
    used_by = [
        feature.name
        for feature in readers
        if cutoff - timedelta(seconds=feature.window_seconds) < version.event_time
    ]
    return (USED, used_by) if used_by else (OUTSIDE_WINDOW, [])


def _status_of_unchosen(known: datetime | None, cutoff: datetime, policy: TimePolicy) -> str:
    if policy.name is PolicyName.NAIVE_EVENT_TIME:
        return SUPERSEDED
    if policy.name is PolicyName.LATEST_REVISED:
        # Either no version had appeared by the cutoff, or this one is not the latest revision.
        return SUPERSEDED if known is not None and known <= cutoff else NOT_YET_AVAILABLE
    if known is None:
        return UNKNOWN_AVAILABILITY
    return NOT_YET_AVAILABLE if known > cutoff else SUPERSEDED
