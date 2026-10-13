"""Local durable storage for one replay partition: outputs, revision index, checkpoints.

Recovery contract (local SQLite only):

1. A chunk's outputs, the revision-index changes it caused, and the checkpoint
   that says "resume after this chunk" are written in ONE transaction. After a
   crash, either all three are present or none are.
2. Outputs are keyed by (run_id, prediction_id, feature_config_hash). Writing a
   key that already exists is a no-op if the content is identical and an error
   if it differs. So reprocessing input after restoring an older checkpoint
   cannot duplicate or silently change an issued prediction.
3. A store belongs to one run. Opening it for a different run is refused.
4. State is not versioned per checkpoint. Rewinding to an older checkpoint
   without restoring the matching state is not a supported recovery path. If
   it happens, rule 2 turns any changed prediction into an error instead of a
   silent rewrite.

This is not end-to-end exactly-once. It covers this file on this disk, under
SQLite's atomic-commit guarantees. It does not cover external sinks, other
machines, or storage that loses acknowledged writes.
"""

import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS outputs (
    run_id TEXT NOT NULL,
    prediction_id TEXT NOT NULL,
    feature_config_hash TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    encounter_id TEXT,
    prediction_us INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (run_id, prediction_id, feature_config_hash)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS revision_state (
    logical_event_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    is_upsert INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    event_us INTEGER,
    value REAL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS checkpoints (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    next_offset INTEGER NOT NULL,
    finished INTEGER NOT NULL
);
"""

# (prediction_id, entity_id, encounter_id, prediction_us, payload JSON)
OutputRow = tuple[str, str, str | None, int, str]
# (logical_event_id, entity_id, source_revision, is_upsert, event_type, event_us, value)
StateRow = tuple[str, str, int, bool, str, int | None, float | None]


class StoreMismatch(Exception):
    """The store on disk belongs to a different run."""


class DeterminismError(Exception):
    """A prediction that was already issued was recomputed with a different result."""


@dataclass(frozen=True)
class Checkpoint:
    next_offset: int
    finished: bool


class ReplayStore:
    def __init__(
        self, path: str | Path, *, run_id: str, feature_config_hash: str, log_sha256: str
    ) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.feature_config_hash = feature_config_hash
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: transactions are opened and closed explicitly below.
        self._connection = sqlite3.connect(self.path, isolation_level=None)
        self._connection.execute("PRAGMA journal_mode = DELETE")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._connection.executescript(_SCHEMA)
        self._bind(
            {"run_id": run_id, "feature_config_hash": feature_config_hash, "log_sha256": log_sha256}
        )

    def _bind(self, identity: dict[str, str]) -> None:
        stored = dict(self._connection.execute("SELECT key, value FROM meta").fetchall())
        if not stored:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.executemany("INSERT INTO meta VALUES (?, ?)", sorted(identity.items()))
            self._connection.execute("COMMIT")
        elif stored != identity:
            differing = sorted(key for key in identity if stored.get(key) != identity[key])
            raise StoreMismatch(
                f"{self.path} belongs to a different run (differs in {differing}). "
                "Runs are never overwritten; use a new output directory."
            )

    def latest_checkpoint(self) -> Checkpoint:
        row = self._connection.execute(
            "SELECT next_offset, finished FROM checkpoints ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        return Checkpoint(int(row[0]), bool(row[1])) if row else Checkpoint(0, False)

    def revision_state(self) -> Iterator[StateRow]:
        cursor = self._connection.execute(
            "SELECT logical_event_id, entity_id, source_revision, is_upsert, event_type, event_us, value "
            "FROM revision_state"
        )
        for row in cursor:
            yield (row[0], row[1], row[2], bool(row[3]), row[4], row[5], row[6])

    def commit_chunk(
        self,
        outputs: Sequence[OutputRow],
        changed_state: Iterable[StateRow],
        next_offset: int,
        finished: bool,
        before_commit: Callable[[], None] | None = None,
    ) -> None:
        """Write a chunk's outputs, state changes and checkpoint atomically."""
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            self._write_outputs(outputs)
            connection.executemany(
                "INSERT OR REPLACE INTO revision_state VALUES (?, ?, ?, ?, ?, ?, ?)", changed_state
            )
            connection.execute(
                "INSERT INTO checkpoints (next_offset, finished) VALUES (?, ?)",
                (next_offset, int(finished)),
            )
            if before_commit is not None:
                before_commit()
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def _write_outputs(self, outputs: Sequence[OutputRow]) -> None:
        connection = self._connection
        before = connection.total_changes
        connection.executemany(
            "INSERT INTO outputs VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (
                (
                    self.run_id,
                    prediction_id,
                    self.feature_config_hash,
                    entity_id,
                    encounter_id,
                    us,
                    payload,
                )
                for prediction_id, entity_id, encounter_id, us, payload in outputs
            ),
        )
        if connection.total_changes - before == len(outputs):
            return
        # Some keys already existed: every one of them must hold identical content.
        for prediction_id, entity_id, encounter_id, us, payload in outputs:
            existing = connection.execute(
                "SELECT entity_id, encounter_id, prediction_us, payload FROM outputs "
                "WHERE run_id = ? AND prediction_id = ? AND feature_config_hash = ?",
                (self.run_id, prediction_id, self.feature_config_hash),
            ).fetchone()
            if existing != (entity_id, encounter_id, us, payload):
                raise DeterminismError(
                    f"Prediction {prediction_id!r} was already issued in run {self.run_id} with "
                    f"different content. Issued: {existing!r}. Recomputed: "
                    f"{(entity_id, encounter_id, us, payload)!r}."
                )

    def outputs(self) -> list[tuple[Any, ...]]:
        return self._connection.execute(
            "SELECT prediction_id, entity_id, encounter_id, prediction_us, payload FROM outputs "
            "WHERE run_id = ? AND feature_config_hash = ? ORDER BY prediction_id",
            (self.run_id, self.feature_config_hash),
        ).fetchall()

    def counts(self) -> dict[str, int]:
        def one(sql: str) -> int:
            return int(self._connection.execute(sql).fetchone()[0])

        return {
            "outputs": one("SELECT count(*) FROM outputs"),
            "revision_entries": one("SELECT count(*) FROM revision_state"),
            "checkpoints": one("SELECT count(*) FROM checkpoints"),
        }

    def close(self) -> None:
        self._connection.close()


def encode_payload(values: Sequence[float | int | None]) -> str:
    """Feature values as JSON. Python writes floats in shortest round-trip form, so this is exact."""
    return json.dumps(values, separators=(",", ":"))


def decode_payload(payload: str) -> list[float | int | None]:
    values: list[float | int | None] = json.loads(payload)
    return values
