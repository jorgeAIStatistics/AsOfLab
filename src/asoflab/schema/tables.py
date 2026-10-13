"""Arrow table layouts, conversions, Parquet I/O, and content hashing.

Tables are the bulk form of the records in `asoflab.schema.models`. The
column types here are the on-disk contract: timestamps are always
`timestamp[us, tz=UTC]`.
"""

import hashlib
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from pydantic import ValidationError

from asoflab.contract import Aggregation
from asoflab.errors import ContractViolation
from asoflab.schema.models import (
    EventVersion,
    FeatureConfig,
    FeatureRow,
    FollowUp,
    OutcomeEvent,
    PredictionRow,
)

TIMESTAMP = pa.timestamp("us", tz="UTC")

# A table already in memory, or the path of a Parquet file.
type TableSource = pa.Table | str | Path

EVENT_SCHEMA = pa.schema(
    [
        ("entity_id", pa.string()),
        ("encounter_id", pa.string()),
        ("logical_event_id", pa.string()),
        ("version_id", pa.string()),
        ("source_revision", pa.int64()),
        ("operation", pa.string()),
        ("event_type", pa.string()),
        ("event_time", TIMESTAMP),
        ("value", pa.float64()),
        ("unit", pa.string()),
        ("source", pa.string()),
        ("schema_version", pa.string()),
        ("source_available_time", TIMESTAMP),
        ("source_availability_provenance", pa.string()),
        ("pipeline_available_time", TIMESTAMP),
        ("pipeline_availability_provenance", pa.string()),
        ("ingestion_time", TIMESTAMP),
    ]
)

# Everything except ingestion_time: two rows that agree on these are one version.
PAYLOAD_COLUMNS = [name for name in EVENT_SCHEMA.names if name != "ingestion_time"]

SPINE_SCHEMA = pa.schema(
    [
        ("prediction_id", pa.string()),
        ("entity_id", pa.string()),
        ("encounter_id", pa.string()),
        ("prediction_time", TIMESTAMP),
    ]
)

OUTCOME_SCHEMA = pa.schema(
    [
        ("outcome_event_id", pa.string()),
        ("entity_id", pa.string()),
        ("encounter_id", pa.string()),
        ("outcome_type", pa.string()),
        ("event_time", TIMESTAMP),
        ("ascertainment_time", TIMESTAMP),
    ]
)

FOLLOW_UP_SCHEMA = pa.schema(
    [
        ("entity_id", pa.string()),
        ("encounter_id", pa.string()),
        ("follow_up_start", TIMESTAMP),
        ("follow_up_end", TIMESTAMP),
    ]
)

EVENT_SORT_KEYS = ["logical_event_id", "version_id", "source_revision", "ingestion_time"]


def _records_to_table(records: Iterable[Any], schema: pa.Schema) -> pa.Table:
    rows = [record.model_dump() for record in records]
    columns = {name: [row[name] for row in rows] for name in schema.names}
    return pa.table(columns, schema=schema)


def events_to_table(events: Iterable[EventVersion]) -> pa.Table:
    return _records_to_table(events, EVENT_SCHEMA)


def spine_to_table(spine: Iterable[PredictionRow]) -> pa.Table:
    return _records_to_table(spine, SPINE_SCHEMA)


def outcomes_to_table(outcomes: Iterable[OutcomeEvent]) -> pa.Table:
    return _records_to_table(outcomes, OUTCOME_SCHEMA)


def follow_up_to_table(follow_up: Iterable[FollowUp]) -> pa.Table:
    return _records_to_table(follow_up, FOLLOW_UP_SCHEMA)


def _table_to_records(table: pa.Table, model: type[Any], what: str) -> list[Any]:
    try:
        return [model(**row) for row in table.to_pylist()]
    except ValidationError as error:
        raise ContractViolation("E_SCHEMA", f"Invalid {what} record: {error}") from error


def table_to_events(table: pa.Table) -> list[EventVersion]:
    return _table_to_records(conform(table, EVENT_SCHEMA, "events"), EventVersion, "event")


def table_to_spine(table: pa.Table) -> list[PredictionRow]:
    return _table_to_records(conform(table, SPINE_SCHEMA, "spine"), PredictionRow, "spine")


def conform(table: pa.Table, schema: pa.Schema, what: str) -> pa.Table:
    """Return the contract columns of `table` with exactly the contract types.

    Safe, lossless casts are applied (for example a wider string type, or a
    coarser timestamp unit). Anything that would change meaning is refused:
    timestamps without a time zone, and precision that would be truncated.
    """
    missing = [name for name in schema.names if name not in table.column_names]
    if missing:
        raise ContractViolation(
            "E_SCHEMA", f"The {what} table is missing required columns: {missing}."
        )
    columns = []
    for field in schema:
        column = table.column(field.name)
        if column.type != field.type:
            column = _cast(column, field, what)
        columns.append(column)
    return pa.table(columns, schema=schema)


def _cast(column: pa.ChunkedArray, field: pa.Field, what: str) -> pa.ChunkedArray:
    source = column.type
    if pa.types.is_dictionary(source):
        column = column.cast(source.value_type)
        source = column.type
    if pa.types.is_timestamp(field.type):
        if not pa.types.is_timestamp(source):
            raise ContractViolation(
                "E_SCHEMA", f"Column {what}.{field.name} must be a timestamp, found {source}."
            )
        if source.tz is None:
            raise ContractViolation(
                "E_TS_NAIVE",
                f"Column {what}.{field.name} has no time zone, so its instants are ambiguous.",
                remediation="Localise the column to its true zone, then convert to UTC.",
            )
        try:
            return column.cast(field.type, safe=True)
        except pa.ArrowInvalid as error:
            raise ContractViolation(
                "E_TS_PRECISION",
                f"Column {what}.{field.name} has precision finer than a microsecond.",
                remediation="Round or truncate deliberately before loading.",
            ) from error
    try:
        return column.cast(field.type, safe=True)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as error:
        raise ContractViolation(
            "E_SCHEMA",
            f"Column {what}.{field.name} has type {source}; expected {field.type}.",
        ) from error


def read_parquet(path: str | Path, schema: pa.Schema, what: str) -> pa.Table:
    return conform(pq.read_table(path), schema, what)


def write_parquet(table: pa.Table, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, target, compression="zstd")
    return target


def content_hash(table: pa.Table, sort_keys: Sequence[str]) -> str:
    """SHA-256 of a table's logical content, independent of row order.

    Rows are sorted by `sort_keys`, then every column is fed to the hash in a
    fixed binary form. Unlike a hash of the Parquet file, this does not change
    with the writer version or compression settings.

    It detects accidental or unrecorded changes. It says nothing about whether
    the content is correct.
    """
    ordered = table.sort_by([(key, "ascending") for key in sort_keys]).combine_chunks()
    digest = hashlib.sha256()
    digest.update(f"rows={ordered.num_rows}\n".encode())
    for field in ordered.schema:
        column = ordered.column(field.name)
        digest.update(f"{field.name}:{field.type}\n".encode())
        digest.update(np.packbits(pc.is_null(column).to_numpy()).tobytes())
        if pa.types.is_string(field.type):
            # Nulls are already recorded in the mask above.
            for value in column.to_pylist():
                digest.update(b"\x1f" if value is None else value.encode() + b"\x1f")
        elif pa.types.is_floating(field.type):
            digest.update(pc.fill_null(column, 0.0).to_numpy().astype("<f8").tobytes())
        else:
            integers = pc.fill_null(column.cast(pa.int64()), 0)
            digest.update(integers.to_numpy().astype("<i8").tobytes())
    return digest.hexdigest()


def feature_schema(config: FeatureConfig) -> pa.Schema:
    fields = list(SPINE_SCHEMA)
    for feature in config.features:
        kind = pa.int64() if feature.aggregation is Aggregation.COUNT else pa.float64()
        fields.append(pa.field(feature.name, kind))
    return pa.schema(fields)


def feature_rows_to_table(rows: Iterable[FeatureRow], config: FeatureConfig) -> pa.Table:
    """Feature rows as a wide table, one row per prediction, sorted by prediction_id."""
    ordered = sorted(rows, key=lambda row: row.prediction_id)
    columns: dict[str, list[Any]] = {
        "prediction_id": [row.prediction_id for row in ordered],
        "entity_id": [row.entity_id for row in ordered],
        "encounter_id": [row.encounter_id for row in ordered],
        "prediction_time": [row.prediction_time for row in ordered],
    }
    for name in config.names:
        columns[name] = [row.values[name] for row in ordered]
    return pa.table(columns, schema=feature_schema(config))
