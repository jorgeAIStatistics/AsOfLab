# AsOfLab temporal contract

**Contract version:** `1.0.0` (exposed in code as `asoflab.contract.CONTRACT_VERSION`).
Every run manifest records the contract version it was computed under. A change
to any rule on this page that can change an output is a new contract version.

This page is the specification. The reference oracle
(`src/asoflab/oracle/reference.py`) is its most literal executable form, and the
handwritten fixtures in `tests/fixtures/cases/` are worked examples with answers
calculated by hand.

## 1. What the contract answers

Given an append-only archive of observation *versions* and a prediction made at
time `T`, the contract defines exactly which observations a pipeline could have
used at `T`, and which value each one had at that moment.

It covers **observed measurements** only: things that happened and were then
recorded. Planned or scheduled future interventions have different semantics
(they are knowable before they occur) and are out of scope for version 1.

It does **not** decide whether using a measurement is clinically appropriate,
whether a value is correct, or whether a relationship is causal. Timestamp rules
cannot establish any of those.

## 2. Time

| Rule | Value |
| --- | --- |
| Timeline | Instants on the UTC timeline |
| Precision | Microseconds |
| On-disk type | Arrow `timestamp[us, tz=UTC]` |
| Accepted input | Timezone-aware datetimes, or ISO-8601 strings with an explicit offset or `Z` |
| Rejected input | Naive datetimes and strings without an offset (`E_TS_NAIVE`); non-zero digits finer than a microsecond (`E_TS_PRECISION`); anything unparseable (`E_TS_INVALID`) |

Two inputs that denote the same instant are the same time. `08:00+00:00` and
`03:00-05:00` compare equal everywhere in the system.

A naive timestamp is rejected rather than assumed to be UTC, because the same
wall-clock string means different instants at different sites, and a wrong guess
silently shifts every eligibility decision.

Durations (lookback windows, horizons) are fixed amounts of elapsed time. One
"day" is exactly 24 hours. No calendar or daylight-saving arithmetic is used
anywhere.

## 3. Records

### 3.1 Event versions (append-only)

One row is one *version* of one observation. Corrections and retractions are
new rows; existing rows are never edited.

| Field | Type | Meaning |
| --- | --- | --- |
| `entity_id` | string | Whose observation this is. The patient, in the clinical example. |
| `encounter_id` | string, optional | Episode within the entity (a hospital encounter). Carried for reference; not used for eligibility. |
| `logical_event_id` | string | Stable identity of one observation across all of its versions. |
| `version_id` | string | Identity of this version within its logical event. |
| `source_revision` | integer ≥ 1 | Revision order assigned by the source. Higher supersedes lower. |
| `operation` | `upsert` or `retract` | `upsert` asserts the observation. `retract` withdraws it. |
| `event_type` | string | What was measured, for example `lab.lactate`. |
| `event_time` | timestamp | When the represented measurement happened. Required for `upsert`. |
| `value` | float | The measured value. Required and finite for `upsert`. |
| `unit` | string, optional | Unit of `value`. |
| `source` | string | Originating system. |
| `schema_version` | string | Version of the source record layout. |
| `source_available_time` | timestamp, optional | When this version became available in its source system. |
| `source_availability_provenance` | `observed`, `assumed`, `unknown` | How `source_available_time` was obtained. |
| `pipeline_available_time` | timestamp, optional | When this version became available to the prediction pipeline being replayed. |
| `pipeline_availability_provenance` | `observed`, `assumed`, `unknown` | How `pipeline_available_time` was obtained. |
| `ingestion_time` | timestamp, optional | When this row entered *this archive*. Metadata only. |

`ingestion_time` is never used to decide eligibility. A retrospective import
date says when an analyst loaded the data, not when a historical pipeline could
have seen it, and substituting one for the other is exactly the mistake this
project exists to expose.

Provenance must agree with the value: `unknown` if and only if the time is
missing (`E_PROVENANCE_MISMATCH` otherwise).

### 3.2 Identity and revision rules

- A logical event belongs to exactly one entity (`E_ENTITY_CHANGED`) and has
  exactly one event type (`E_TYPE_CHANGED`). Moving an observation to another
  patient is not supported in version 1; it must be modelled as a retraction
  plus a new logical event.
- `(logical_event_id, version_id)` identifies a version. Two rows with the same
  identity and the same payload are an **exact duplicate**: they are counted,
  reported, and have no effect on any output.
- Two rows with the same identity and a different payload are a contradiction
  and are rejected (`E_DUP_CONFLICT`). The error names the fields that differ.
  "Payload" is every field except `ingestion_time`.
- Two different versions of one logical event may not share a
  `source_revision` (`E_REV_AMBIGUOUS`), because their order would be undefined.
- Revision numbers may have gaps.
- Mutable across versions: `event_time`, `value`, `unit`, `operation`,
  `encounter_id`, `source`, both availability times.
- The event-type prefix `outcome.` is reserved and rejected in the event
  stream (`E_RESERVED_TYPE`). This is a naming guard against one specific
  accident, not a leakage detector.

### 3.3 Prediction spine

One row per prediction to be issued: `prediction_id` (unique,
`E_SPINE_DUPLICATE` otherwise), `entity_id`, optional `encounter_id`,
`prediction_time`.

Every spine row produces exactly one output row, including when no observation
is eligible.

### 3.4 Outcomes and follow-up

Outcomes live in their own tables and are never an input to feature
computation. The feature engines do not accept them as arguments.

- **Outcome event:** `outcome_event_id`, `entity_id`, optional `encounter_id`,
  `outcome_type`, `event_time` (when it happened), `ascertainment_time` (when it
  became known to whoever assigns labels).
- **Follow-up:** `entity_id`, optional `encounter_id`, `follow_up_start`,
  `follow_up_end`. Outcome status is observed only inside this interval.

Label rules are in section 8.

## 4. Time policies

A policy says which clock decides "known", and which version supplies the
content.

| Policy | A version is usable at `T` when | Content comes from | Faithful replay? |
| --- | --- | --- | --- |
| `source_as_known` | its `source_available_time ≤ T` | the highest revision usable at `T` | Yes, of the source system |
| `pipeline_as_known` | its `pipeline_available_time ≤ T` | the highest revision usable at `T` | Yes, of that pipeline |
| `latest_revised` | *any* version of the logical event was available by `T` | the highest revision in the archive | **No** |
| `naive_event_time` | always | the highest revision in the archive | **No** |

All four then require the chosen content's `event_time` to fall in the lookback
window (section 5).

`latest_revised` and `naive_event_time` are diagnostic and retrospective modes.
Their outputs are labelled as not being a faithful historical replay in every
manifest and report.

**Design decision D-003: why `latest_revised` keeps a first-availability gate.**
Read literally, "use the latest revision and filter on event time" is the same
computation as the naive mode. Defining the two that way would give one policy
two names. Instead `latest_revised` keeps the question "had this observation
appeared yet?" and drops the question "what did it say at the time?". This
separates the two mistakes the project measures:

- `pipeline_as_known` → `latest_revised` changes only **which revision** is read.
- `latest_revised` → `naive_event_time` changes only **whether availability is
  respected**.

`latest_revised` takes a `gate_clock` (`pipeline` by default, or `source`).

**Unknown availability.** Under the default `quarantine` handling, a version
whose selected availability time is missing is excluded from everything,
including revision resolution, and counted. This is the only handling that can
be reported as **strict**.

The explicit alternative `impute_event_time_plus_lag` treats a missing
availability time as `event_time + lag`. Runs that use it are labelled
**sensitivity** and can never be reported as strict.

Quarantine prevents use of information whose arrival cannot be established. It
does not make the result right: if the quarantined version was a correction or
a retraction, the earlier version stays active at every cutoff. The audit
reports each such logical event (`W_LATER_REVISION_QUARANTINED`).

## 5. Eligibility at a cutoff

For a prediction at time `T`, a feature with lookback `W` on `event_type`, and
an as-known policy with known-time `K(v)`:

1. **Eligible versions.** Keep the versions of this entity with `K(v)` present
   and `K(v) ≤ T`.
2. **Resolve.** For each logical event, the *active version* is the eligible
   version with the highest `source_revision`.
3. **Window.** The logical event contributes if its active version is an
   `upsert`, has the feature's `event_type`, and `T − W < event_time ≤ T`.
4. **Aggregate.** Each contributing logical event is used exactly once.

**The order of steps 2 and 3 is part of the contract.** Resolution comes first.
Filtering versions by window and then resolving among the survivors is wrong:
if a correction moves an observation's `event_time` out of the window, that
ordering discards the correction and resurrects the superseded version.

**Never** pick the archive's latest version first and then test its
availability. A correction that arrived after `T` would erase the earlier
version that was genuinely known at `T`.

### Boundaries and ties

| Situation | Rule |
| --- | --- |
| Availability exactly at `T` | Eligible. Arrivals at `T` are processed before the prediction at `T`. |
| `event_time` exactly `T` | Inside the window. |
| `event_time` exactly `T − W` | Outside the window. The window is `(T − W, T]`. |
| Two contributing events with the same `event_time` (for `last`) | The one with the greater `logical_event_id` wins, compared as UTF-8 byte strings. |
| A lower revision arrives after a higher one | The higher revision stays active. Arrival order never overrides source revision order. |
| An `upsert` revision after a `retract` | The observation is active again with the new content. |

### Consequences

- An observation that arrives late can contribute to later predictions for as
  long as its `event_time` is still inside their windows.
- It can never change a prediction that was already issued. Recomputing under a
  different policy is a new run with its own identity.
- Adding versions whose known-time is after `T` leaves every as-known output at
  or before `T` unchanged.

## 6. Features

Version 1 supports four aggregations over the contributing set `S`.

| Aggregation | Result | When `S` is empty |
| --- | --- | --- |
| `count` | Number of contributing logical events (integer) | `0` |
| `mean` | Arithmetic mean of the values | missing |
| `last` | Value with the greatest `(event_time, logical_event_id)` | missing |
| `time_since_last` | `(T − greatest event_time)` in hours | missing |

Every feature has a finite positive lookback window. Features are scoped to one
entity. Features that read across entities, joins between entities, and global
state are not supported, and the configuration schema rejects them
(`E_UNSUPPORTED_SCOPE`), because the parallel execution model assigns each
entity to exactly one worker.

A feature set is identified by `feature_config_hash`: the SHA-256 of its
canonical JSON. This is the "feature definition version" in output keys.

## 7. Equivalence between engines

The oracle, the batch backend, and the incremental replay must agree:

- **Exactly** on prediction identifiers, entity identifiers, prediction times,
  the set of output rows, `count`, and `last`.
- Within `rel_tol = 1e-9` and `abs_tol = 1e-12` on `mean` and
  `time_since_last`, because the engines add floating-point numbers in different
  orders.

Missing must match missing.

## 8. Labels

For a prediction at `T` with horizon `H`, the target is whether an outcome
event of the configured type occurs with `event_time` in `(T, T + H]` for the
same entity and encounter.

Evaluated with all information ascertained by a label cutoff `C` (or with no
cutoff, for a completed study):

| Status | Condition | Label |
| --- | --- | --- |
| `positive` | An event in `(T, T + H]` with `ascertainment_time ≤ C` | 1 |
| `pending_horizon` | No such event known, and `C < T + H`: the horizon has not elapsed | unknown |
| `unknown_follow_up` | No such event known, and follow-up ends before `T + H` or there is no follow-up record | unknown |
| `pending_ascertainment` | Horizon elapsed and follow-up covers it, but `C < T + H + L` | unknown |
| `negative` | None of the above | 0 |

The conditions are tested in that order.

`L` is the declared maximum outcome ascertainment lag. It is an assumption that
the analyst states. In the synthetic data it holds by construction.

**An unknown label is never converted to 0.** Unknown labels are excluded from
metrics and counted in label coverage.

A prediction is **label-mature at `C`** when `T + H + L ≤ C`. Training sets are
restricted to mature predictions for both classes. Positives become known
earlier than negatives, so admitting every label known at `C` would select on
the outcome.

## 9. Validation outcomes

Errors stop a run. Warnings are counted and reported.

| Code | Kind | Meaning |
| --- | --- | --- |
| `E_SCHEMA` | error | A column is missing or has the wrong type |
| `E_TS_NAIVE`, `E_TS_PRECISION`, `E_TS_INVALID` | error | See section 2 |
| `E_DUP_CONFLICT` | error | Same version identity, different payload |
| `E_REV_AMBIGUOUS` | error | Two versions share a source revision |
| `E_ENTITY_CHANGED`, `E_TYPE_CHANGED` | error | An immutable field differs between versions |
| `E_PROVENANCE_MISMATCH` | error | Provenance disagrees with the availability time |
| `E_UPSERT_INCOMPLETE` | error | An upsert lacks `event_time` or a finite `value` |
| `E_RESERVED_TYPE` | error | An `outcome.` event type in the event stream |
| `E_SPINE_DUPLICATE` | error | Repeated `prediction_id` |
| `E_FEATURE_CONFIG`, `E_UNSUPPORTED_SCOPE` | error | Invalid feature definition |
| `W_EXACT_DUPLICATES` | warning | Identical rows were present and had no effect |
| `W_QUARANTINED_UNKNOWN` | warning | Versions excluded for missing availability |
| `W_LATER_REVISION_QUARANTINED` | warning | A quarantined version outranks an active one |
| `W_ASSUMED_PROVENANCE` | warning | Eligibility relied on assumed availability times |
| `W_AVAIL_BEFORE_EVENT` | warning | A version is marked available before its event happened |
| `W_PIPELINE_BEFORE_SOURCE` | warning | The pipeline had a version before its source did |
| `W_MIXED_UNITS` | warning | One event type carries more than one unit |

Contradictory timestamps are reported, never rewritten. The eligibility rule
needs both `event_time ≤ T` and known-time `≤ T`, so a version marked available
before its event time still cannot contribute before the event time.

## 10. What a passing run does and does not show

A strict run with no errors shows that the outputs follow this contract for the
data supplied. It does not show that the supplied availability times are true,
that the data are complete, or that the model is safe to use. Hashes in a
manifest support reproducibility and detect accidental or unrecorded changes to
files; they are not evidence about the real world.
