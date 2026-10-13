"""Every handwritten fixture, against every engine, compared with answers worked by hand."""

import pytest

from asoflab.errors import ContractViolation
from asoflab.fixtures import FixtureCase, load_cases
from asoflab.schema.models import TimePolicy
from asoflab.schema.tables import events_to_table, spine_to_table
from asoflab.schema.validation import validate_events, validate_inputs
from support import ENGINES, differences_from_expected

CASES = load_cases()
VALID = [case for case in CASES if case.expected_error is None]
CHECKS = [
    pytest.param(case, slug, engine, id=f"{case.name}:{slug}:{engine}")
    for case in VALID
    for slug in case.expected
    for engine in ENGINES
]


def test_the_corpus_covers_the_named_failure_modes() -> None:
    names = {case.name for case in CASES}
    assert len(names) == len(CASES)
    assert {
        "delayed_result",
        "correction_keeps_earlier_version",
        "retraction_known_later",
        "late_older_revision",
        "future_append_invariance",
        "missing_features_preserve_spine",
        "exact_duplicate_idempotent",
        "conflicting_duplicate_rejected",
        "utc_equivalence",
        "window_and_tie_boundaries",
        "revision_changes_event_time",
    } <= names
    for case in VALID:
        assert "pipeline_as_known" in case.expected or "source_as_known" in case.expected
        assert "naive_event_time" in case.expected, case.name


@pytest.mark.parametrize(("case", "slug", "engine"), CHECKS)
def test_engine_matches_hand_calculation(case: FixtureCase, slug: str, engine: str) -> None:
    table = ENGINES[engine](case.events, case.spine, case.config, TimePolicy.parse(slug))
    assert table.num_rows == len(case.spine), "one output row per spine row"
    problems = differences_from_expected(table, case.expected[slug])
    assert not problems, f"{case.name} under {slug} with {engine}: " + "; ".join(problems)


@pytest.mark.parametrize("case", [c for c in CASES if c.expected_error], ids=lambda c: c.name)
def test_contradictory_input_is_refused_with_an_actionable_error(case: FixtureCase) -> None:
    assert case.expected_error is not None
    with pytest.raises(ContractViolation) as caught:
        validate_inputs(events_to_table(case.events), spine_to_table(case.spine))
    error = caught.value
    assert error.code == case.expected_error["code"]
    assert error.examples, "the error must point at the offending records"
    assert error.remediation, "the error must say what to do"
    if "differing_fields" in case.expected_error:
        assert error.examples[0]["differing_fields"] == case.expected_error["differing_fields"]


@pytest.mark.parametrize("case", [c for c in VALID if c.expected_warnings], ids=lambda c: c.name)
def test_expected_warnings_are_counted(case: FixtureCase) -> None:
    report = validate_events(events_to_table(case.events), TimePolicy.parse("pipeline_as_known"))
    counts = {finding.code: finding.count for finding in report.warnings}
    for code, count in case.expected_warnings.items():
        assert counts.get(code) == count, f"{case.name}: {code}"


def test_the_headline_example_differs_between_naive_and_as_known() -> None:
    """The README example: the fixture must actually show the leak it describes."""
    case = next(c for c in CASES if c.name == "delayed_result")
    assert case.expected["pipeline_as_known"]["A"]["lac_last_6h"] is None
    assert case.expected["naive_event_time"]["A"]["lac_last_6h"] == 4.2
