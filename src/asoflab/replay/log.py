"""Replay log preparation: turn an archive and a spine into ordered streams.

A replay log is the sequence a stream processor would have received: each
version at the moment it became known, interleaved with prediction triggers.
Entities are assigned to partitions by a stable hash, and each partition is
written as one Parquet file sorted in processing order.

Sort order within a partition: `time_us`, then arrivals before triggers, then
identifiers. "Arrivals before triggers" is how the engine implements the
contract's rule that data available at exactly T is used by the prediction at T.

What "arrives when" depends on the policy:

- as-known policies: every version, at its known time on the policy's clock.
  Versions with unknown availability are quarantined (left out).
- `latest_revised`: today's content of each logical event, at the moment the
  logical event first became known.
- `naive_event_time`: today's content, at its own event time.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from asoflab.contract import CONTRACT_VERSION, MICROSECONDS_PER_SECOND, Clock, PolicyName
from asoflab.duck import connect
from asoflab.schema.models import FeatureConfig, TimePolicy
from asoflab.schema.tables import EVENT_SCHEMA, SPINE_SCHEMA, TableSource, conform

ARRIVAL = 0
TRIGGER = 1

LOG_COLUMNS = [
    "time_us",
    "kind",
    "entity_id",
    "item_id",  # logical_event_id for an arrival, prediction_id for a trigger
    "source_revision",
    "is_upsert",
    "event_type",
    "event_us",
    "value",
    "encounter_id",
]
_ORDER = "time_us, kind, entity_id, item_id, source_revision"
MANIFEST_NAME = "prepared.json"


@dataclass(frozen=True)
class PreparedLog:
    directory: Path
    policy: str
    feature_config_hash: str
    partitions: list[dict[str, Any]]

    @property
    def n_partitions(self) -> int:
        return len(self.partitions)

    @property
    def fingerprint(self) -> str:
        """Identifies this exact log: policy, features, contract, layout and file contents."""
        text = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(text.encode()).hexdigest()

    def path(self, index: int) -> Path:
        return self.directory / str(self.partitions[index]["file"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "policy": self.policy,
            "feature_config_hash": self.feature_config_hash,
            "partitions": self.partitions,
        }

    @classmethod
    def load(cls, directory: str | Path) -> "PreparedLog":
        directory = Path(directory)
        document = json.loads((directory / MANIFEST_NAME).read_text())
        return cls(
            directory, document["policy"], document["feature_config_hash"], document["partitions"]
        )


def partition_of(entity_id: str, n_partitions: int) -> int:
    """Partition for an entity. Stable across processes, platforms and library versions."""
    digest = hashlib.sha256(entity_id.encode()).digest()
    return int.from_bytes(digest[:8], "big") % n_partitions


def prepare_log(
    events: TableSource,
    spine: TableSource,
    config: FeatureConfig,
    policy: TimePolicy,
    directory: str | Path,
    *,
    n_partitions: int = 1,
    threads: int | None = None,
    memory_limit: str | None = None,
) -> PreparedLog:
    """Write one sorted log file per partition into `directory`, plus a manifest."""
    if n_partitions < 1:
        raise ValueError("n_partitions must be at least 1")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / MANIFEST_NAME).exists():
        raise FileExistsError(
            f"{directory} already holds a prepared log. Logs are never overwritten."
        )

    connection = connect(threads=threads, memory_limit=memory_limit)
    _register(connection, "events", events, EVENT_SCHEMA)
    _register(connection, "spine", spine, SPINE_SCHEMA)
    _assign_partitions(connection, n_partitions)
    connection.execute(f"CREATE TEMP TABLE stream AS {_stream_sql(config, policy)}")

    partitions = []
    for index in range(n_partitions):
        name = f"part-{index:05d}.parquet"
        target = directory / name
        connection.execute(
            f"COPY (SELECT {', '.join(LOG_COLUMNS)} FROM stream WHERE part = {index} "
            f"ORDER BY {_ORDER}) TO {_literal(str(target))} (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        row = connection.execute(
            "SELECT count(*), count(*) FILTER (WHERE kind = 0), count(*) FILTER (WHERE kind = 1) "
            f"FROM stream WHERE part = {index}"
        ).fetchone()
        assert row is not None
        partitions.append(
            {
                "file": name,
                "rows": int(row[0]),
                "arrivals": int(row[1]),
                "triggers": int(row[2]),
                "sha256": _file_hash(target),
            }
        )
    prepared = PreparedLog(directory, policy.slug(), config.config_hash(), partitions)
    (directory / MANIFEST_NAME).write_text(json.dumps(prepared.to_dict(), indent=2))
    return prepared


def _stream_sql(config: FeatureConfig, policy: TimePolicy) -> str:
    types = ", ".join(_literal(event_type) for event_type in config.event_types())
    triggers = """
    SELECT epoch_us(s.prediction_time) AS time_us, 1 AS kind, s.entity_id,
           s.prediction_id AS item_id, CAST(NULL AS BIGINT) AS source_revision,
           CAST(NULL AS BOOLEAN) AS is_upsert, CAST(NULL AS VARCHAR) AS event_type,
           CAST(NULL AS BIGINT) AS event_us, CAST(NULL AS DOUBLE) AS value,
           s.encounter_id, p.part
    FROM spine s JOIN assignment p USING (entity_id)"""

    if policy.name in (PolicyName.SOURCE_AS_KNOWN, PolicyName.PIPELINE_AS_KNOWN):
        known = _known_us_sql(policy)
        arrivals = f"""
    SELECT {known} AS time_us, 0 AS kind, e.entity_id, e.logical_event_id AS item_id,
           e.source_revision, e.operation = 'upsert' AS is_upsert, e.event_type,
           epoch_us(e.event_time) AS event_us, e.value, CAST(NULL AS VARCHAR) AS encounter_id, p.part
    FROM events e JOIN assignment p USING (entity_id)
    WHERE e.event_type IN ({types}) AND {known} IS NOT NULL"""
        return f"{arrivals}\n    UNION ALL{triggers}"

    # Retrospective and diagnostic policies read today's content for every cutoff.
    content = f"""
    WITH todays AS (
        SELECT * FROM events WHERE event_type IN ({types})
        QUALIFY row_number() OVER (PARTITION BY logical_event_id ORDER BY source_revision DESC) = 1
    )"""
    if policy.name is PolicyName.NAIVE_EVENT_TIME:
        arrival_time, gate = "epoch_us(e.event_time)", ""
    else:
        known = _known_us_sql(policy)
        content += f""",
    appeared AS (
        SELECT e.logical_event_id, min({known}) AS first_us
        FROM events e WHERE {known} IS NOT NULL GROUP BY 1
    )"""
        arrival_time, gate = "g.first_us", "JOIN appeared g USING (logical_event_id)"
    arrivals = f"""
    SELECT {arrival_time} AS time_us, 0 AS kind, e.entity_id, e.logical_event_id AS item_id,
           e.source_revision, TRUE AS is_upsert, e.event_type,
           epoch_us(e.event_time) AS event_us, e.value, CAST(NULL AS VARCHAR) AS encounter_id, p.part
    FROM todays e {gate} JOIN assignment p USING (entity_id)
    WHERE e.operation = 'upsert'"""
    return f"{content}{arrivals}\n    UNION ALL{triggers}"


def _known_us_sql(policy: TimePolicy) -> str:
    column = "source_available_time" if policy.clock is Clock.SOURCE else "pipeline_available_time"
    known = f"epoch_us(e.{column})"
    if policy.imputes:
        # Explicit sensitivity mode only; such a run is never reported as strict.
        lag_us = policy.impute_lag_seconds * MICROSECONDS_PER_SECOND
        known = f"coalesce({known}, epoch_us(e.event_time) + {lag_us})"
    return known


def _assign_partitions(connection: duckdb.DuckDBPyConnection, n_partitions: int) -> None:
    """Create `assignment(entity_id, part)`. Every row of an entity lands in one partition."""
    entities = connection.execute(
        "SELECT entity_id FROM events UNION SELECT entity_id FROM spine"
    ).to_arrow_table()
    ids = entities.column("entity_id").to_pylist()
    parts = [partition_of(entity_id, n_partitions) for entity_id in ids]
    connection.register(
        "assignment", pa.table({"entity_id": pa.array(ids, pa.string()), "part": parts})
    )


def _register(
    connection: duckdb.DuckDBPyConnection, name: str, source: TableSource, schema: pa.Schema
) -> None:
    if isinstance(source, pa.Table):
        connection.register(name, conform(source, schema, name))
        return
    path = Path(source)
    conform(pq.read_schema(path).empty_table(), schema, name)
    connection.execute(
        f"CREATE VIEW {name} AS SELECT {', '.join(schema.names)} FROM read_parquet({_literal(str(path))})"
    )


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
