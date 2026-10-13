"""Rules that need more than one row, and table-level type rules."""

from datetime import UTC, datetime

import pyarrow as pa
import pytest

from asoflab.errors import ContractViolation
from asoflab.fixtures import load_case
from asoflab.schema.models import EventVersion, PredictionRow, TimePolicy
from asoflab.schema.tables import (
    EVENT_SCHEMA,
    EVENT_SORT_KEYS,
    conform,
    content_hash,
    events_to_table,
    spine_to_table,
)
from asoflab.schema.validation import validate_events, validate_inputs, validate_spine


def _codes(report) -> dict[str, int]:  # type: ignore[no-untyped-def]
    return {finding.code: finding.count for finding in report.findings}


def _variant(event: EventVersion, **changes: object) -> EventVersion:
    return EventVersion(**{**event.model_dump(), **changes})


def test_conflicting_duplicate_names_the_differing_field() -> None:
    case = load_case("conflicting_duplicate_rejected")
    report = validate_events(events_to_table(case.events))
    conflict = next(finding for finding in report.errors if finding.code == "E_DUP_CONFLICT")
    assert conflict.examples == [
        {"logical_event_id": "e1", "version_id": "v1", "differing_fields": ["value"]}
    ]
    assert conflict.remediation
    with pytest.raises(ContractViolation) as caught:
        report.raise_for_errors()
    assert "differing_fields" in str(caught.value)


def test_conflict_on_availability_time_is_also_a_conflict() -> None:
    event = load_case("delayed_result").events[0]
    later = _variant(event, pipeline_available_time=datetime(2025, 3, 1, 12, 0, tzinfo=UTC))
    report = validate_events(events_to_table([event, later]))
    conflict = next(finding for finding in report.errors if finding.code == "E_DUP_CONFLICT")
    assert conflict.examples[0]["differing_fields"] == ["pipeline_available_time"]


def test_exact_duplicates_are_reported_not_rejected() -> None:
    case = load_case("exact_duplicate_idempotent")
    report = validate_events(events_to_table(case.events))
    assert report.ok
    assert _codes(report)["W_EXACT_DUPLICATES"] == 2
    assert report.counts["exact_duplicate_rows"] == 2


def test_ambiguous_revision_order_is_rejected() -> None:
    case = load_case("ambiguous_revision_rejected")
    report = validate_events(events_to_table(case.events))
    assert [finding.code for finding in report.errors] == ["E_REV_AMBIGUOUS"]
    assert report.errors[0].examples == [{"logical_event_id": "e1", "source_revision": 1}]


def test_an_observation_cannot_move_between_entities_or_types() -> None:
    event = load_case("delayed_result").events[0]
    moved = _variant(event, version_id="v2", source_revision=2, entity_id="p2")
    retyped = _variant(event, version_id="v3", source_revision=3, event_type="lab.creatinine")
    codes = _codes(validate_events(events_to_table([event, moved, retyped])))
    assert codes["E_ENTITY_CHANGED"] == 1
    assert codes["E_TYPE_CHANGED"] == 1


def test_quarantine_counts_follow_the_policy_clock() -> None:
    case = load_case("unknown_availability_quarantined")
    table = events_to_table(case.events)
    pipeline = validate_events(table, TimePolicy.parse("pipeline_as_known"))
    assert pipeline.counts["versions_quarantined_unknown_availability"] == 1
    assert _codes(pipeline)["W_QUARANTINED_UNKNOWN"] == 1
    source = validate_events(table, TimePolicy.parse("source_as_known"))
    assert source.counts["versions_quarantined_unknown_availability"] == 0
    imputed = validate_events(table, TimePolicy.parse("pipeline_as_known-impute0s"))
    assert imputed.counts["versions_quarantined_unknown_availability"] == 0
    assert imputed.counts["versions_with_imputed_availability"] == 1


def test_a_quarantined_correction_is_flagged() -> None:
    event = load_case("delayed_result").events[0]
    correction = _variant(
        event,
        version_id="v2",
        source_revision=2,
        value=9.0,
        pipeline_available_time=None,
        pipeline_availability_provenance="unknown",
    )
    report = validate_events(
        events_to_table([event, correction]), TimePolicy.parse("pipeline_as_known")
    )
    assert _codes(report)["W_LATER_REVISION_QUARANTINED"] == 1


def test_contradictory_timestamps_are_reported_and_left_alone() -> None:
    case = load_case("window_and_tie_boundaries")
    table = events_to_table(case.events)
    report = validate_events(table)
    assert report.ok
    assert _codes(report)["W_AVAIL_BEFORE_EVENT"] == 1
    assert content_hash(table, EVENT_SORT_KEYS) == content_hash(
        events_to_table(case.events), EVENT_SORT_KEYS
    )


def test_assumed_provenance_and_mixed_units_are_surfaced() -> None:
    event = load_case("delayed_result").events[0]
    assumed = _variant(
        event, logical_event_id="e2", pipeline_availability_provenance="assumed", unit="mg/dL"
    )
    report = validate_events(
        events_to_table([event, assumed]), TimePolicy.parse("pipeline_as_known")
    )
    codes = _codes(report)
    assert codes["W_ASSUMED_PROVENANCE"] == 1
    assert codes["W_MIXED_UNITS"] == 1


def test_duplicate_prediction_ids_are_rejected() -> None:
    row = PredictionRow(prediction_id="A", entity_id="p1", prediction_time="2025-03-01T09:00:00Z")
    report = validate_spine(spine_to_table([row, row]))
    assert [finding.code for finding in report.errors] == ["E_SPINE_DUPLICATE"]
    with pytest.raises(ContractViolation):
        validate_inputs(
            events_to_table(load_case("delayed_result").events), spine_to_table([row, row])
        )


def test_timestamp_columns_without_a_zone_are_refused() -> None:
    table = events_to_table(load_case("delayed_result").events)
    naive = table.set_column(
        table.schema.get_field_index("event_time"),
        "event_time",
        table.column("event_time").cast(pa.timestamp("us")),
    )
    with pytest.raises(ContractViolation) as caught:
        conform(naive, EVENT_SCHEMA, "events")
    assert caught.value.code == "E_TS_NAIVE"


def test_nanosecond_columns_are_accepted_only_when_nothing_is_lost() -> None:
    table = events_to_table(load_case("delayed_result").events)
    index = table.schema.get_field_index("event_time")
    whole = table.set_column(
        index, "event_time", table.column("event_time").cast(pa.timestamp("ns", tz="UTC"))
    )
    assert conform(whole, EVENT_SCHEMA, "events").equals(table)
    nanos = pa.array([whole.column("event_time")[0].value + 1], pa.timestamp("ns", tz="UTC"))
    with pytest.raises(ContractViolation) as caught:
        conform(whole.set_column(index, "event_time", nanos), EVENT_SCHEMA, "events")
    assert caught.value.code == "E_TS_PRECISION"


def test_other_time_zones_and_wider_types_are_normalised() -> None:
    table = events_to_table(load_case("delayed_result").events)
    index = table.schema.get_field_index("event_time")
    tokyo = table.set_column(
        index, "event_time", table.column("event_time").cast(pa.timestamp("ms", tz="Asia/Tokyo"))
    )
    tokyo = tokyo.set_column(0, "entity_id", tokyo.column("entity_id").cast(pa.large_string()))
    assert conform(tokyo, EVENT_SCHEMA, "events").equals(table)


def test_missing_columns_are_named() -> None:
    table = events_to_table(load_case("delayed_result").events).drop_columns(["version_id"])
    with pytest.raises(ContractViolation, match="version_id") as caught:
        conform(table, EVENT_SCHEMA, "events")
    assert caught.value.code == "E_SCHEMA"


def test_content_hash_ignores_row_order_and_detects_any_change() -> None:
    case = load_case("future_append_invariance")
    forward = events_to_table(case.events)
    backward = events_to_table(list(reversed(case.events)))
    assert content_hash(forward, EVENT_SORT_KEYS) == content_hash(backward, EVENT_SORT_KEYS)
    changed = events_to_table([_variant(case.events[0], value=2.0000001), *case.events[1:]])
    assert content_hash(changed, EVENT_SORT_KEYS) != content_hash(forward, EVENT_SORT_KEYS)
    nulled = events_to_table([_variant(case.events[0], unit=None), *case.events[1:]])
    emptied = events_to_table([_variant(case.events[0], unit=""), *case.events[1:]])
    assert content_hash(nulled, EVENT_SORT_KEYS) != content_hash(emptied, EVENT_SORT_KEYS)
