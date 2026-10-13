"""The three engines must give the same answer on any archive, under every policy."""

from typing import Any

from hypothesis import given, settings

from asoflab.audit.parity import ParityReport, compare_feature_tables
from asoflab.oracle.explain import explain_cutoff
from asoflab.schema.models import TimePolicy
from strategies import Archive, archives, policies
from support import ENGINES, save_counterexample


def responsible_events(
    archive: Archive, policy: TimePolicy, report: ParityReport
) -> dict[str, Any]:
    """For the first disagreement: the cutoff, and what the oracle did with each version there."""
    first = report.examples[0]
    prediction = next(row for row in archive.spine if row.prediction_id == first.prediction_id)
    versions = [v for v in archive.events if v.entity_id == prediction.entity_id]
    statuses = explain_cutoff(versions, prediction.prediction_time, archive.config, policy)
    return {
        "prediction_id": prediction.prediction_id,
        "cutoff": prediction.prediction_time.isoformat(),
        "column": first.column,
        "left": first.left,
        "right": first.right,
        "versions_at_cutoff": [
            {
                "logical_event_id": s.version.logical_event_id,
                "source_revision": s.version.source_revision,
                "status": s.status,
            }
            for s in statuses
        ],
    }


def assert_engines_agree(archive: Archive, policy: TimePolicy, name: str) -> None:
    reference = ENGINES["oracle"](archive.events, archive.spine, archive.config, policy)
    assert reference.num_rows == len(archive.spine)
    for engine in ("batch", "replay"):
        other = ENGINES[engine](archive.events, archive.spine, archive.config, policy)
        report = compare_feature_tables(
            reference, other, archive.config, left_name="oracle", right_name=engine
        )
        if not report.equivalent:
            detail = {"engines": ["oracle", engine], **responsible_events(archive, policy, report)}
            path = save_counterexample(
                name, archive.events, archive.spine, archive.config, policy, detail
            )
            raise AssertionError(
                f"{report.describe()} At cutoff {detail['cutoff']} the oracle saw "
                f"{detail['versions_at_cutoff']}. Counterexample saved to {path}."
            )


# scripts/seeded_bug_report.py measures how many archives it takes to expose a seeded bug:
# the slowest needed a few hundred. The example counts here are set well above that.
@settings(max_examples=1000)
@given(archive=archives(), policy=policies)
def test_oracle_batch_and_replay_agree(archive: Archive, policy: TimePolicy) -> None:
    assert_engines_agree(archive, policy, "engines_agree")


@settings(max_examples=300)
@given(archive=archives(max_logical_events=12, max_predictions=12), policy=policies)
def test_engines_agree_on_larger_archives(archive: Archive, policy: TimePolicy) -> None:
    assert_engines_agree(archive, policy, "engines_agree_larger")
