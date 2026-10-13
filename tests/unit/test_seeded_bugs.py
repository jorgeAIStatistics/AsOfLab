"""The handwritten fixtures must expose every seeded bug.

A test suite that passes proves little unless it can also fail. Each seeded bug
is one central timestamp or revision mistake; this module checks that at least
one fixture's hand-calculated answer contradicts it.
"""

import pytest

from asoflab.fixtures import load_cases
from asoflab.schema.models import TimePolicy
from asoflab.schema.tables import feature_rows_to_table
from seeded_bugs import CONTROL, SEEDED_BUGS
from support import differences_from_expected

AS_KNOWN = ["pipeline_as_known", "source_as_known"]
VALID = [case for case in load_cases() if case.expected_error is None]


def killers(compute) -> list[str]:  # type: ignore[no-untyped-def]
    """Names of the fixtures whose hand-calculated answers this implementation contradicts."""
    exposed = []
    for case in VALID:
        for slug in AS_KNOWN:
            if slug not in case.expected:
                continue
            rows = compute(case.events, case.spine, case.config, TimePolicy.parse(slug))
            table = feature_rows_to_table(rows, case.config)
            if differences_from_expected(table, case.expected[slug]):
                exposed.append(case.name)
                break
    return exposed


def kill_matrix() -> dict[str, list[str]]:
    return {name: killers(compute) for name, compute in SEEDED_BUGS.items()}


def test_the_harness_itself_is_correct() -> None:
    assert killers(CONTROL) == [], "the control implementation must agree with every fixture"


@pytest.mark.parametrize("name", sorted(SEEDED_BUGS))
def test_seeded_bug_is_caught_by_a_fixture(name: str) -> None:
    exposed = killers(SEEDED_BUGS[name])
    assert exposed, f"no fixture exposes the seeded bug {name!r}"


def test_headline_bugs_are_caught_by_the_fixture_named_for_them() -> None:
    matrix = kill_matrix()
    assert "delayed_result" in matrix["ignore_availability"]
    assert "correction_keeps_earlier_version" in matrix["latest_then_filter"]
    assert "correction_keeps_earlier_version" in matrix["first_seen_gate_latest_value"]
    assert "revision_changes_event_time" in matrix["window_before_resolve"]
    assert "retraction_known_later" in matrix["ignore_retractions"]
    assert "retraction_with_payload" in matrix["retraction_payload_read"]
    assert "late_older_revision" in matrix["arrival_order_resolution"]
    assert "exact_duplicate_idempotent" in matrix["count_every_version"]
    assert "window_and_tie_boundaries" in matrix["exclusive_availability"]
    assert "window_and_tie_boundaries" in matrix["inclusive_window_start"]
    assert "window_and_tie_boundaries" in matrix["exclusive_window_end"]
    assert "entity_isolation" in matrix["cross_entity_read"]
    assert "missing_features_preserve_spine" in matrix["drop_empty_predictions"]
    assert "last_tie_break" in matrix["last_tie_lowest_id"]
