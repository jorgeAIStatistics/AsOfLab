"""DuckDB connection setup shared by validation, the batch backend, and replay preparation.

Only connection plumbing lives here. No selection logic is shared through this module.
"""

import duckdb


def connect(
    *, threads: int | None = None, memory_limit: str | None = None
) -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB connection pinned to UTC.

    All time arithmetic in this project is done on integer microseconds, so the
    session time zone only affects how timestamps are displayed. It is pinned
    anyway so that nothing depends on the machine's local zone.
    """
    connection = duckdb.connect(database=":memory:")
    connection.execute("SET TimeZone = 'UTC'")
    if threads is not None:
        connection.execute(f"SET threads = {int(threads)}")
    if memory_limit is not None:
        connection.execute("SET memory_limit = ?", [memory_limit])
    return connection
