"""Set-wise validation of event and spine tables. Section 9 of the temporal contract.

Row-level rules are also enforced by the Pydantic models. This module applies
the same rules to whole tables in SQL so that large archives can be checked,
and adds the rules that need more than one row (duplicates, revision order).

Validation only reports. It never repairs, fills in, or drops anything.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

import duckdb
import pyarrow as pa

from asoflab.contract import RESERVED_EVENT_TYPE_PREFIX, Clock
from asoflab.duck import connect
from asoflab.errors import ContractViolation
from asoflab.schema.models import TimePolicy
from asoflab.schema.tables import EVENT_SCHEMA, PAYLOAD_COLUMNS, SPINE_SCHEMA, conform

Severity = Literal["error", "warning"]
_MAX_EXAMPLES = 5
_VERSION_KEYS = "entity_id, logical_event_id, version_id, source_revision"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    message: str
    count: int
    examples: list[dict[str, Any]] = field(default_factory=list)
    remediation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "count": self.count,
            "examples": self.examples,
            "remediation": self.remediation,
        }


@dataclass
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def errors(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if self.errors:
            first = self.errors[0]
            raise ContractViolation(
                first.code, first.message, examples=first.examples, remediation=first.remediation
            )

    def merge(self, other: "ValidationReport") -> "ValidationReport":
        return ValidationReport(self.findings + other.findings, {**self.counts, **other.counts})

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "counts": self.counts,
            "findings": [finding.to_dict() for finding in self.findings],
        }


# Row-level rules: (code, severity, SQL predicate that is true for a bad row, message, remediation)
_ROW_RULES: list[tuple[str, Severity, str, str, str | None]] = [
    (
        "E_SCHEMA",
        "error",
        "entity_id IS NULL OR entity_id = '' OR logical_event_id IS NULL OR logical_event_id = '' "
        "OR version_id IS NULL OR version_id = '' OR event_type IS NULL OR event_type = '' "
        "OR source IS NULL OR source = '' OR schema_version IS NULL OR schema_version = '' "
        "OR source_revision IS NULL OR source_revision < 1 "
        "OR operation IS NULL OR operation NOT IN ('upsert', 'retract') "
        "OR source_availability_provenance IS NULL "
        "OR source_availability_provenance NOT IN ('observed', 'assumed', 'unknown') "
        "OR pipeline_availability_provenance IS NULL "
        "OR pipeline_availability_provenance NOT IN ('observed', 'assumed', 'unknown')",
        "Rows have a missing identifier, a revision below 1, or a value outside the allowed set.",
        None,
    ),
    (
        "E_UPSERT_INCOMPLETE",
        "error",
        "(operation = 'upsert' AND (event_time IS NULL OR value IS NULL)) "
        "OR (value IS NOT NULL AND NOT isfinite(value))",
        "Upserts need an event_time and a finite value.",
        None,
    ),
    (
        "E_PROVENANCE_MISMATCH",
        "error",
        "(source_available_time IS NULL) <> (source_availability_provenance = 'unknown') "
        "OR (pipeline_available_time IS NULL) <> (pipeline_availability_provenance = 'unknown')",
        "Availability provenance must be 'unknown' exactly when the availability time is missing.",
        "Do not fill in a time you do not have; mark it unknown.",
    ),
    (
        "E_RESERVED_TYPE",
        "error",
        f"starts_with(event_type, '{RESERVED_EVENT_TYPE_PREFIX}')",
        "The event stream contains event types with the reserved outcome prefix.",
        "Keep outcomes in the outcome tables, not in the event stream.",
    ),
    (
        "W_AVAIL_BEFORE_EVENT",
        "warning",
        "source_available_time < event_time OR pipeline_available_time < event_time",
        "Versions are marked available before their event happened.",
        "Check for clock differences between systems. Nothing was changed.",
    ),
    (
        "W_PIPELINE_BEFORE_SOURCE",
        "warning",
        "pipeline_available_time < source_available_time",
        "The pipeline is recorded as having versions before their source did.",
        "Check how each availability time was derived. Nothing was changed.",
    ),
]


def validate_events(table: pa.Table, policy: TimePolicy | None = None) -> ValidationReport:
    """Check an event table against the contract. Pass a policy to add its quarantine counts."""
    events = conform(table, EVENT_SCHEMA, "events")
    connection = connect()
    connection.register("events", events)
    report = ValidationReport(counts={"event_versions": events.num_rows})

    _row_rules(connection, report)
    _duplicate_rules(connection, report)
    _grouped_rule(
        connection,
        report,
        "E_REV_AMBIGUOUS",
        "logical_event_id, source_revision",
        "count(DISTINCT version_id) > 1",
        "Different versions of one logical event share a source revision, so their order "
        "is undefined.",
        "Give each version of a logical event its own source_revision.",
    )
    _grouped_rule(
        connection,
        report,
        "E_ENTITY_CHANGED",
        "logical_event_id",
        "count(DISTINCT entity_id) > 1",
        "A logical event is attributed to more than one entity.",
        "Retract the observation and record a new logical event for the other entity.",
    )
    _grouped_rule(
        connection,
        report,
        "E_TYPE_CHANGED",
        "logical_event_id",
        "count(DISTINCT event_type) > 1",
        "A logical event changes event type between versions.",
        "Retract the observation and record a new logical event of the other type.",
    )
    _unit_rule(connection, report)
    report.counts["logical_events"] = _scalar(
        connection, "SELECT count(DISTINCT logical_event_id) FROM events"
    )
    report.counts["entities"] = _scalar(connection, "SELECT count(DISTINCT entity_id) FROM events")
    if policy is not None and policy.clock is not None:
        _availability_rules(connection, report, policy)
    return report


def validate_spine(table: pa.Table) -> ValidationReport:
    spine = conform(table, SPINE_SCHEMA, "spine")
    connection = connect()
    connection.register("spine", spine)
    report = ValidationReport(counts={"predictions": spine.num_rows})
    incomplete = _scalar(
        connection,
        "SELECT count(*) FROM spine WHERE prediction_id IS NULL OR prediction_id = '' "
        "OR entity_id IS NULL OR entity_id = '' OR prediction_time IS NULL",
    )
    if incomplete:
        report.findings.append(
            Finding(
                "E_SCHEMA",
                "error",
                "Spine rows are missing a prediction_id, entity_id, or prediction_time.",
                incomplete,
            )
        )
    repeated = connection.execute(
        "SELECT prediction_id, count(*) AS n FROM spine GROUP BY 1 HAVING count(*) > 1 "
        f"ORDER BY 1 LIMIT {_MAX_EXAMPLES}"
    ).fetchall()
    if repeated:
        total = _scalar(
            connection,
            "SELECT count(*) FROM (SELECT 1 FROM spine GROUP BY prediction_id HAVING count(*) > 1)",
        )
        report.findings.append(
            Finding(
                "E_SPINE_DUPLICATE",
                "error",
                "Prediction identifiers are repeated in the spine.",
                total,
                [{"prediction_id": row[0], "rows": row[1]} for row in repeated],
                "Each prediction needs its own prediction_id.",
            )
        )
    return report


def validate_inputs(
    events: pa.Table, spine: pa.Table, policy: TimePolicy | None = None
) -> ValidationReport:
    """Validate both tables, raising ContractViolation on the first error."""
    report = validate_events(events, policy).merge(validate_spine(spine))
    report.raise_for_errors()
    return report


def _scalar(connection: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = connection.execute(sql).fetchone()
    return int(row[0] or 0) if row else 0


def _examples(
    connection: duckdb.DuckDBPyConnection, select: str, columns: list[str]
) -> list[dict[str, Any]]:
    rows = connection.execute(f"{select} LIMIT {_MAX_EXAMPLES}").fetchall()
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _row_rules(connection: duckdb.DuckDBPyConnection, report: ValidationReport) -> None:
    counters = ", ".join(
        f"count(*) FILTER (WHERE {predicate})" for _, _, predicate, _, _ in _ROW_RULES
    )
    counts = connection.execute(f"SELECT {counters} FROM events").fetchone() or ()
    keys = [key.strip() for key in _VERSION_KEYS.split(",")]
    for (code, severity, predicate, message, remediation), count in zip(
        _ROW_RULES, counts, strict=True
    ):
        if count:
            examples = _examples(
                connection,
                f"SELECT {_VERSION_KEYS} FROM events WHERE {predicate} ORDER BY {_VERSION_KEYS}",
                keys,
            )
            report.findings.append(
                Finding(code, severity, message, int(count), examples, remediation)
            )


def _duplicate_rules(connection: duckdb.DuckDBPyConnection, report: ValidationReport) -> None:
    payload = "struct_pack(" + ", ".join(f"{name} := {name}" for name in PAYLOAD_COLUMNS) + ")"
    connection.execute(
        f"CREATE TEMP VIEW identities AS SELECT logical_event_id, version_id, count(*) AS n_rows, "
        f"count(DISTINCT {payload}) AS n_payloads FROM events GROUP BY 1, 2"
    )
    conflicts = _scalar(connection, "SELECT count(*) FROM identities WHERE n_payloads > 1")
    if conflicts:
        examples = []
        identities = connection.execute(
            "SELECT logical_event_id, version_id FROM identities WHERE n_payloads > 1 "
            f"ORDER BY 1, 2 LIMIT {_MAX_EXAMPLES}"
        ).fetchall()
        for logical_event_id, version_id in identities:
            rows = (
                connection.execute(
                    f"SELECT DISTINCT {', '.join(PAYLOAD_COLUMNS)} FROM events "
                    "WHERE logical_event_id = ? AND version_id = ?",
                    [logical_event_id, version_id],
                )
                .to_arrow_table()
                .to_pylist()
            )
            differing = sorted(
                name for name in PAYLOAD_COLUMNS if len({repr(row[name]) for row in rows}) > 1
            )
            examples.append(
                {
                    "logical_event_id": logical_event_id,
                    "version_id": version_id,
                    "differing_fields": differing,
                }
            )
        report.findings.append(
            Finding(
                "E_DUP_CONFLICT",
                "error",
                "The same version identity appears with different content.",
                conflicts,
                examples,
                "Decide which record is right, or give the later one a new version_id and "
                "a higher source_revision.",
            )
        )
    redundant = _scalar(connection, "SELECT sum(n_rows - n_payloads) FROM identities")
    report.counts["exact_duplicate_rows"] = redundant
    if redundant:
        report.findings.append(
            Finding(
                "W_EXACT_DUPLICATES",
                "warning",
                "Identical rows are present. They have no effect on any output.",
                redundant,
            )
        )


def _grouped_rule(
    connection: duckdb.DuckDBPyConnection,
    report: ValidationReport,
    code: str,
    group_by: str,
    having: str,
    message: str,
    remediation: str,
) -> None:
    base = f"SELECT {group_by} FROM events GROUP BY {group_by} HAVING {having}"
    count = _scalar(connection, f"SELECT count(*) FROM ({base})")
    if count:
        columns = [column.strip() for column in group_by.split(",")]
        examples = _examples(connection, f"{base} ORDER BY {group_by}", columns)
        report.findings.append(Finding(code, "error", message, count, examples, remediation))


def _unit_rule(connection: duckdb.DuckDBPyConnection, report: ValidationReport) -> None:
    base = (
        "SELECT event_type, list_sort(list(DISTINCT unit)) AS units FROM events "
        "WHERE operation = 'upsert' AND unit IS NOT NULL GROUP BY 1 HAVING count(DISTINCT unit) > 1"
    )
    count = _scalar(connection, f"SELECT count(*) FROM ({base})")
    if count:
        examples = _examples(connection, f"{base} ORDER BY 1", ["event_type", "units"])
        report.findings.append(
            Finding(
                "W_MIXED_UNITS",
                "warning",
                "An event type carries more than one unit. Values are aggregated as given.",
                count,
                examples,
                "Convert to one unit before loading.",
            )
        )


def _availability_rules(
    connection: duckdb.DuckDBPyConnection, report: ValidationReport, policy: TimePolicy
) -> None:
    prefix = "source" if policy.clock is Clock.SOURCE else "pipeline"
    known = f"{prefix}_available_time"
    unknown = f"{known} IS NULL"
    if policy.imputes:
        unknown = f"({known} IS NULL AND event_time IS NULL)"
        imputed = _scalar(
            connection,
            f"SELECT count(*) FROM events WHERE {known} IS NULL AND event_time IS NOT NULL",
        )
        report.counts["versions_with_imputed_availability"] = imputed
    quarantined = _scalar(connection, f"SELECT count(*) FROM events WHERE {unknown}")
    report.counts["versions_quarantined_unknown_availability"] = quarantined
    if quarantined:
        examples = _examples(
            connection,
            f"SELECT {_VERSION_KEYS} FROM events WHERE {unknown} ORDER BY {_VERSION_KEYS}",
            [key.strip() for key in _VERSION_KEYS.split(",")],
        )
        report.findings.append(
            Finding(
                "W_QUARANTINED_UNKNOWN",
                "warning",
                f"Versions with no {prefix} availability time were excluded from every cutoff.",
                quarantined,
                examples,
                "Recover the availability time, or run an explicitly labelled sensitivity policy.",
            )
        )
    outranked = (
        "SELECT logical_event_id FROM events GROUP BY 1 "
        f"HAVING max(source_revision) FILTER (WHERE {unknown}) "
        f"> min(source_revision) FILTER (WHERE NOT {unknown})"
    )
    stale = _scalar(connection, f"SELECT count(*) FROM ({outranked})")
    report.counts["logical_events_with_quarantined_later_revision"] = stale
    if stale:
        report.findings.append(
            Finding(
                "W_LATER_REVISION_QUARANTINED",
                "warning",
                "A quarantined version outranks one that stays active, so a correction or "
                "retraction is never applied for these observations.",
                stale,
                _examples(connection, f"{outranked} ORDER BY 1", ["logical_event_id"]),
                "Treat results for these observations as uncertain.",
            )
        )
    assumed = _scalar(
        connection,
        f"SELECT count(*) FROM events WHERE {prefix}_availability_provenance = 'assumed'",
    )
    report.counts["versions_with_assumed_availability"] = assumed
    if assumed:
        report.findings.append(
            Finding(
                "W_ASSUMED_PROVENANCE",
                "warning",
                f"Eligibility relies on assumed {prefix} availability times.",
                assumed,
                remediation="State the assumption wherever results from this run are reported.",
            )
        )
