"""One entry point for the three engines, so callers can swap them by name.

The engines themselves share no selection code; this module only dispatches.
"""

from typing import Any

import pyarrow as pa

from asoflab.schema.models import FeatureConfig, TimePolicy
from asoflab.schema.tables import (
    EVENT_SCHEMA,
    SPINE_SCHEMA,
    TableSource,
    conform,
    feature_rows_to_table,
    read_parquet,
    table_to_events,
    table_to_spine,
)

ENGINE_NAMES = ("oracle", "batch", "replay")


def load_table(source: TableSource, schema: pa.Schema, what: str) -> pa.Table:
    if isinstance(source, pa.Table):
        return conform(source, schema, what)
    return read_parquet(source, schema, what)


def run_engine(
    name: str,
    events: TableSource,
    spine: TableSource,
    config: FeatureConfig,
    policy: TimePolicy,
    **options: Any,
) -> pa.Table:
    """Features from the named engine, as a wide table sorted by prediction_id."""
    if name == "oracle":
        from asoflab.oracle import compute_features as oracle_features

        rows = oracle_features(
            table_to_events(load_table(events, EVENT_SCHEMA, "events")),
            table_to_spine(load_table(spine, SPINE_SCHEMA, "spine")),
            config,
            policy,
        )
        return feature_rows_to_table(rows, config)
    if name == "batch":
        from asoflab.batch import compute_features as batch_features

        return batch_features(events, spine, config, policy, **options)
    if name == "replay":
        from asoflab.replay import replay_features

        return replay_features(events, spine, config, policy, **options)
    raise ValueError(f"Unknown engine {name!r}. Choose from {ENGINE_NAMES}.")
