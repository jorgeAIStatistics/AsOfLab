# Limitations

What AsOfLab does not do, does not show, or has not been tested for.

## What a passing result means

- **It is not a leakage detector in general.** It checks one thing: whether
  each observation, in the revision that was in force, was available on a stated
  clock at the prediction time. It cannot tell whether using a measurement is
  clinically appropriate, whether a relationship is causal, or whether a value
  is correct.
- **It trusts the availability times it is given.** If `pipeline_available_time`
  is wrong, the replay is wrong in the same way. Provenance (`observed`,
  `assumed`, `unknown`) records how each time was obtained, and assumed times
  are counted and reported, but nothing can verify them from inside the data.
- **Hashes are not proof of correctness.** A manifest makes a run reproducible
  and makes unrecorded changes to files detectable. It says nothing about
  whether the content is true.

## The temporal contract

- Version 1 covers observed measurements only. Planned or scheduled future
  interventions are out of scope.
- An observation cannot move between entities or change event type across
  versions. Such data are rejected, not modelled.
- Quarantine is per version. If a correction or retraction has unknown
  availability, the earlier version stays in force at every cutoff. This never
  uses unestablished information, but it can be stale. The audit reports each
  such observation.
- Features are scoped to one entity. Cross-entity features, joins and global
  state are rejected.
- Four aggregations: last, count, mean, time since last. Every feature needs a
  finite window.
- Units are not converted. Mixed units within an event type are reported as a
  warning and aggregated as given.
- The equivalence tolerance on `mean` (relative 1e-9) assumes values of
  comparable magnitude. A sum with catastrophic cancellation can exceed any
  fixed relative tolerance.

## The engines

- **Replay is a local simulator.** One machine, local files. It is not Flink,
  not a distributed system, and no result here says anything about one.
- **The replay revision index is never evicted.** It holds one entry per
  logical event for the whole run. No bound on lateness is assumed, so there is
  no safe moment to drop an entry.
- **Recovery is local.** Outputs, state and checkpoint commit atomically in one
  SQLite file. That is not end-to-end exactly-once. External sinks, other
  machines and storage that loses acknowledged writes are not covered.
- **Power loss is untested.** Tests kill the process. On macOS, durability
  across power loss also needs `fullfsync`, which is not set.
- **A checkpoint cannot be rewound on its own.** State is not versioned per
  checkpoint. The store detects the resulting inconsistency and refuses to
  overwrite an issued prediction.
- **One hot entity is not parallelised.** All of an entity's records go to one
  worker.
- **The batch backend aggregates every in-window observation per prediction.**
  For an entity with very many observations and very many predictions this is
  the dominant cost, and it exceeds the replay engine's.
- **The oracle is slow by design** and is used on small data and samples.

## Validation

- Agreement between three engines is strong evidence, not proof. A mistake in
  the shared parts (record models, timestamp parsing, table layouts,
  validation) would be invisible to parity. Those parts are covered by the
  handwritten fixtures and unit tests only.
- The seeded bugs are the mistakes that were thought of. Passing them says
  nothing about mistakes that were not.
- Property tests are random. How many generated archives each seeded bug needed
  is measured and published; a bug subtler than those could need more than the
  tests run.

## The statistical demonstration

- **All data are simulated.** No result is evidence about a real patient,
  hospital or model.
- **Effect sizes belong to the simulation.** How much a timing mistake changes
  a metric depends on how informative the delayed or revised data are, which
  here is a consequence of parameters chosen in advance. A real system could
  show a larger, smaller or opposite effect.
- The simulation has no drift over calendar time, no missing-not-at-random
  outcome ascertainment, and no dependence of corrections on the outcome.
- Bootstrap intervals are conditional on the fitted models. Variability from
  model fitting appears only in the spread across the five seeds, which is a
  description of five cohorts and not an interval.
- One model family (logistic regression) and one feature set.
- Censoring is handled only by excluding unknown labels in a cohort built to
  have complete follow-up. General survival analysis is not implemented.
  Excluding unknown labels while keeping observed positives would bias
  prevalence in data with real censoring.
- Subgroup results are descriptive.

## Performance results

- One laptop, synthetic workloads, local files, warm file cache.
- Peak memory is sampled every 20 ms and can miss short peaks.
- Timings from another machine will differ. Only comparisons between runs with
  equivalent outputs are meaningful, and only those are reported as speedups.

## Optional work

- **Delivered:** a comparison with Feast 0.66.0 on one feature shape and two
  local offline stores ([integrations/feast](../integrations/feast/README.md)).
  Other Feast stores were not run, and the observation about the Dask store's
  tie-break has not been reported upstream.
- **Not delivered:** a MIMIC-IV adapter. No restricted data were accessed.
- **Not delivered:** a Flink adapter.
- **Out of scope:** authentication, multi-user features, deployment.
