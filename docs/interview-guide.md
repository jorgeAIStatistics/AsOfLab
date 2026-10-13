# Interview explanation guide

How to explain AsOfLab, and how to answer the questions it invites. Each
section gives a short answer, the detail behind it, where the code is, and the
honest limit. Measured numbers are in [results.md](results.md); quote them from
there with their scope.

## The 30-second version

A model predicts at 09:00. A blood sample was drawn at 08:00 and its result
came back at 11:00. If you build training data by asking "what was measured
before 09:00", you hand the model a number nobody had. AsOfLab reconstructs
what a pipeline could actually have known at each prediction time, including
later corrections and retractions, and then measures how much the evaluation
changes when you get that wrong. It does the reconstruction three independent
ways and requires them to agree.

## 1. Time semantics

**Short answer.** Every observation has three times that matter: when it
happened, when its source had it, and when the prediction pipeline had it. A
feature may use an observation only if it happened by the prediction time *and*
was known by then on the clock being replayed.

**Detail.**

- `event_time`: when the measurement happened. It decides window membership.
- `source_available_time`: when the source system had this version.
- `pipeline_available_time`: when the model's pipeline had it. A pipeline that
  loads in hourly batches knows things later than the lab system does.
- `ingestion_time`: when the row entered this archive. Never used for
  eligibility, because a retrospective import date says when an analyst loaded
  the data, not when anyone could have used it.
- Window: `(T − W, T]`. Availability exactly at `T` counts; arrivals at `T` are
  processed before the prediction at `T`.
- All times are UTC instants at microsecond precision. A timestamp without an
  offset is refused, not assumed.

**Where.** `docs/temporal-contract.md`, `src/asoflab/schema/time.py`,
fixture `window_and_tie_boundaries`.

**Likely follow-up: "Why not assume naive timestamps are UTC?"** Because the
same wall-clock string is a different instant at different sites, and a wrong
guess shifts every eligibility decision without any error.

**Limit.** The tool trusts the availability times it is given. It records how
each was obtained, and nothing more.

## 2. Revision resolution

**Short answer.** Data are append-only versions. At a cutoff, first keep the
versions that were known, then take the highest source revision of each
observation, then apply the window. The order matters.

**Detail.**

- The wrong order number one: take today's latest version, then check whether
  it was available. A correction that arrived after the cutoff erases the
  earlier value that really was known.
- The wrong order number two: filter versions by window, then resolve. If a
  correction moved the observation's time out of the window, the correction is
  filtered away and the superseded version comes back.
- Source revision order decides, never arrival order. A stale revision
  delivered late does not win.
- A retraction is a version. It applies only from when it was known.

**Where.** `oracle/reference.py` (about 150 lines, the clearest statement),
fixtures `correction_keeps_earlier_version`, `revision_changes_event_time`,
`late_older_revision`, `retraction_known_later`.

**Likely follow-up: "How do you know the fixtures can catch these?"** Each
mistake is written out as a deliberately wrong implementation in
`tests/seeded_bugs.py`, and a test requires at least one fixture to contradict
each one. The same is done for one-line mutations of the batch SQL. One SQL
mutation survived every fixture, which is how the fixture
`two_types_same_window` came to exist.

**Limit.** Quarantine is per version: a correction with unknown availability
leaves the earlier version in force. That never uses unestablished
information, but it can be stale, and the audit says so.

## 3. Why three implementations

**Short answer.** Because the thing most likely to be wrong is the selection
logic, and a single implementation can only be tested against expectations
someone wrote by hand. Three different algorithms that must agree on generated
data test far more cases than anyone can write.

**Detail.**

- Oracle: nested loops, written to be obviously right.
- Batch: computes, once per version, the interval of cutoffs during which that
  version is in force, then joins predictions to intervals.
- Replay: feeds versions in the order they became known and keeps state.
- They share record models and input validation, not selection code.

**What the comparison found.** The first random generator was too weak: it
exposed the seeded tie-break bug in one of four long runs, because two
observations almost never landed on the same instant. And the batch backend
gave means that differed in the last bits from run to run, because parallel
aggregation added numbers in a different order each time.

**Limit.** A mistake in the shared parts is invisible to parity.

## 4. Complexity

| Engine | Cost |
| --- | --- |
| Oracle | Predictions x versions of the same entity. Quadratic within an entity |
| Batch | Computing validity intervals: a sort of the versions. Join and aggregation: every in-force observation inside a window, once per prediction that reads it |
| Replay | A sort to prepare the log. Then per arrival, a dictionary update and a sorted insert. Per prediction, a binary search for count, last and elapsed time, plus a pass over the window for the mean |

**The point worth making.** The batch join was first written on entity alone,
which is quadratic in an entity's size. Adding a time-bucket key (a bucket as
wide as the longest window, so only two buckets can hold a window's contents)
removed that. What remains is inherent to "join then aggregate": an entity with
very many observations and very many predictions is expensive for batch and
cheaper for replay, because replay answers count and last with a binary search.
On evenly spread data batch is much faster. The benchmark shows both.

**Where.** `batch/duckdb_backend.py` (`build_query`), `replay/state.py`.

## 5. Memory and state

**Short answer.** Replay keeps two things per entity. Values inside a window
are bounded and evicted. The record of the highest revision seen for each
observation is never evicted.

**Why it cannot be evicted.** A stale revision can arrive arbitrarily late and
can carry any event time. If the engine had forgotten that a retraction with a
higher revision exists, the stale version would look new and bring a withdrawn
observation back. Fixture `stale_revision_after_expiry` and a seeded "forgetful
state" show exactly that failure.

**What a real system would do.** Assume a bound on lateness and expire entries
after it, or keep the index in a disk-backed keyed store. Version 1 does
neither and says so.

## 6. Failure recovery

**Short answer.** Each chunk's outputs, state changes and checkpoint commit in
one SQLite transaction. Outputs are keyed by run, prediction and feature
definition; writing the same content again is a no-op and writing different
content is an error.

**What is tested.** Stopping at three points (mid-chunk, after writing but
before commit, just after commit) at every chunk of several fixtures, random
crash sequences with changing chunk sizes, and real process kills with
`os._exit`. In every case the final committed outputs equal those of an
uninterrupted run.

**What I do not claim.** End-to-end exactly-once. The guarantee covers one
local file. It says nothing about an external sink, another machine, or power
loss. And rewinding a checkpoint without its state is not a supported path:
the store detects it and refuses to overwrite.

**Likely follow-up: "Why is a checkpoint not enough for exactly-once?"**
Because a checkpoint only says where to resume. If outputs are written
somewhere that does not commit together with the checkpoint, a crash between
the two either loses or duplicates them. You need a transactional sink or
idempotent writes, and the sink has to take part.

## 7. Statistical clustering

**Short answer.** A patient contributes many predictions and they are
correlated. Treating rows as independent makes intervals far too narrow. So
patients are the unit of everything: the splits, and the bootstrap.

**Detail.**

- Splits assign patients, never rows.
- The bootstrap resamples patients and carries all their predictions along.
- Comparisons are paired: the same resample is used for every policy and the
  difference is computed inside it. That removes the shared patient-to-patient
  noise from the difference.
- Resamples that cannot be evaluated are counted. If too many fail, no interval
  is reported.

**The limit to state without being asked.** The intervals are conditional on
the fitted models. They cover sampling of test patients, not model fitting.
The spread across five seeds covers the rest, and five seeds are a description,
not an interval.

**Where.** `evaluation/bootstrap.py` (about 150 lines),
`tests/unit/test_bootstrap.py` (checks the width against the analytic cluster
standard error).

## 8. Labels

**Short answer.** An unknown label is not a negative. A negative is known only
after the horizon plus the longest ascertainment delay; a positive is known
sooner. Training sets wait for both.

**Why.** If you take every label known at a cutoff, recent predictions
contribute their positives and not yet their negatives. That selects on the
outcome.

## 9. What the synthetic results do and do not show

**They show** that the machinery reconstructs correctly (the negative control
gives exactly zero difference), how to measure the effect with honest
uncertainty, and that the number of predictions touched by a timing mistake and
the change in a metric are different things.

**They do not show** how large the effect is in any real system. That depends
on how informative the delayed and revised data are, which in a simulation is
whatever the parameters make it. The parameters were fixed before the
comparisons were run, every seed is reported, and nothing was tuned to produce
a headline.

**If asked "so is leakage a big deal or not?"** In this simulation the effect
on discrimination was small. That is a fact about this simulation. The
contribution is being able to answer the question for a given pipeline, with a
defined contract and an interval, instead of assuming the answer.

## 10. Questions to expect about scope

- **"Is this better than Feast?"** No such claim. Feast has had an opt-in
  created-timestamp filter since v0.66.0, which is the same availability gate.
  AsOfLab resolves explicit revisions and aggregates windows; Feast retrieves
  the latest feature row. Different jobs.
- **"Is the replay like Flink?"** It borrows the vocabulary. It is a
  single-machine simulator.
- **"Would this work on MIMIC?"** The contract maps cleanly for laboratory
  events, where the documentation distinguishes when a specimen was charted
  from when the result was available. That mapping does not carry over to other
  tables, and it says nothing about when a deployed pipeline received the data.
  No adapter is included.
- **"What would you do next?"** A lateness bound for the revision index; an
  ASOF-style plan for `last` in the batch backend; an adapter with a real
  availability clock.
