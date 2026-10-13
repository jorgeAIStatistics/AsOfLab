"""Batch backend: the temporal contract as set-based DuckDB SQL over Arrow or Parquet.

The approach differs from the oracle on purpose. Instead of resolving
revisions separately for every cutoff, it computes once, per logical event,
the interval of cutoffs during which each version is the one in force:

    version v is in force for cutoffs T with  valid_from <= T < valid_to

`valid_from` is the moment v became known, provided v outranked everything
known before it. `valid_to` is the moment the next such version became known.
A prediction then joins to the versions in force at its cutoff, and only after
that is the event-time window applied.

All time arithmetic uses integer microseconds since the epoch, so the session
time zone and calendar rules cannot affect a result.
"""

from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from asoflab.contract import (
    MICROSECONDS_PER_HOUR,
    MICROSECONDS_PER_SECOND,
    Aggregation,
    Clock,
    PolicyName,
)
from asoflab.duck import connect
from asoflab.schema.models import FeatureConfig, FeatureSpec, TimePolicy
from asoflab.schema.tables import (
    EVENT_SCHEMA,
    SPINE_SCHEMA,
    TableSource,
    conform,
    feature_schema,
)

# Sentinels for "since always" and "for ever", far outside any real timestamp
# and small enough that subtracting a window cannot overflow a 64-bit integer.
_ALWAYS = -(2**62)
_FOREVER = 2**62


def compute_features(
    events: TableSource,
    spine: TableSource,
    config: FeatureConfig,
    policy: TimePolicy,
    *,
    threads: int | None = None,
    memory_limit: str | None = None,
) -> pa.Table:
    """Features for every spine row, as a wide table sorted by prediction_id."""
    return run_query(
        events,
        spine,
        config,
        build_query(config, policy),
        threads=threads,
        memory_limit=memory_limit,
    )


def run_query(
    events: TableSource,
    spine: TableSource,
    config: FeatureConfig,
    sql: str,
    *,
    threads: int | None = None,
    memory_limit: str | None = None,
) -> pa.Table:
    """Run feature SQL against `events` and `spine`. Separate so tests can run altered SQL."""
    connection = connect(threads=threads, memory_limit=memory_limit)
    _register(connection, "events", events, EVENT_SCHEMA)
    _register(connection, "spine", spine, SPINE_SCHEMA)
    result = connection.execute(sql).to_arrow_table()
    return result.cast(feature_schema(config))


def build_query(config: FeatureConfig, policy: TimePolicy) -> str:
    """The complete SQL for one feature set under one policy."""
    types = ", ".join(_literal(event_type) for event_type in config.event_types())
    longest_us = (
        max(feature.window_seconds for feature in config.features) * MICROSECONDS_PER_SECOND
    )
    columns = ",\n       ".join(_feature_sql(feature) for feature in config.features)
    return f"""
WITH versions AS (
    SELECT entity_id, logical_event_id, source_revision, operation, event_type,
           epoch_us(event_time) AS event_us, value,
           {_known_us_sql(policy)} AS known_us
    FROM events
    WHERE event_type IN ({types})
),
{_active_sql(policy)},
cutoffs AS (
    SELECT prediction_id, entity_id, encounter_id, prediction_time,
           epoch_us(prediction_time) AS cutoff_us
    FROM spine
)
SELECT c.prediction_id, c.entity_id, c.encounter_id, c.prediction_time,
       {columns}
FROM cutoffs c
LEFT JOIN active a
  ON a.entity_id = c.entity_id
 AND a.valid_from_us <= c.cutoff_us AND c.cutoff_us < a.valid_to_us
 AND a.event_us <= c.cutoff_us AND a.event_us > c.cutoff_us - {longest_us}
GROUP BY c.prediction_id, c.entity_id, c.encounter_id, c.prediction_time, c.cutoff_us
ORDER BY c.prediction_id
"""


def contributions_query(config: FeatureConfig, policy: TimePolicy) -> str:
    """One row per (prediction, logical event) that at least one feature reads under `policy`.

    This is the feature query before aggregation: which observation, in which
    revision, with which event time and value. The audit compares two policies
    with it. An observation counts as read when it lies inside the longest
    window of any feature on its event type.
    """
    types = ", ".join(_literal(event_type) for event_type in config.event_types())
    cases = " ".join(
        f"WHEN {_literal(event_type)} THEN {seconds * MICROSECONDS_PER_SECOND}"
        for event_type, seconds in sorted(config.max_window_seconds().items())
    )
    return f"""
WITH versions AS (
    SELECT entity_id, logical_event_id, source_revision, operation, event_type,
           epoch_us(event_time) AS event_us, value,
           {_known_us_sql(policy)} AS known_us
    FROM events
    WHERE event_type IN ({types})
),
{_active_sql(policy)}
SELECT s.prediction_id, s.entity_id, epoch_us(s.prediction_time) AS cutoff_us,
       a.logical_event_id, a.event_type, a.source_revision, a.event_us, a.value
FROM spine s
JOIN active a
  ON a.entity_id = s.entity_id
 AND a.valid_from_us <= epoch_us(s.prediction_time) AND epoch_us(s.prediction_time) < a.valid_to_us
 AND a.event_us <= epoch_us(s.prediction_time)
 AND a.event_us > epoch_us(s.prediction_time) - CASE a.event_type {cases} END
"""


def in_force_query(config: FeatureConfig, policy: TimePolicy) -> str:
    """Every interval in which a version (upsert or retraction) is in force, for an as-known policy."""
    if policy.name not in (PolicyName.SOURCE_AS_KNOWN, PolicyName.PIPELINE_AS_KNOWN):
        raise ValueError("in-force intervals are defined for as-known policies only")
    types = ", ".join(_literal(event_type) for event_type in config.event_types())
    return f"""
WITH versions AS (
    SELECT entity_id, logical_event_id, source_revision, operation, event_type,
           epoch_us(event_time) AS event_us, value,
           {_known_us_sql(policy)} AS known_us
    FROM events
    WHERE event_type IN ({types})
),
{_active_sql(policy)}
SELECT logical_event_id, source_revision, operation, event_us, value,
       known_us AS valid_from_us, coalesce(replaced_us, {_FOREVER}) AS valid_to_us
FROM takeovers
"""


def known_us_expression(policy: TimePolicy) -> str:
    """SQL for a version's known time on the policy's clock (integer microseconds, or NULL)."""
    return _known_us_sql(policy)


def register_sources(
    connection: duckdb.DuckDBPyConnection, events: TableSource, spine: TableSource
) -> None:
    """Make `events` and `spine` available to queries built by this module."""
    _register(connection, "events", events, EVENT_SCHEMA)
    _register(connection, "spine", spine, SPINE_SCHEMA)


def _known_us_sql(policy: TimePolicy) -> str:
    if policy.clock is None:
        return "CAST(NULL AS BIGINT)"
    column = "source_available_time" if policy.clock is Clock.SOURCE else "pipeline_available_time"
    known = f"epoch_us({column})"
    if policy.imputes:
        # Explicit sensitivity mode only; such a run is never reported as strict.
        lag_us = policy.impute_lag_seconds * MICROSECONDS_PER_SECOND
        known = f"coalesce({known}, epoch_us(event_time) + {lag_us})"
    return known


def _active_sql(policy: TimePolicy) -> str:
    """CTEs ending in `active`: one row per interval in which an upsert is in force."""
    latest = """latest AS (
    SELECT * FROM versions
    QUALIFY row_number() OVER (PARTITION BY logical_event_id ORDER BY source_revision DESC) = 1
)"""
    if policy.name is PolicyName.NAIVE_EVENT_TIME:
        return f"""{latest},
active AS (
    SELECT entity_id, logical_event_id, source_revision, event_type, event_us, value,
           {_ALWAYS} AS valid_from_us, {_FOREVER} AS valid_to_us
    FROM latest
    WHERE operation = 'upsert'
)"""
    if policy.name is PolicyName.LATEST_REVISED:
        return f"""{latest},
first_seen AS (
    SELECT logical_event_id, min(known_us) AS first_known_us
    FROM versions
    WHERE known_us IS NOT NULL
    GROUP BY logical_event_id
),
active AS (
    SELECT l.entity_id, l.logical_event_id, l.source_revision, l.event_type, l.event_us, l.value,
           f.first_known_us AS valid_from_us, {_FOREVER} AS valid_to_us
    FROM latest l
    JOIN first_seen f USING (logical_event_id)
    WHERE l.operation = 'upsert'
)"""
    return f"""ranked AS (
    -- Versions with unknown availability are quarantined here.
    SELECT *, max(source_revision) OVER (
               PARTITION BY logical_event_id
               ORDER BY known_us, source_revision
               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS best_earlier_revision
    FROM versions
    WHERE known_us IS NOT NULL
),
takeovers AS (
    -- A version takes over on arrival only if it outranks everything known before it.
    -- A late, lower revision and an exact duplicate both fail this test.
    SELECT *, lead(known_us) OVER (
               PARTITION BY logical_event_id ORDER BY known_us, source_revision) AS replaced_us
    FROM ranked
    WHERE best_earlier_revision IS NULL OR source_revision > best_earlier_revision
),
active AS (
    -- Retractions are in force too, but contribute nothing, so they are dropped
    -- only after the intervals have been computed.
    SELECT entity_id, logical_event_id, source_revision, event_type, event_us, value,
           known_us AS valid_from_us, coalesce(replaced_us, {_FOREVER}) AS valid_to_us
    FROM takeovers
    WHERE operation = 'upsert'
)"""


def _feature_sql(feature: FeatureSpec) -> str:
    window_us = feature.window_seconds * MICROSECONDS_PER_SECOND
    in_window = (
        f"FILTER (WHERE a.event_type = {_literal(feature.event_type)} "
        f"AND a.event_us > c.cutoff_us - {window_us})"
    )
    if feature.aggregation is Aggregation.COUNT:
        expression = f"count(a.logical_event_id) {in_window}"
    elif feature.aggregation is Aggregation.MEAN:
        # Floating-point addition is not associative, and with several threads the order in
        # which rows reach an aggregate changes from run to run. Fixing the order makes the
        # result bit-identical across runs and thread counts.
        expression = f"favg(a.value ORDER BY a.event_us, a.logical_event_id) {in_window}"
    elif feature.aggregation is Aggregation.LAST:
        expression = f"arg_max(a.value, (a.event_us, a.logical_event_id)) {in_window}"
    else:
        expression = f"(c.cutoff_us - max(a.event_us) {in_window}) / {MICROSECONDS_PER_HOUR}.0"
    return f'{expression} AS "{feature.name}"'


def _literal(text: str) -> str:
    """A SQL string literal. DuckDB string literals have no backslash escapes."""
    return "'" + text.replace("'", "''") + "'"


def _register(
    connection: duckdb.DuckDBPyConnection, name: str, source: TableSource, schema: pa.Schema
) -> None:
    if isinstance(source, pa.Table):
        connection.register(name, conform(source, schema, name))
        return
    path = Path(source)
    conform(pq.read_schema(path).empty_table(), schema, name)  # type check without reading rows
    columns = ", ".join(schema.names)
    connection.execute(
        f"CREATE VIEW {name} AS SELECT {columns} FROM read_parquet({_literal(str(path))})"
    )
