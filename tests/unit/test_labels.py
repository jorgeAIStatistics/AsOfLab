"""Fixed-horizon labels: boundaries, maturity, and the rule that unknown is never zero."""

from datetime import UTC, datetime, timedelta

import pyarrow as pa
import pytest

from asoflab.evaluation.labels import (
    NEGATIVE,
    PENDING_ASCERTAINMENT,
    PENDING_HORIZON,
    POSITIVE,
    UNKNOWN_FOLLOW_UP,
    assign_labels,
    label_coverage,
)
from asoflab.schema.models import FollowUp, LabelSpec, OutcomeEvent, PredictionRow
from asoflab.schema.tables import follow_up_to_table, outcomes_to_table, spine_to_table

DAY = datetime(2025, 3, 1, tzinfo=UTC)
SPEC = LabelSpec(
    outcome_type="deterioration", horizon_seconds=12 * 3600, max_ascertainment_lag_seconds=6 * 3600
)


def at(hours: float) -> datetime:
    return DAY + timedelta(hours=hours)


def prediction(
    name: str, hours: float, encounter: str = "enc1", entity: str = "p1"
) -> PredictionRow:
    return PredictionRow(
        prediction_id=name, entity_id=entity, encounter_id=encounter, prediction_time=at(hours)
    )


def outcome(
    hours: float, known_after: float = 0.0, encounter: str = "enc1", kind: str = "deterioration"
) -> OutcomeEvent:
    return OutcomeEvent(
        outcome_event_id=f"{encounter}-{kind}-{hours}",
        entity_id="p1",
        encounter_id=encounter,
        outcome_type=kind,
        event_time=at(hours),
        ascertainment_time=at(hours + known_after),
    )


def follow_up(end_hours: float, encounter: str = "enc1") -> FollowUp:
    return FollowUp(
        entity_id="p1", encounter_id=encounter, follow_up_start=at(0), follow_up_end=at(end_hours)
    )


def statuses(spine, outcomes, follow_ups, as_of=None) -> dict[str, tuple[int | None, str]]:  # type: ignore[no-untyped-def]
    table = assign_labels(
        spine_to_table(spine),
        outcomes_to_table(outcomes),
        follow_up_to_table(follow_ups),
        SPEC,
        as_of=as_of,
    )
    return {row["prediction_id"]: (row["label"], row["label_status"]) for row in table.to_pylist()}


def test_horizon_boundaries() -> None:
    """The target window is (T, T + 12h]: open at the prediction time, closed at the horizon."""
    spine = [prediction("at_event", 20), prediction("just_before", 19.999), prediction("exactly_12h_before", 8),
             prediction("just_over_12h_before", 7.999), prediction("after_event", 21)]  # fmt: skip
    result = statuses(spine, [outcome(20)], [follow_up(60)])
    assert result["at_event"] == (0, NEGATIVE), (
        "an event at the prediction instant is not in the future"
    )
    assert result["just_before"] == (1, POSITIVE)
    assert result["exactly_12h_before"] == (1, POSITIVE), "the horizon end is included"
    assert result["just_over_12h_before"] == (0, NEGATIVE)
    assert result["after_event"] == (0, NEGATIVE)


def test_unknown_follow_up_is_never_a_negative_label() -> None:
    spine = [
        prediction("covered", 10),
        prediction("cut_short", 30),
        prediction("no_record", 10, encounter="enc2"),
    ]
    result = statuses(spine, [], [follow_up(40)])
    assert result["covered"] == (0, NEGATIVE), (
        "follow-up to hour 40 covers the horizon ending at hour 22"
    )
    assert result["cut_short"] == (None, UNKNOWN_FOLLOW_UP), (
        "follow-up ends at 40, the horizon at 42"
    )
    assert result["no_record"] == (None, UNKNOWN_FOLLOW_UP)
    table = assign_labels(
        spine_to_table(spine), outcomes_to_table([]), follow_up_to_table([follow_up(40)]), SPEC
    )
    assert table.column("label").null_count == 2
    assert table.column("label").to_pylist().count(0) == 1


def test_an_event_seen_before_follow_up_ended_is_still_positive() -> None:
    result = statuses([prediction("a", 30)], [outcome(35)], [follow_up(40)])
    assert result["a"] == (1, POSITIVE)


def test_label_maturity_as_of_a_cutoff() -> None:
    """Horizon 12 h, maximum ascertainment lag 6 h. Prediction at hour 10: horizon ends at 22, mature at 28."""
    spine = [prediction("a", 10)]
    follow = [follow_up(100)]
    happened_known_late = [outcome(15, known_after=5)]  # happens at 15, known at 20

    assert statuses(spine, happened_known_late, follow, as_of=at(19))["a"] == (
        None,
        PENDING_HORIZON,
    )
    assert statuses(spine, happened_known_late, follow, as_of=at(20))["a"] == (1, POSITIVE)
    assert statuses(spine, [], follow, as_of=at(21.9))["a"] == (None, PENDING_HORIZON)
    assert statuses(spine, [], follow, as_of=at(22))["a"] == (None, PENDING_ASCERTAINMENT)
    assert statuses(spine, [], follow, as_of=at(27.9))["a"] == (None, PENDING_ASCERTAINMENT)
    assert statuses(spine, [], follow, as_of=at(28))["a"] == (0, NEGATIVE)
    assert statuses(spine, [], follow)["a"] == (0, NEGATIVE)


def test_labelling_at_the_horizon_without_waiting_would_mislabel_a_late_ascertained_event() -> None:
    """The reason pending_ascertainment exists: at hour 22 the event is real but not yet known."""
    spine = [prediction("a", 10)]
    late = [outcome(21, known_after=5)]  # happens at 21, known at 26
    assert statuses(spine, late, [follow_up(100)], as_of=at(22))["a"] == (
        None,
        PENDING_ASCERTAINMENT,
    )
    assert statuses(spine, late, [follow_up(100)], as_of=at(26))["a"] == (1, POSITIVE)


def test_only_the_same_encounter_and_outcome_type_count() -> None:
    spine = [prediction("a", 10)]
    elsewhere = [outcome(15, encounter="enc2"), outcome(15, kind="readmission")]
    assert statuses(spine, elsewhere, [follow_up(100)])["a"] == (0, NEGATIVE)


def test_coverage_reports_unknown_labels() -> None:
    spine = [prediction("pos", 10), prediction("neg", 0), prediction("unknown", 95)]
    table = assign_labels(
        spine_to_table(spine),
        outcomes_to_table([outcome(15)]),
        follow_up_to_table([follow_up(100)]),
        SPEC,
    )
    coverage = label_coverage(table)
    assert coverage["predictions"] == 3
    assert coverage["labelled"] == 2
    assert coverage["unknown"] == 1
    assert coverage["coverage"] == pytest.approx(2 / 3)
    assert coverage["by_status"][UNKNOWN_FOLLOW_UP] == 1


def test_label_table_keeps_every_prediction_in_order() -> None:
    spine = [prediction(f"q{index}", index) for index in (5, 1, 9)]
    table = assign_labels(
        spine_to_table(spine), outcomes_to_table([]), follow_up_to_table([follow_up(100)]), SPEC
    )
    assert table.column("prediction_id").to_pylist() == ["q5", "q1", "q9"]
    assert isinstance(table, pa.Table)


def test_impatient_labelling_records_late_ascertained_events_as_negatives() -> None:
    from asoflab.evaluation.labels import count_premature_negatives, labels_recorded_at_horizon

    spine = spine_to_table(
        [
            prediction("known_in_time", 10),  # event at 15, known at 17, horizon ends at 22
            prediction("known_too_late", 10, encounter="enc2"),  # event at 21, known at 26
            prediction("no_event", 10, encounter="enc3"),
            prediction("cut_short", 95, encounter="enc3"),  # follow-up ends at 100, horizon at 107
        ]
    )
    outcomes = outcomes_to_table(
        [outcome(15, known_after=2), outcome(21, known_after=5, encounter="enc2")]
    )
    follow = follow_up_to_table([follow_up(100), follow_up(100, "enc2"), follow_up(100, "enc3")])
    recorded = labels_recorded_at_horizon(spine, outcomes, follow, SPEC)
    assert recorded == [1, 0, 0, None]
    final = assign_labels(spine, outcomes, follow, SPEC).column("label").to_pylist()
    assert final == [1, 1, 0, None], (
        "the mature label of record is positive for the late-ascertained event"
    )
    assert count_premature_negatives(spine, outcomes, SPEC) == 1
