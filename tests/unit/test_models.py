"""Row-level contract rules, and agreement between the row models and the table validator."""

from typing import Any

import pyarrow as pa
import pytest

from asoflab.contract import Strictness
from asoflab.errors import ContractViolation
from asoflab.schema.models import EventVersion, FeatureConfig, FeatureSpec, TimePolicy
from asoflab.schema.tables import EVENT_SCHEMA, conform, events_to_table
from asoflab.schema.validation import validate_events

GOOD: dict[str, Any] = {
    "entity_id": "p1",
    "logical_event_id": "e1",
    "version_id": "v1",
    "source_revision": 1,
    "operation": "upsert",
    "event_type": "lab.lactate",
    "event_time": "2025-03-01T08:00:00Z",
    "value": 2.1,
    "unit": "mmol/L",
    "source": "lis",
    "schema_version": "1",
    "source_available_time": "2025-03-01T08:30:00Z",
    "source_availability_provenance": "observed",
    "pipeline_available_time": "2025-03-01T08:45:00Z",
    "pipeline_availability_provenance": "observed",
}

# Each entry breaks exactly one row-level rule.
BAD_ROWS: list[tuple[str, dict[str, Any], str]] = [
    ("upsert without value", {"value": None}, "E_UPSERT_INCOMPLETE"),
    ("upsert without event_time", {"event_time": None}, "E_UPSERT_INCOMPLETE"),
    ("non-finite value", {"value": float("inf")}, "E_UPSERT_INCOMPLETE"),
    ("NaN value", {"value": float("nan")}, "E_UPSERT_INCOMPLETE"),
    (
        "time present but provenance unknown",
        {"pipeline_availability_provenance": "unknown"},
        "E_PROVENANCE_MISMATCH",
    ),
    (
        "time missing but provenance observed",
        {"source_available_time": None},
        "E_PROVENANCE_MISMATCH",
    ),
    ("reserved outcome type", {"event_type": "outcome.death"}, "E_RESERVED_TYPE"),
]


def test_a_complete_row_is_accepted() -> None:
    event = EventVersion(**GOOD)
    assert validate_events(events_to_table([event])).ok


@pytest.mark.parametrize(("why", "change", "code"), BAD_ROWS, ids=[row[0] for row in BAD_ROWS])
def test_row_model_and_table_validator_refuse_the_same_rows(
    why: str, change: dict[str, Any], code: str
) -> None:
    row = {**GOOD, **change}
    with pytest.raises(ContractViolation) as caught:
        EventVersion(**row)
    assert caught.value.code == code, why

    # Build the same row as a table directly, bypassing the row model.
    as_table = pa.table({name: [row.get(name)] for name in EVENT_SCHEMA.names})
    as_table = as_table.set_column(
        as_table.schema.get_field_index("event_time"),
        "event_time",
        pa.array(
            [EventVersion(**GOOD).event_time if row["event_time"] else None],
            EVENT_SCHEMA.field("event_time").type,
        ),
    )
    for name in ("source_available_time", "pipeline_available_time", "ingestion_time"):
        moment = getattr(EventVersion(**GOOD), name) if row.get(name) else None
        as_table = as_table.set_column(
            as_table.schema.get_field_index(name),
            name,
            pa.array([moment], EVENT_SCHEMA.field(name).type),
        )
    report = validate_events(conform(as_table, EVENT_SCHEMA, "events"))
    assert code in [finding.code for finding in report.errors], why


def test_a_retraction_needs_no_event_time_or_value() -> None:
    retraction = EventVersion(**{**GOOD, "operation": "retract", "event_time": None, "value": None})
    assert validate_events(events_to_table([retraction])).ok


def test_unknown_fields_are_refused() -> None:
    with pytest.raises(ValueError, match="extra"):
        EventVersion(**{**GOOD, "note": "free text"})


@pytest.mark.parametrize(
    ("slug", "strictness", "faithful"),
    [
        ("pipeline_as_known", Strictness.STRICT, True),
        ("source_as_known", Strictness.STRICT, True),
        ("pipeline_as_known-impute0s", Strictness.SENSITIVITY, False),
        ("source_as_known-impute3600s", Strictness.SENSITIVITY, False),
        ("latest_revised", Strictness.RETROSPECTIVE, False),
        ("latest_revised-source", Strictness.RETROSPECTIVE, False),
        ("naive_event_time", Strictness.DIAGNOSTIC, False),
    ],
)
def test_only_unimputed_as_known_policies_are_strict(
    slug: str, strictness: Strictness, faithful: bool
) -> None:
    policy = TimePolicy.parse(slug)
    assert policy.strictness is strictness
    assert policy.faithful_replay is faithful
    assert TimePolicy.parse(policy.slug()) == policy


@pytest.mark.parametrize(
    "slug",
    ["as_known", "naive_event_time-impute0s", "pipeline_as_known-source", "latest_revised-x"],
)
def test_meaningless_policies_are_refused(slug: str) -> None:
    with pytest.raises(ContractViolation) as caught:
        TimePolicy.parse(slug)
    assert caught.value.code == "E_POLICY"


def test_cross_entity_features_are_rejected() -> None:
    with pytest.raises(ContractViolation) as caught:
        FeatureSpec(
            name="ward_mean",
            event_type="lab.lactate",
            aggregation="mean",
            window="6h",
            scope="ward",
        )
    assert caught.value.code == "E_UNSUPPORTED_SCOPE"


@pytest.mark.parametrize(
    "features",
    [
        [],
        [{"name": "Bad Name", "event_type": "x", "aggregation": "last", "window": "1h"}],
        [{"name": "prediction_id", "event_type": "x", "aggregation": "last", "window": "1h"}],
        [{"name": "a", "event_type": "x", "aggregation": "last", "window": "0h"}],
        [
            {
                "name": "a",
                "event_type": "x",
                "aggregation": "last",
                "window": "1h",
                "window_seconds": 5,
            }
        ],
        [
            {"name": "a", "event_type": "x", "aggregation": "last", "window": "1h"},
            {"name": "a", "event_type": "y", "aggregation": "mean", "window": "2h"},
        ],
    ],
)
def test_invalid_feature_sets_are_refused(features: list[dict[str, Any]]) -> None:
    with pytest.raises(ContractViolation) as caught:
        FeatureConfig(features=features)
    assert caught.value.code == "E_FEATURE_CONFIG"


def test_feature_hash_is_the_definition_not_the_spelling() -> None:
    one = FeatureConfig(
        features=[{"name": "a", "event_type": "x", "aggregation": "last", "window": "1d"}]
    )
    same = FeatureConfig(
        features=[{"name": "a", "event_type": "x", "aggregation": "last", "window_seconds": 86400}]
    )
    other = FeatureConfig(
        features=[{"name": "a", "event_type": "x", "aggregation": "last", "window": "23h"}]
    )
    assert one.config_hash() == same.config_hash()
    assert one.config_hash() != other.config_hash()
