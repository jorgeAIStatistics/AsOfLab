# Feast comparison

An optional check that AsOfLab and [Feast](https://feast.dev) give the same
answer where they mean the same thing, and a clear statement of where they do
not mean the same thing.

It is not a benchmark and not a ranking. The two tools have different jobs:
Feast retrieves the latest feature row per entity as of a time; AsOfLab resolves
explicit revisions of each observation and aggregates over windows.

Feast pins a large dependency set of its own, so it runs in a separate
environment in this directory. The two sides exchange Parquet and JSON files
and never import each other.

## Run it

From the repository root:

```bash
uv sync --project integrations/feast --frozen
```

```bash
uv run python integrations/feast/run_comparison.py
```

Results are written to `results/interop/feast.json`. The numbers below come
from that file.

## What is compared

One feature: the last value of one event type in a 24 h window, on synthetic
data, with Feast 0.66.0 and local Parquet sources.

| Feast | AsOfLab |
| --- | --- |
| `get_historical_features(...)`, default | `naive_event_time` |
| `get_historical_features(..., filter_by_created_timestamp=True)` | `pipeline_as_known` |
| `event_timestamp` of a feature row | `event_time` of an upsert version |
| `created_timestamp_column` | `pipeline_available_time` of that version |
| feature view `ttl` | the feature's lookback window |

`filter_by_created_timestamp` was added by Feast pull request #6617 and
released in v0.66.0.

## Inside the shared semantics

| Case | Feast offline store | Predictions | Equal | Different |
| --- | --- | --- | --- | --- |
| Delayed results, no revisions | DuckDB | 4,000 | 4,000 | 0 |
| Corrections that keep the event time and arrive in order | DuckDB | 3,478 | 3,478 | 0 |

The counts are the same in both modes (default against naive, and the
created-timestamp filter against as-known). With the DuckDB offline store,
Feast and AsOfLab agree on every prediction in both cases.

## Outside the shared semantics

Feast feature rows have no logical-event identity, no revision order and no
retraction marker. A dataset with retractions, corrections that move the event
time, and lower revisions delivered late therefore cannot be expressed in a
Feast source in a way that asks the same question. On such a dataset the two
tools differed on 188 of 3,333 predictions by default and 121 with the filter
(DuckDB store). These differences are **unsupported semantics, not defects in
Feast**.

## Two observations about Feast's Dask offline store

Both are reported here as observations with Feast 0.66.0. Neither has been
reported to the Feast maintainers: no contact with maintainers was authorised
for this project.

**1. Entity rows with no feature in range are absent from the result.** With
the Dask store, 2,137 of 4,000 predictions were missing from the output in the
first case. In every one of them AsOfLab also has no value, so no value is
wrong; the rows are simply not returned. Pull request #6617 itself notes that
in the Dask store an entity row whose matching feature rows are all filtered
out disappears, and describes this as existing behaviour of that store. A
caller who needs one row per prediction should join the result back to the
entity table.

**2. When two rows share an event timestamp, the later-created row does not
reliably win.** Feast's documentation says the created timestamp deduplicates
rows that share an event timestamp, the highest winning. `repro_created_tiebreak.py`
tests exactly that using only Feast and pandas: each entity has two rows at the
same event timestamp, created an hour apart, and the entity row is later than
both.

| Offline store | Entities | Later-created row returned | Earlier-created row returned |
| --- | --- | --- | --- |
| DuckDB | 5,000 | 5,000 | 0 |
| Dask | 5 | 5 | 0 |
| Dask | 500 | 253 | 247 |
| Dask | 5,000 | 2,548 | 2,452 |

(One event time per entity; the result is the same with and without
`filter_by_created_timestamp`. Full output: `results/interop/feast_created_tiebreak.json`.)

A possible explanation, from reading `feast/infra/offline_stores/dask.py`: the
store sorts by the created timestamp and then, in a second pass, by the event
timestamp. That gives the documented result only if the second sort keeps tied
rows in their existing order, which a sort is not obliged to do. This is a
reading of the code, not a confirmed diagnosis.

Consequence for the comparison: in the corrections case, the Dask store
differed from AsOfLab on 96 predictions by default and 51 with the filter, and
every one of those differences is Feast returning another version recorded at
the same event timestamp.

To rerun the reproduction:

```bash
integrations/feast/.venv/bin/python integrations/feast/repro_created_tiebreak.py
```

## Limits

- One feature shape, one event type, synthetic data, two local offline stores.
  Other Feast stores were not run.
- Agreement on this comparison does not show that either tool is correct in
  general.
- Feast's time-to-live bound is inclusive at its lower edge and AsOfLab's window
  is exclusive there. The synthetic data make an exact coincidence at that edge
  practically impossible, so the difference is not exercised.
