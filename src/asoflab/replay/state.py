"""Version-aware state for one entity during incremental replay.

Two things are remembered for each entity:

`revisions` maps every logical event ever seen to the highest source revision
applied so far. It is never evicted. Without it, a stale revision delivered
long after an observation was retracted or had aged out of every window would
look like a brand-new observation and bring it back to life.

`values` holds, per event type, the in-force upserts that can still fall
inside some future window, sorted by (event time, logical event id). Entries
are evicted once they are older than the longest window for their type.

Memory therefore has a bounded part (values: longest window times event rate)
and an unbounded part (revisions: one small entry per logical event, for the
whole run). Version 1 assumes no bound on how late a stale revision can
arrive, which is why the second part is never dropped.
"""

import math
from bisect import bisect_left, bisect_right, insort
from collections.abc import Sequence
from operator import itemgetter

from asoflab.contract import MICROSECONDS_PER_HOUR, MICROSECONDS_PER_SECOND, Aggregation
from asoflab.schema.models import FeatureConfig

_event_us = itemgetter(0)

# (event_us, logical_event_id, value)
Entry = tuple[int, str, float]


class FeaturePlan:
    """The feature set arranged for evaluation: features that share a window are computed together."""

    def __init__(self, config: FeatureConfig) -> None:
        self.n_features = len(config.features)
        self.longest_us = {
            event_type: seconds * MICROSECONDS_PER_SECOND
            for event_type, seconds in config.max_window_seconds().items()
        }
        grouped: dict[tuple[str, int], list[tuple[int, Aggregation]]] = {}
        for position, feature in enumerate(config.features):
            key = (feature.event_type, feature.window_seconds * MICROSECONDS_PER_SECOND)
            grouped.setdefault(key, []).append((position, feature.aggregation))
        self.groups = [
            (event_type, window_us, wanted) for (event_type, window_us), wanted in grouped.items()
        ]


class EntityState:
    __slots__ = ("in_force", "revisions", "values", "watermark_us")

    def __init__(self) -> None:
        self.revisions: dict[str, int] = {}
        # logical_event_id -> (event_type, event_us, value), for upserts currently held in `values`
        self.in_force: dict[str, tuple[str, int, float]] = {}
        self.values: dict[str, list[Entry]] = {}
        self.watermark_us: int | None = None  # time of this entity's most recent trigger

    def apply(
        self,
        logical_event_id: str,
        revision: int,
        is_upsert: bool,
        event_type: str,
        event_us: int | None,
        value: float | None,
        plan: FeaturePlan,
    ) -> bool:
        """Apply one arriving version. Returns False if it was stale or a duplicate."""
        seen = self.revisions.get(logical_event_id)
        if seen is not None and revision <= seen:
            return False  # source revision order decides, never arrival order
        self.revisions[logical_event_id] = revision
        self._drop(logical_event_id)
        if is_upsert:
            assert event_us is not None
            assert value is not None
            if not self._already_expired(event_type, event_us, plan):
                self.in_force[logical_event_id] = (event_type, event_us, value)
                insort(self.values.setdefault(event_type, []), (event_us, logical_event_id, value))
        return True

    def restore(
        self,
        logical_event_id: str,
        revision: int,
        is_upsert: bool,
        event_type: str,
        event_us: int | None,
        value: float | None,
    ) -> None:
        """Rebuild state from the durable revision index after a restart."""
        self.revisions[logical_event_id] = revision
        if is_upsert:
            assert event_us is not None
            assert value is not None
            self.in_force[logical_event_id] = (event_type, event_us, value)
            insort(self.values.setdefault(event_type, []), (event_us, logical_event_id, value))

    def features_at(self, cutoff_us: int, plan: FeaturePlan) -> list[float | int | None]:
        """Feature values for a prediction at `cutoff_us`, then evict what can no longer matter."""
        result: list[float | int | None] = [None] * plan.n_features
        for event_type, window_us, wanted in plan.groups:
            entries = self.values.get(event_type, ())
            # Window (cutoff - W, cutoff] on event time.
            low = bisect_right(entries, cutoff_us - window_us, key=_event_us)
            high = bisect_right(entries, cutoff_us, key=_event_us)
            _aggregate(entries, low, high, cutoff_us, wanted, result)
        self._evict(cutoff_us, plan)
        return result

    def _already_expired(self, event_type: str, event_us: int, plan: FeaturePlan) -> bool:
        # Later triggers for this entity are at or after the watermark, so a value this
        # old can never be read. Its revision is still remembered.
        if self.watermark_us is None:
            return False
        return event_us <= self.watermark_us - plan.longest_us[event_type]

    def _drop(self, logical_event_id: str) -> None:
        held = self.in_force.pop(logical_event_id, None)
        if held is not None:
            event_type, event_us, value = held
            entries = self.values[event_type]
            del entries[bisect_left(entries, (event_us, logical_event_id, value))]

    def _evict(self, cutoff_us: int, plan: FeaturePlan) -> None:
        self.watermark_us = cutoff_us
        for event_type, entries in self.values.items():
            expired = bisect_right(entries, cutoff_us - plan.longest_us[event_type], key=_event_us)
            if expired:
                for _, logical_event_id, _ in entries[:expired]:
                    del self.in_force[logical_event_id]
                del entries[:expired]


def _aggregate(
    entries: Sequence[Entry],
    low: int,
    high: int,
    cutoff_us: int,
    wanted: Sequence[tuple[int, Aggregation]],
    result: list[float | int | None],
) -> None:
    count = high - low
    for position, aggregation in wanted:
        if aggregation is Aggregation.COUNT:
            result[position] = count
        elif count == 0:
            result[position] = None
        elif aggregation is Aggregation.MEAN:
            # Recomputed from the window each time. A running sum that adds on arrival
            # and subtracts on expiry would drift.
            result[position] = math.fsum(entry[2] for entry in entries[low:high]) / count
        elif aggregation is Aggregation.LAST:
            # Entries are sorted by (event time, logical event id): the last one in the
            # window is the latest, with ties going to the greater identifier.
            result[position] = entries[high - 1][2]
        else:
            result[position] = (cutoff_us - entries[high - 1][0]) / MICROSECONDS_PER_HOUR
