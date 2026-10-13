"""The batch backend: set-based DuckDB SQL over Arrow tables or Parquet files."""

from asoflab.batch.duckdb_backend import build_query, compute_features

__all__ = ["build_query", "compute_features"]
