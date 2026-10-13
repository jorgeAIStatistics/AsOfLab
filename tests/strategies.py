"""Hypothesis strategies that generate small adversarial archives.

The generator is built to collide. Every time lies on a 30-minute grid inside
half a day, and every window is a whole number of grid steps. On top of that,
each archive picks one or two "hot" instants and concentrates measurements,
arrivals and predictions on them and on the window edges they imply, and most
events belong to one entity and one event type. So exact ties (availability at
the cutoff, an event exactly on a window edge, two observations at the same
instant) are the normal case rather than a rare one.

Revisions arrive in any order, change event times, retract and reinstate, and
sometimes have unknown availability.

How well this works is measured, not assumed: scripts/seeded_bug_report.py
counts how many generated archives it takes to expose each seeded bug.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from hypothesis import strategies as st

from asoflab.schema.models import EventVersion, FeatureConfig, PredictionRow, TimePolicy
from support import POLICIES

BASE = datetime(2025, 3, 1, tzinfo=UTC)
STEP = timedelta(minutes=30)
LAST_SLOT = 24

EVENT_TYPES = ["lab.lactate", "vital.heart_rate"]
WINDOW_SECONDS = [3600, 7200, 21600]
AGGREGATIONS = ["last", "count", "mean", "time_since_last"]


def at(slot: int) -> datetime:
    return BASE + slot * STEP


@dataclass(frozen=True)
class Archive:
    events: list[EventVersion]
    spine: list[PredictionRow]
    config: FeatureConfig

    def __repr__(self) -> str:  # compact output when Hypothesis reports a failure
        lines = [f"Archive({len(self.events)} versions, {len(self.spine)} predictions)"]
        for v in self.events:
            lines.append(
                f"  {v.entity_id} {v.logical_event_id} r{v.source_revision} {v.operation.value} "
                f"{v.event_type} event={_clock(v.event_time)} value={v.value} "
                f"src={_clock(v.source_available_time)} pipe={_clock(v.pipeline_available_time)}"
            )
        for row in self.spine:
            lines.append(
                f"  predict {row.prediction_id} {row.entity_id} at {_clock(row.prediction_time)}"
            )
        lines.append(f"  features {self.config.names}")
        return "\n".join(lines)


def _clock(moment: datetime | None) -> str:
    return "-" if moment is None else moment.strftime("%H:%M")


slots = st.integers(0, LAST_SLOT)
values = st.integers(-500, 500).map(lambda tenths: tenths / 10)
policies = st.sampled_from(POLICIES)
as_known_policies = st.sampled_from([p for p in POLICIES if p.faithful_replay])

# Most records share an entity and an event type, so that they interact.
entity_ids = st.sampled_from(["p0", "p0", "p0", "p1", "p2"])
event_types = st.sampled_from(["lab.lactate", "lab.lactate", "vital.heart_rate"])
# Window lengths in grid steps: predictions placed this far after a hot instant sit on a window edge.
_EDGE_STEPS = [0, *(seconds // int(STEP.total_seconds()) for seconds in WINDOW_SECONDS)]


def near(hot: list[int]) -> st.SearchStrategy[int]:
    """A slot that is usually one of the archive's hot instants."""
    return st.one_of(st.sampled_from(hot), st.sampled_from(hot), slots)


def maybe_near(hot: list[int]) -> st.SearchStrategy[int | None]:
    """An availability slot: usually hot, sometimes anywhere, about one time in six unknown."""
    return st.one_of(
        st.none(), st.sampled_from(hot), st.sampled_from(hot), st.sampled_from(hot), slots, slots
    )


def on_an_edge(hot: list[int]) -> st.SearchStrategy[int]:
    """A prediction slot: often exactly at a hot instant or exactly one window after it."""
    edge = st.builds(
        lambda slot, steps: min(slot + steps, LAST_SLOT),
        st.sampled_from(hot),
        st.sampled_from(_EDGE_STEPS),
    )
    return st.one_of(edge, edge, slots)


@st.composite
def logical_events(draw: st.DrawFn, index: int, hot: list[int]) -> list[EventVersion]:
    entity_id = draw(entity_ids)
    event_type = draw(event_types)
    usual_event_slot = draw(near(hot))
    revisions = draw(st.lists(st.integers(1, 6), min_size=1, max_size=4, unique=True))
    versions = []
    for revision in revisions:
        retract = draw(st.sampled_from([False, False, False, False, True]))
        moved = draw(st.sampled_from([False, False, True]))
        event_slot = draw(slots) if moved else usual_event_slot
        if retract and draw(st.booleans()):
            event_time, value = None, None  # a bare tombstone
        else:
            event_time, value = at(event_slot), draw(values)
        source_slot = draw(maybe_near(hot))
        pipeline_slot = draw(maybe_near(hot))
        versions.append(
            EventVersion(
                entity_id=entity_id,
                logical_event_id=f"e{index}",
                version_id=f"v{revision}",
                source_revision=revision,
                operation="retract" if retract else "upsert",
                event_type=event_type,
                event_time=event_time,
                value=value,
                unit=None,
                source="generated",
                schema_version="1",
                source_available_time=None if source_slot is None else at(source_slot),
                source_availability_provenance="unknown" if source_slot is None else "observed",
                pipeline_available_time=None if pipeline_slot is None else at(pipeline_slot),
                pipeline_availability_provenance="unknown" if pipeline_slot is None else "observed",
            )
        )
    return versions


@st.composite
def feature_configs(draw: st.DrawFn) -> FeatureConfig:
    choices = [
        (event_type, aggregation, window)
        for event_type in EVENT_TYPES
        for aggregation in AGGREGATIONS
        for window in WINDOW_SECONDS
    ]
    chosen = draw(st.lists(st.sampled_from(choices), min_size=1, max_size=4, unique=True))
    return FeatureConfig(
        features=[
            {
                "name": f"{event_type.split('.')[1]}_{aggregation}_{window}",
                "event_type": event_type,
                "aggregation": aggregation,
                "window_seconds": window,
            }
            for event_type, aggregation, window in chosen
        ]
    )


@st.composite
def archives(draw: st.DrawFn, max_logical_events: int = 6, max_predictions: int = 6) -> Archive:
    hot = draw(st.lists(slots, min_size=1, max_size=2))
    n_events = draw(st.integers(0, max_logical_events))
    events = [version for index in range(n_events) for version in draw(logical_events(index, hot))]
    # Deliver some versions more than once, exactly as an at-least-once feed would.
    if events:
        repeats = draw(st.lists(st.integers(0, len(events) - 1), max_size=2))
        events = events + [events[position] for position in repeats]
    events = draw(st.permutations(events))
    spine = [
        PredictionRow(
            prediction_id=f"q{index}",
            # "p9" never has any events: its predictions must still come out.
            entity_id=draw(st.one_of(entity_ids, st.just("p9"))),
            prediction_time=at(draw(on_an_edge(hot))),
        )
        for index in range(draw(st.integers(1, max_predictions)))
    ]
    return Archive(list(events), spine, draw(feature_configs()))


def known_time(version: EventVersion, policy: TimePolicy) -> datetime | None:
    """Test-side statement of the policy clock, for building 'known by T' subsets."""
    from asoflab.contract import Clock

    moment = (
        version.source_available_time
        if policy.clock is Clock.SOURCE
        else version.pipeline_available_time
    )
    if moment is None and policy.imputes and version.event_time is not None:
        moment = version.event_time + timedelta(seconds=policy.impute_lag_seconds)
    return moment
