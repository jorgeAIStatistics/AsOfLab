# Architecture

AsOfLab is a Python library with a command line, a report generator and a small
inspection UI. It runs on one machine with no services: DuckDB and Parquet for
bulk data, SQLite for replay checkpoints.

Measured numbers quoted on this page come from `results/` and are listed with
their commands in [claims-ledger.md](claims-ledger.md).

## Layout

```
src/asoflab/
  contract.py        names and constants of the temporal contract
  schema/            record models, Arrow layouts, UTC handling, validation
  fixtures/          handwritten cases with hand-calculated answers
  oracle/            reference implementation and the eligibility explainer
  batch/             DuckDB backend
  replay/            incremental replay: log, state, store, engine, runner
  simulation/        engineering workload generator; clinical simulation
  evaluation/        labels, splits, model, metrics, bootstrap, experiments
  audit/             parity, differential audit, manifests, HTML report
  api/               read-only API for the UI
  cli.py             command line
tests/               unit, property, integration; seeded bugs; generators
benchmarks/          benchmark harness
scripts/             result generators
web/                 inspection UI (React, TypeScript, Vite)
results/             small, committed, synthetic result files
```

The core (`contract`, `schema`, `oracle`, `batch`, `replay`, `audit.parity`) is
domain-independent and speaks of entities and event types.
`simulation/clinical.py` and `evaluation/experiment.py` are the clinical
example built on top of it. Nothing in the core imports them.

## Three engines, one contract

The contract ([temporal-contract.md](temporal-contract.md)) is implemented
three times, with three different algorithms, so that agreement is evidence.

| | Oracle | Batch | Replay |
| --- | --- | --- | --- |
| Where | `oracle/reference.py` | `batch/duckdb_backend.py` | `replay/` |
| Idea | For every prediction, look at every version | Compute each version's validity interval once, then join | Feed versions in the order they became known, keep state |
| Written for | Being obviously right | Throughput | Stream-like semantics and recovery |
| Shares with the others | Record models only | Record models only | Record models only |

What is shared is deliberately limited to vocabulary: the record models, the
policy and feature definitions, timestamp parsing, table layouts and input
validation. Which versions are selected, how revisions are resolved, how the
window is applied and how values are aggregated is written separately in each
engine.

### Oracle

Four nested steps per prediction, exactly as section 5 of the contract states
them: keep the versions known by the cutoff, pick the highest source revision of
each logical event, apply the event-time window, aggregate. It costs time
proportional to (predictions x versions of the same entity) and is used on
small data, on samples, and to settle disagreements.

### Batch

For as-known policies the query first turns versions into intervals:

- Order the versions of each logical event by the time they became known.
- A version *takes over* when it arrives only if its source revision is higher
  than every revision known before it. A late lower revision and an exact
  duplicate both fail this test.
- A takeover is in force from its known time until the next takeover's.

A prediction at `T` then joins to the rows with `valid_from <= T < valid_to`,
and only afterwards is the event-time window applied. This is the usual
system-time validity idea from bitemporal databases, applied per version.

Two refinements came from measurement, not from the first design:

- **Time buckets.** The join key is (entity, event type, bucket), where a bucket
  is as wide as the longest window for that event type, so a prediction is
  paired with two buckets rather than the entity's whole history. Without it the
  join is quadratic in the size of an entity. `compute_features(...,
  time_buckets=False)` keeps the plain join available so the difference can be
  measured.
- **Ordered mean.** A floating-point sum depends on the order of addition. With
  DuckDB's parallel aggregation that order changed from run to run, so identical
  runs produced means that differed in the last bits and feature-table hashes
  that did not match. The mean now sums in a fixed order (event time, then
  logical event id). Output is bit-identical across runs and thread counts.

All time arithmetic is on integer microseconds, so the session time zone and
calendar rules cannot affect a result.

Complexity: the join and aggregation visit every in-force observation inside a
window once per prediction that reads it.

### Replay

Replay is a **local, single-machine stream simulator**. It is not a
distributed stream processor and makes no claim about one.

1. **Prepare** (`replay/log.py`). Entities are assigned to partitions by a
   stable hash. For each partition one Parquet log is written, sorted by
   (time, arrivals before triggers, identifiers). What "arrives when" depends on
   the policy; see the module docstring. This step is the sort-and-compact cost
   of replay and is timed separately in the benchmarks.
2. **Replay** (`replay/engine.py`, `state.py`). The log is read in chunks. An
   arrival updates the entity's state; a trigger reads it and emits one feature
   row. Scheduling follows the known time (log order). Window membership follows
   event time. The two clocks are never mixed.
3. **Commit** (`replay/store.py`). At the end of each chunk, the chunk's
   outputs, the state changes it caused and a checkpoint are written in one
   SQLite transaction.

**State per entity.**

- `revisions`: the highest source revision applied for every logical event ever
  seen. Never evicted.
- `values`: per event type, the in-force upserts that can still fall inside a
  future window, sorted by (event time, logical event id). Evicted once older
  than the longest window for that type.

**Memory, honestly.** `values` is bounded by event rate times the longest
window. `revisions` is not bounded: it grows by one small entry per logical
event for the whole run, in memory and in the SQLite store. It cannot be dropped
without assuming a limit on how late a stale revision may arrive, because a
stale revision can carry any event time. Version 1 assumes no such limit. A
production system would need one (an allowed-lateness or time-to-live rule), or
a disk-backed keyed index.

**Why the mean is recomputed.** A running sum that adds on arrival and
subtracts on expiry drifts. Each trigger sums its window with `math.fsum`. So
`count`, `last` and `time_since_last` cost a binary search per trigger, and
`mean` costs time proportional to the window's contents.

**Parallelism.** Partitions share nothing, so they can be replayed by separate
worker processes with no coordination (`replay/runner.py`). Every record and
trigger of an entity is in one partition and therefore one worker. This is also
the limit of the model: a feature that reads across entities cannot be computed
this way, and the feature schema rejects any scope other than `entity`. One
very active entity cannot be split across workers.

### Recovery contract

Scope: one SQLite file per partition, on local disk.

1. Outputs, state changes and the checkpoint of a chunk commit together or not
   at all.
2. Outputs are keyed by `(run_id, prediction_id, feature_config_hash)`. Writing
   an existing key with identical content is a no-op. Writing it with different
   content raises `DeterminismError`. An issued prediction is never overwritten.
3. A store belongs to one run and refuses to open for another.
4. State is not versioned per checkpoint. Rewinding to an older checkpoint
   without the matching state is not supported; rule 2 turns a changed
   prediction into an error instead of a silent rewrite.

This is **not** end-to-end exactly-once. It says nothing about external sinks,
other machines, or storage that loses acknowledged writes. On macOS, SQLite's
`synchronous=FULL` does not guarantee durability across power loss unless
`fullfsync` is also set; that case is neither configured nor tested. What is
tested is process death: the replay process is killed with `os._exit` at three
points and restarted (`tests/integration/test_process_kill.py`,
`scripts/recovery_report.py`).

## Run identity and immutability

A replay run id is a hash of the prepared log's fingerprint, the feature
definition and the contract version. An experiment run id is a hash of the
generator configuration, the feature definition, the contract version, the
package version and the analysis settings. The same inputs give the same id;
any change gives a new one. A directory that already holds a completed run is
never written to again. Recomputing under another policy is a new run.

## Validation strategy

| Layer | What it shows | Where |
| --- | --- | --- |
| Handwritten fixtures | Each engine reproduces answers worked by hand | `src/asoflab/fixtures/cases/`, `tests/unit/test_fixtures.py` |
| Seeded bugs | The fixtures contradict each known mistake | `tests/seeded_bugs.py`, `test_seeded_bugs.py`, `test_seeded_sql_bugs.py`, `test_seeded_replay_bugs.py` |
| Bug discovery | The random generator finds each seeded bug unaided | `tests/property/test_bug_discovery.py`, `scripts/seeded_bug_report.py` |
| Parity | Three engines agree on generated archives under every policy | `tests/property/test_engine_parity.py` |
| Invariances | Row order, duplicates, later-known versions, other entities, chunking and partitioning do not change answers | `tests/property/test_invariances.py` |
| Recovery | Any crash point leaves the same committed outputs | `tests/integration/test_replay_recovery.py`, `test_process_kill.py` |
| Run-time checks | Every experiment run compares replay with batch on all its predictions, and the oracle with batch on a patient sample | `audit.json` of each run |

When a property fails, the failing archive is written to
`tests/counterexamples/` with the cutoff and the status of each version at that
cutoff. Hypothesis replays its smallest failing example last, so the file left
behind is the minimal case.

## Design decisions

| Id | Decision | Reason |
| --- | --- | --- |
| D-001 | Canonical fixtures ship inside the package | The CLI, and other projects, can run them |
| D-002 | Availability provenance is recorded per clock | Source availability can be observed while pipeline availability is assumed |
| D-003 | `latest_revised` keeps a first-availability gate | Otherwise it is the naive policy under another name; with the gate, the revision effect and the availability effect can be separated |
| D-004 | Batch uses validity intervals | A different algorithm from the oracle |
| D-005 | Quarantine is per version | Simple and never uses unestablished information; the audit flags the cases where it leaves a stale version active |
| D-006 | SQL time arithmetic on integer microseconds | Independent of time zone and calendar rules |
| D-007 | Ordered summation in the batch mean | Bit-identical output across runs and thread counts |
| D-008 | Replay handles all four policies by changing what arrives when | One engine, no policy branches in the state code |
| D-009 | Atomic commit plus keyed idempotent outputs | A concrete, testable local recovery contract |
| D-010 | Time-bucket join key in the batch query | Removes the quadratic cost inside one entity |
| D-011 | Bootstrap resamples expressed as integer weights | Exactly equivalent to duplicating rows, and fast enough for thousands of resamples |

## Out of scope for version 1

Kafka, Flink, Kubernetes, a custom database, authentication, cross-entity
features, planned future interventions, survival analysis. A Flink adapter
would be a separate milestone with its own integration tests.
