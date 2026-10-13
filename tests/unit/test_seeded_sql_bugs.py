"""The same idea as test_seeded_bugs, applied to the batch backend's SQL.

Each mutation is a one-line change to the generated query that reproduces a
real SQL mistake. The handwritten fixtures must contradict every one of them.
"""

import pytest

from asoflab.batch.duckdb_backend import build_query, run_query
from asoflab.fixtures import load_cases
from asoflab.schema.models import TimePolicy
from asoflab.schema.tables import events_to_table, spine_to_table
from support import differences_from_expected

VALID = [case for case in load_cases() if case.expected_error is None]

# name -> (text in the correct SQL, replacement, fixture expected to expose it)
SQL_MUTATIONS: dict[str, tuple[str, str, str]] = {
    "availability_strictly_before_cutoff": (
        "a.valid_from_us <= c.cutoff_us",
        "a.valid_from_us < c.cutoff_us",
        "window_and_tie_boundaries",
    ),
    "superseded_version_survives_takeover_instant": (
        "c.cutoff_us < a.valid_to_us",
        "c.cutoff_us <= a.valid_to_us",
        "correction_keeps_earlier_version",
    ),
    "late_lower_revision_takes_over": (
        "WHERE best_earlier_revision IS NULL OR source_revision > best_earlier_revision",
        "WHERE TRUE",
        "late_older_revision",
    ),
    "window_start_inclusive": (
        "a.event_us > c.cutoff_us - ",
        "a.event_us >= c.cutoff_us - ",
        "window_and_tie_boundaries",
    ),
    "measurement_after_cutoff_allowed": (
        "AND a.event_us <= c.cutoff_us AND",
        "AND",
        "window_and_tie_boundaries",
    ),
    "retraction_rows_read": (
        "FROM takeovers\n    WHERE operation = 'upsert'",
        "FROM takeovers\n    WHERE TRUE",
        "retraction_with_payload",
    ),
    "inner_join_drops_empty_predictions": (
        "LEFT JOIN active a",
        "JOIN active a",
        "missing_features_preserve_spine",
    ),
    "join_ignores_entity": (
        "ON a.entity_id = c.entity_id",
        "ON TRUE",
        "entity_isolation",
    ),
    "availability_ignored": (
        "AND a.valid_from_us <= c.cutoff_us AND c.cutoff_us < a.valid_to_us",
        "AND c.cutoff_us < a.valid_to_us",
        "delayed_result",
    ),
}


def exposing_fixtures(old: str, new: str) -> list[str]:
    exposed = []
    for case in VALID:
        for slug in ("pipeline_as_known", "source_as_known"):
            if slug not in case.expected:
                continue
            correct = build_query(case.config, TimePolicy.parse(slug))
            assert old in correct, "the mutation target must exist in the generated SQL"
            table = run_query(
                events_to_table(case.events),
                spine_to_table(case.spine),
                case.config,
                correct.replace(old, new),
            )
            if differences_from_expected(table, case.expected[slug]):
                exposed.append(case.name)
                break
    return exposed


@pytest.mark.parametrize("name", sorted(SQL_MUTATIONS))
def test_sql_mutation_is_caught_by_a_fixture(name: str) -> None:
    old, new, named_fixture = SQL_MUTATIONS[name]
    exposed = exposing_fixtures(old, new)
    assert named_fixture in exposed, f"{name}: exposed only by {exposed}"
