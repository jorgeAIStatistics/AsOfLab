# What did the pipeline know at 09:00?

*A short technical account of AsOfLab. Jorge Rodriguez, October 2026. Every
number below is in [results.md](results.md) and traced in
[claims-ledger.md](claims-ledger.md). All data are simulated.*

## The problem

A blood sample is drawn at 08:00. The laboratory releases the result at 11:00.
A model makes a prediction at 09:00.

The easy way to build training data is to ask what was measured before 09:00.
That hands the model a value nobody had for two more hours. The model then
looks better in retrospect than it could have been in use, or worse, or the
same. You cannot tell without reconstructing what was actually known.

Delay is one of three things that separate "what happened" from "what was
known". Values are corrected after the fact, and today's table holds the
correction. Results are withdrawn, and today's table does not hold them at all.
On top of that there is more than one clock: the source system has a result
before the model's pipeline does.

## What already exists

None of the ideas are new. Bitemporal modelling separates valid time from
system time, and SQL:2011 standardised it. Feature stores do point-in-time
joins; Feast added an opt-in filter on the row-creation timestamp in v0.66.0,
which is the same availability gate. Epidemiological and economic forecasting
have evaluated models on data "as it was known" for years, with tools such as
`epiprocess` and data sets of economic vintages.

[prior-art.md](prior-art.md) lists what was checked and how. What AsOfLab adds
is engineering: a written contract, three implementations that must agree, a
set of cases that are shown to be able to fail, and a statistical evaluation
that respects patient clustering. Whether that combination is useful beyond a
laptop is a hypothesis.

## The contract

Every observation is stored as append-only *versions*. A version carries when
the measurement happened, when the source had it, when the pipeline had it, and
a source revision number. Corrections and retractions are new versions.

For a prediction at time `T` and a lookback `W`:

1. Keep the versions known by `T` on the chosen clock.
2. For each observation, take the highest source revision among them.
3. Keep it if it is not a retraction and its event time is in `(T − W, T]`.
4. Aggregate, each observation once.

The order of steps 2 and 3 is part of the contract. Take today's latest version
first and then test its availability, and a correction that arrived after `T`
erases the value that really was known. Filter by window first and resolve
second, and a correction that moved an observation out of the window is
discarded, bringing the superseded version back.

Four policies make the comparison possible. Two are faithful replays (as known
to the source, as known to the pipeline). `latest_revised` respects when an
observation first appeared but reads today's corrected value. `naive_event_time`
ignores availability. The step from as-known to latest-revised isolates the
revision mistake; the step from there to naive isolates the availability
mistake.

## Three algorithms

The **oracle** is nested loops: every prediction against every version of the
same entity. It is slow and hard to get wrong.

The **batch** backend, in DuckDB, never resolves revisions per prediction. It
computes once, for each version, the interval of cutoffs during which that
version is in force: from the moment it became known, provided it outranked
everything known before it, until the next such version arrived. A prediction
joins to the intervals that contain its time, and the window is applied
afterwards.

The **replay** engine feeds versions in the order they became known,
interleaved with prediction triggers, and keeps state per entity. It commits
each chunk's outputs, state changes and checkpoint in one SQLite transaction.
It is a local simulator, written to exercise stream-like semantics and
recovery. It is not a distributed stream processor.

## Does the validation work?

A test suite that passes proves little unless it can also fail.

- 22 handwritten cases have answers worked out by hand. All three engines must
  match them.
- 15 deliberately wrong implementations, each one a mistake real pipelines
  make, must each be contradicted by at least one case. So must 11 one-line
  mutations of the batch SQL.
- A random archive generator must find each wrong implementation with no hints.

The last check failed at first. The generator exposed a tie-breaking bug in one
of four runs of 4,000 archives, because two observations almost never landed on
the same instant. After the generator was changed to concentrate events on
shared instants and window edges, every seeded bug was found in 10 of 10
trials, the slowest after 673 archives.

Two more problems surfaced the same way. One SQL mutation, which dropped the
event type from a join, survived every case until a new one was written. And
the batch backend returned means that differed in the last bits between
identical runs, because parallel aggregation added the numbers in a different
order each time. Hashes of the output changed from run to run. The mean now
sums in a fixed order.

Recovery was tested by killing the replay process at three points and
restarting it. In 9 of 9 cases the committed outputs matched an uninterrupted
run. That is a statement about one local file. It is not end-to-end
exactly-once.

## What the simulation showed

The simulation has a latent severity that evolves hourly, an outcome drawn from
a hazard, measurements taken more often when severity is higher, and three
switchable mechanisms: delayed laboratory results, corrections and retractions,
and delayed outcome ascertainment. Its parameters were fixed before any
comparison was run. Seven scenarios were run with five prespecified seeds and
3,000 synthetic patients each, and all 35 runs are reported.

One model was trained on as-known features and frozen. It was then scored on
held-out patients with features built under each policy. Uncertainty comes from
a paired bootstrap that resamples patients.

- With no delays and no revisions, every policy gave bit-identical features
  and every difference was exactly zero.
- With everything switched on, the naive policy read something unavailable in
  46.3% of predictions. AUROC changed by +0.0023 on average across the five
  seeds (range −0.0005 to +0.0049), and in all five the 95% interval included
  zero.
- With delayed outcomes, a pipeline that records labels the moment the horizon
  ends would have marked about 65% of positive labels as negatives. Scoring
  against those labels lowered average precision by 0.04 to 0.10 in all ten
  affected runs, every interval excluding zero.

So contamination was widespread and its effect on discrimination was small.
Those are different quantities. The size of the effect depends on how
informative the delayed data are, which in a simulation is set by its
parameters. A real system could show more, less, or the opposite.

## Measured trade-offs

On one laptop, with a million synthetic event versions and 183,351 predictions:

| Activity per entity | Batch | Replay, 1 worker | Replay, 8 partitions, 4 workers |
| --- | --- | --- | --- |
| Even | 1.18 s | 11.21 s | 3.44 s |
| One entity holds 16% | 17.84 s | 12.45 s | 4.10 s |

All runs gave equivalent output; speed is only compared between runs that do.
Batch wins when activity is even. Under heavy skew it must aggregate every
in-window observation for each prediction of the large entity, and it peaked at
5.3 GiB of memory against 0.9 GiB for single-worker replay. Replay answers
counts and last values with a binary search and is faster there.

The batch join was first keyed on entity alone, which is quadratic within an
entity. Adding a time-bucket key cut the skewed case from 51.75 s to 17.84 s.
Parallel replay did not help below a million events: at a hundred thousand,
four workers took 0.86 s against 0.57 s for one.

## Limits

The data are synthetic. The tool trusts the availability times it is given. It
checks timing, and cannot say whether using a measurement is clinically
appropriate or causal. Bootstrap intervals are conditional on the fitted
models. The replay engine's revision index is never evicted, because no bound
on lateness is assumed. [limitations.md](limitations.md) has the full list.

## Reproduce it

```bash
uv sync --all-extras --frozen
```

```bash
uv run asoflab demo
```

The demonstration takes about 15 seconds and compares its output with the
committed reference. [reproducibility.md](reproducibility.md) lists the command
behind every result file.

## Credit

The replay design started from Stefan Vercillo's article "Parallelizing
High-Lag Backfills with Flink". AsOfLab uses no code from it, makes no
comparison with it, and is not endorsed by its author.
