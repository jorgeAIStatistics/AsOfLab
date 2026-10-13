"""Differential audit: where a candidate policy reads something the reference could not have.

The reference is an as-known policy. The candidate is usually the naive one.
For every prediction, the observations each policy reads are compared, and
each difference becomes a finding that names the prediction, the observation,
what was eligible according to the reference, what the candidate actually
used, how serious that is, and what to change.

This audit explains differences in *timing and revisions*. It cannot say
whether a measurement is clinically appropriate, causal, or correct.
"""

from dataclasses import dataclass
from typing import Any

import pyarrow as pa

from asoflab.batch.duckdb_backend import (
    contributions_query,
    in_force_query,
    known_us_expression,
    register_sources,
)
from asoflab.duck import connect
from asoflab.schema.models import FeatureConfig, TimePolicy
from asoflab.schema.tables import TableSource
from asoflab.schema.time import from_us

FINDING_TYPES: dict[str, dict[str, str]] = {
    "used_before_available": {
        "severity": "high",
        "meaning": "The candidate used an observation that no version of which was available yet.",
        "remediation": "Require the availability time on the replayed clock to be at or before "
        "the prediction time.",
    },
    "used_with_unknown_availability": {
        "severity": "high",
        "meaning": "The candidate used an observation whose availability time is unknown.",
        "remediation": "Recover the availability time, or exclude the observation and report the count.",
    },
    "value_revised_later": {
        "severity": "medium",
        "meaning": "Both used the observation, but the candidate read a revision that was not "
        "yet known at the prediction time.",
        "remediation": "Read the version in force at the prediction time, not today's latest version.",
    },
    "used_after_later_revision": {
        "severity": "medium",
        "meaning": "At the prediction time the observation was withdrawn or dated outside the "
        "window; the candidate read a later revision that brought it in.",
        "remediation": "Resolve revisions as known at the prediction time before applying the window.",
    },
    "missed_later_retracted": {
        "severity": "medium",
        "meaning": "The observation was in force at the prediction time and was withdrawn "
        "afterwards. The candidate omits it, so it does not reproduce what was used.",
        "remediation": "Apply a retraction only from the moment the retraction was known.",
    },
    "missed_event_time_moved": {
        "severity": "medium",
        "meaning": "The observation was inside the window as known at the prediction time; a "
        "later revision moved its event time, so the candidate omits it.",
        "remediation": "Resolve revisions as known at the prediction time before applying the window.",
    },
}

_CLASSIFY = """
SELECT coalesce(r.prediction_id, c.prediction_id) AS prediction_id,
       coalesce(r.entity_id, c.entity_id) AS entity_id,
       coalesce(r.cutoff_us, c.cutoff_us) AS cutoff_us,
       coalesce(r.logical_event_id, c.logical_event_id) AS logical_event_id,
       coalesce(r.event_type, c.event_type) AS event_type,
       CASE
         WHEN r.logical_event_id IS NOT NULL AND c.logical_event_id IS NOT NULL THEN 'value_revised_later'
         WHEN r.logical_event_id IS NULL AND k.first_known_us IS NULL THEN 'used_with_unknown_availability'
         WHEN r.logical_event_id IS NULL AND f.logical_event_id IS NULL THEN 'used_before_available'
         WHEN r.logical_event_id IS NULL THEN 'used_after_later_revision'
         WHEN t.operation = 'retract' THEN 'missed_later_retracted'
         ELSE 'missed_event_time_moved'
       END AS finding_type,
       r.logical_event_id IS NOT NULL AS expected_used,
       coalesce(r.source_revision, f.source_revision) AS expected_revision,
       coalesce(r.event_us, f.event_us) AS expected_event_us,
       coalesce(r.value, f.value) AS expected_value,
       f.operation AS expected_operation,
       k.first_known_us AS first_known_us,
       c.logical_event_id IS NOT NULL AS actual_used,
       coalesce(c.source_revision, t.source_revision) AS actual_revision,
       coalesce(c.event_us, t.event_us) AS actual_event_us,
       coalesce(c.value, t.value) AS actual_value
FROM reference r
FULL OUTER JOIN candidate c
  ON r.prediction_id = c.prediction_id AND r.logical_event_id = c.logical_event_id
LEFT JOIN first_known k
  ON k.logical_event_id = coalesce(r.logical_event_id, c.logical_event_id)
LEFT JOIN in_force f
  ON f.logical_event_id = coalesce(r.logical_event_id, c.logical_event_id)
 AND f.valid_from_us <= coalesce(r.cutoff_us, c.cutoff_us)
 AND coalesce(r.cutoff_us, c.cutoff_us) < f.valid_to_us
LEFT JOIN todays t
  ON t.logical_event_id = coalesce(r.logical_event_id, c.logical_event_id)
WHERE r.logical_event_id IS NULL OR c.logical_event_id IS NULL
   OR r.source_revision <> c.source_revision
"""


@dataclass
class DifferentialAudit:
    reference: str
    candidate: str
    predictions: int
    predictions_with_findings: int
    findings_total: int
    by_type: dict[str, int]
    by_event_type: dict[str, dict[str, int]]
    examples: list[dict[str, Any]]
    findings: pa.Table

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_policy": self.reference,
            "candidate_policy": self.candidate,
            "scope": "Timing and revision differences only. Not a test of clinical "
            "appropriateness, causality, or data validity.",
            "predictions": self.predictions,
            "predictions_with_findings": self.predictions_with_findings,
            "findings_total": self.findings_total,
            "findings_shown": len(self.examples),
            "by_type": {
                name: {"count": self.by_type.get(name, 0), **FINDING_TYPES[name]}
                for name in FINDING_TYPES
            },
            "by_event_type": self.by_event_type,
            "examples": self.examples,
        }


def differential_audit(
    events: TableSource,
    spine: TableSource,
    config: FeatureConfig,
    reference: TimePolicy,
    candidate: TimePolicy,
    *,
    max_examples: int = 200,
) -> DifferentialAudit:
    """Compare what `candidate` reads with what `reference` (an as-known policy) reads."""
    if reference.clock is None or not reference.name.value.endswith("as_known"):
        raise ValueError("the reference policy must be an as-known policy")
    connection = connect()
    register_sources(connection, events, spine)
    types = ", ".join("'" + t.replace("'", "''") + "'" for t in config.event_types())
    known = known_us_expression(reference)
    connection.execute(f"CREATE TEMP TABLE reference AS {contributions_query(config, reference)}")
    connection.execute(f"CREATE TEMP TABLE candidate AS {contributions_query(config, candidate)}")
    connection.execute(f"CREATE TEMP TABLE in_force AS {in_force_query(config, reference)}")
    connection.execute(
        "CREATE TEMP TABLE todays AS SELECT logical_event_id, source_revision, operation, "
        f"epoch_us(event_time) AS event_us, value FROM events WHERE event_type IN ({types}) "
        "QUALIFY row_number() OVER (PARTITION BY logical_event_id ORDER BY source_revision DESC) = 1"
    )
    connection.execute(
        f"CREATE TEMP TABLE first_known AS SELECT logical_event_id, min({known}) AS first_known_us "
        f"FROM events WHERE event_type IN ({types}) GROUP BY 1"
    )
    connection.execute(f"CREATE TEMP TABLE findings AS {_CLASSIFY}")

    findings = connection.execute(
        "SELECT * FROM findings ORDER BY prediction_id, logical_event_id"
    ).to_arrow_table()
    by_type = dict(
        connection.execute("SELECT finding_type, count(*) FROM findings GROUP BY 1").fetchall()
    )
    by_event_type: dict[str, dict[str, int]] = {}
    for event_type, finding_type, count in connection.execute(
        "SELECT event_type, finding_type, count(*) FROM findings GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall():
        by_event_type.setdefault(event_type, {})[finding_type] = int(count)
    affected = connection.execute("SELECT count(DISTINCT prediction_id) FROM findings").fetchone()
    total = connection.execute("SELECT count(*) FROM spine").fetchone()
    assert affected is not None
    assert total is not None
    examples = [_describe(row) for row in findings.slice(0, max_examples).to_pylist()]
    return DifferentialAudit(
        reference=reference.slug(),
        candidate=candidate.slug(),
        predictions=int(total[0]),
        predictions_with_findings=int(affected[0]),
        findings_total=findings.num_rows,
        by_type={name: int(count) for name, count in by_type.items()},
        by_event_type=by_event_type,
        examples=examples,
        findings=findings,
    )


def _time(microseconds: int | None) -> str | None:
    return None if microseconds is None else from_us(microseconds).isoformat()


def _describe(row: dict[str, Any]) -> dict[str, Any]:
    kind = FINDING_TYPES[row["finding_type"]]
    return {
        "finding_id": f"{row['prediction_id']}|{row['logical_event_id']}",
        "type": row["finding_type"],
        "severity": kind["severity"],
        "prediction_id": row["prediction_id"],
        "entity_id": row["entity_id"],
        "prediction_time": _time(row["cutoff_us"]),
        "logical_event_id": row["logical_event_id"],
        "event_type": row["event_type"],
        "expected": {
            "eligible": bool(row["expected_used"]),
            "source_revision": row["expected_revision"],
            "operation": row["expected_operation"],
            "event_time": _time(row["expected_event_us"]),
            "value": row["expected_value"],
            "first_available": _time(row["first_known_us"]),
        },
        "actual": {
            "used": bool(row["actual_used"]),
            "source_revision": row["actual_revision"],
            "event_time": _time(row["actual_event_us"]),
            "value": row["actual_value"],
        },
        "meaning": kind["meaning"],
        "remediation": kind["remediation"],
    }
