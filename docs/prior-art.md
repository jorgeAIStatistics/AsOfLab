# Prior art

Sources were consulted on 12 and 13 October 2026.

Point-in-time retrieval, bitemporal modelling, event sourcing and leakage
checks all exist. AsOfLab does not claim to have invented any of them, and in
particular does not present a "known time ≤ prediction time" predicate as new.

## How to read this page

Each statement carries one of two marks.

- **[direct]** The primary source was fetched during this work and the
  statement was checked against it.
- **[assisted]** An automated research pass located and read the source and
  reported the URL. The statement was not re-read by hand. Treat it as a pointer
  to check, not as an established fact.

"Not verified" means no evidence was found either way. It never means the
capability is absent: a feature is not missing just because one page does not
mention it.

## Inspiration

**Stefan Vercillo, "Parallelizing High-Lag Backfills with Flink"**, personal
blog, <https://stefanvercillo.com/blog/archive-replay>.

- [direct] The author's LinkedIn post gives this title and URL and describes
  the problem: replaying months of archived transactions through a pipeline
  built for real-time input, and the questions of how to store data for
  efficient replay, which ordering guarantees to keep, and how to reprocess
  quickly. The post says the approach reduces backfill time from weeks to hours.
  That is the author's statement about his system; this project has not
  examined it and repeats no such figure for itself.
  (<https://www.linkedin.com/posts/stefan-vercillo_parallelizing-high-lag-backfills-with-flink-activity-7414851136999985153-S5wP>)
- **The article body could not be retrieved.** On both days the site presented
  a TLS certificate that failed verification, and the check was not bypassed.
- [assisted] Search-index excerpts indicate the article proposes compacting
  partition-ordered archives into key-grouped files so that more readers can
  replay in parallel while per-key order is kept. Its publication date is
  reported as December 2025.

What AsOfLab takes from it: the starting point that a historical archive can be
reorganised by key group so that independent readers replay disjoint groups.
The replay `prepare` step does this at laptop scale.

What is different: AsOfLab's question is temporal correctness and its measured
statistical consequences, not backfill throughput. Its replay is a local
simulator. No code from the article is used, no performance comparison with it
is made or implied, and there is no collaboration with or endorsement by its
author.

## Feature stores and point-in-time joins

**Feast.**

- [direct] Pull request #6617, "feat: Add opt-in filter_by_created_timestamp
  cutoff to get_historical_features", was merged on 31 July 2026 and closes
  issue #6615. It adds `filter_by_created_timestamp: bool = False` to
  `get_historical_features`, enforcing `created_timestamp <= entity_timestamp`.
  (<https://github.com/feast-dev/feast/pull/6617>)
- [direct] It shipped in release v0.66.0 on 21 August 2026.
  (<https://github.com/feast-dev/feast/releases/tag/v0.66.0>)
- [direct] The documentation says that by default a point-in-time join
  constrains only the feature's event timestamp, that the created-timestamp
  column otherwise deduplicates rows sharing an event timestamp, that rows with
  a NULL created timestamp are excluded when the flag is on, and that
  unsupported stores raise an error.
  (<https://docs.feast.dev/getting-started/concepts/point-in-time-joins>)
- [direct] The pull request lists the stores it implements and four it does not
  (Snowflake, Remote, Ray, MongoDB), and notes that in the Dask store an entity
  row whose matching rows are all filtered out is dropped, which it describes as
  existing behaviour of that store's time-to-live filter.
- Retraction or tombstone semantics, and revisions that change an event time:
  **not verified**. [assisted] A keyword scan of the v0.66.0 documentation found
  no mention.

*Overlap:* the opt-in filter is the same known-time gate as AsOfLab's as-known
policies. *Difference in kind:* Feast's join returns the latest feature row per
entity; AsOfLab resolves explicit revisions per observation and aggregates over
windows. They answer different requests and could be used together.

**Other platforms.** [assisted] Tecton documents an as-of join on an
internally computed effective timestamp. Chronon documents temporal accuracy
for backfills and measures offline-to-online consistency. Hopsworks
distinguishes "as of ingestion time" from "as of event time". Featureform,
Feathr and the Databricks feature store document point-in-time joins on a
timestamp column. Whether each accepts a separate per-row availability time:
not verified.

## Bitemporal data and versioned storage

- [assisted] SQL:2011 defines application-time periods and system-versioned
  tables, and a table may have both (Kulkarni and Michels, "Temporal features in
  SQL:2011", SIGMOD Record 41(3), 2012).
- [assisted] XTDB makes every table bitemporal and publishes a guide to
  point-in-time feature extraction that joins on valid time and pins system time.
- [assisted] dbt snapshots record changes to a mutable table as type-2 slowly
  changing dimensions, with `dbt_valid_from` and `dbt_valid_to`. They capture
  changes when the snapshot job runs and cannot be rebuilt afterwards.
- [assisted] Delta Lake and Apache Iceberg offer time travel to earlier table
  versions by commit time, limited by retention settings.

*Overlap:* AsOfLab's contract is a bitemporal model, and its batch backend uses
system-time validity intervals. That idea is theirs.

## As-of join operators

[assisted] DuckDB `ASOF JOIN`, `pandas.merge_asof` and Snowflake `ASOF JOIN`
match each left row to the nearest right row on a chosen column. They are
operators; what the column means is the user's business.

*Why AsOfLab does not use one:* it needs every observation in force inside a
window, not one nearest row, and "in force" depends on revision order.

## Stream processing

[assisted] Apache Flink's documentation distinguishes event time from
processing time, uses watermarks and allowed lateness for late data, supports
versioned tables and event-time temporal joins, and states that end-to-end
exactly-once needs replayable sources and transactional or idempotent sinks in
addition to checkpointed state.

*Relation:* AsOfLab's replay borrows the vocabulary (event time, arrival,
state, checkpoint). It is a single-process simulator and claims none of those
guarantees beyond its local store.

## Version-aware evaluation: the closest prior work

This is the area nearest to AsOfLab's combination of as-known reconstruction
and measured effect on evaluation.

- **epiprocess and epipredict** (CMU Delphi, R). [direct] An `epi_archive`
  holds the complete version history of a data set, `epix_as_of` returns the
  snapshot as of a version date, and the documentation names accurate model
  backtesting as a main use. Domain: epidemiological surveillance.
  (<https://cmu-delphi.github.io/epiprocess/articles/epi_archive.html>)
- **Real-time data in macroeconomics.** [assisted] Croushore and Stark, "A
  real-time data set for macroeconomists", Journal of Econometrics 105(1), 2001,
  introduced data vintages for testing whether results survive revisions.
  ALFRED and `fredapi` give access to vintages.
- **VINTAGE-TS.** [direct, abstract only] Taimoor Ahmad, "Time-Series
  Foundation Models That Understand Data Revisions", arXiv:2609.28576,
  23 September 2026. The abstract describes accompanying software with
  validity-interval reconstruction and delayed-label filtering, and reports a
  synthetic demonstration.
- **honestclock 0.1.0.** [direct, package metadata] Released on PyPI on
  6 September 2026. Its summary describes point-in-time correctness for
  regulatory panel data, separating what was knowable at a date from what had
  happened. Whether it models revisions: not verified.
- [assisted] Qlib's point-in-time database for amended financial statements;
  Featuretools cutoff times and secondary time index; Temporian; delayed-label
  evaluation in River and scikit-multiflow.

*Assessment:* the central idea, evaluating with data as it was known, is
established in epidemiological and economic forecasting. No open-source tool
was found that combines, for event-level clinical-style data, an explicit
revision contract, three independently implemented engines, an adversarial
fixture corpus and patient-clustered evaluation. That is a statement about
what a two-day search found, not proof that none exists.

## Leakage and data-quality tools

- [assisted] Deepchecks has train-test date-overlap and date-duplicate checks
  on a single datetime column. Evidently's current documentation covers drift,
  data quality and model evaluation; a temporal-leakage check was not found.
  skore flags datetime overlap between train and test.
- [assisted] scikit-learn's common-pitfalls page defines leakage as using
  information that would not be available at prediction time, and
  `TimeSeriesSplit` orders splits in time.

*Relation:* these check splits and feature-target relationships. AsOfLab
checks per-observation availability and revision state at each prediction.
They are complementary.

## Clinical prediction tooling and data

- **EHR Risk Framework.** [direct] A public repository of that name exists,
  created in March 2026. It describes itself as an open workbench for
  leakage-aware, calibrated and explainable risk modelling on longitudinal
  EHR-style data.
  (<https://github.com/ranasl62/ehr-risk-framework>) [assisted] It uses one
  index time per patient, builds features from events at or before it, audits
  for post-index feature events and patient overlap between splits, uses one
  event timestamp column, and bootstraps over test rows. A separate availability
  time: not verified.
- **MEDS and ACES.** [assisted] The Medical Event Data Standard's core schema
  has one `time` column and is open to custom columns; its MIMIC-IV conversion
  emits a laboratory result event at `storetime`. ACES defines prediction times
  and label windows declaratively. meds-evaluation reports metrics weighted by
  sample and by subject.
- **TemporAI.** [assisted] A library for time-series machine learning in
  medicine, last released in December 2023. Availability versus measurement
  time: not verified.
- [assisted] FIDDLE, timeseriesflattener and EHRSHOT build features from data
  before a prediction time.

**MIMIC-IV.**

- [direct] The `labevents` documentation defines `charttime` as when the
  measurement was charted, usually when the specimen was acquired and well
  before the result exists, and `storetime` as when the measurement became
  available in the laboratory system. Of `storetime` it says: "This is when the
  information would have been available to care providers." (MIMIC-IV
  documentation, `labevents`.)
  (<https://mimic.mit.edu/docs/iv/modules/hosp/labevents.html>)
- [assisted] `storetime` means something different in other tables: manual
  entry or validation in `chartevents`, and last update in
  `microbiologyevents`. Dates are shifted per patient, intervals within a
  patient are preserved, and patients are not comparable in calendar time.
  Access requires credentialing, a data use agreement and training.

*Consequence for an adapter:* `labevents.storetime` maps to
`source_available_time` for that table only. It says nothing about when an
arbitrary deployed pipeline received a result, so `pipeline_available_time`
would be unknown or explicitly assumed. No MIMIC adapter is included, and no
restricted data were accessed.

## Literature on leakage and timing

All [assisted].

- Kapoor and Narayanan, "Leakage and the reproducibility crisis in
  machine-learning-based science", Patterns 4(9), 2023: a taxonomy that includes
  illegitimate features and temporal leakage.
- Collins et al., "TRIPOD+AI statement", BMJ 385, 2024: reporting items on how
  and when predictors and outcomes are measured.
- Albu et al., "Challenges and Recommendations for Electronic Health Records
  Data Extraction and Preparation for Dynamic Prediction Modeling in
  Hospitalized Patients", J Med Internet Res 27, 2025: distinguishes laboratory
  timestamps and recommends result time for features.
- Ötleş et al., "Mind the Performance Gap", MLHC 2021; Kamran et al.,
  "Evaluation of Sepsis Prediction Models before Onset of Treatment", NEJM AI
  1(3), 2024; Wong et al., JAMA Intern Med 181(8), 2021; Sherman et al., AMIA
  Annual Symposium 2017: retrospective-versus-prospective gaps and the effect of
  prediction timing on reported performance.

The research pass found no paper that quantifies the inflation caused by using
collection time instead of result time for laboratory features.

## Statistical methods

All [assisted]; details in [statistical-methods.md](statistical-methods.md).
Field and Welsh, "Bootstrapping clustered data", JRSS B 69(3), 2007. Rutter,
"Bootstrap estimation of diagnostic accuracy with patient-clustered data",
Acad Radiol 7(6), 2000. Obuchowski, "Nonparametric analysis of clustered ROC
curve data", Biometrics 53(2), 1997. Van Calster et al., "Calibration: the
Achilles heel of predictive analytics", BMC Med 17, 2019.

## Reuse decisions

| Need | Decision |
| --- | --- |
| Query engine, columnar files | Reused: DuckDB, Apache Arrow, Parquet |
| Local transactional storage | Reused: SQLite |
| Model, reference metrics | Reused: scikit-learn (model; metric implementations are checked against it) |
| Property-based testing | Reused: Hypothesis |
| Input contracts, CLI | Reused: Pydantic, Typer |
| Bootstrap | Written here. [assisted] `scipy.stats.bootstrap` has no cluster parameter, and it does not report how many resamples could not be evaluated |
| As-of selection | Written here, for the reason given under "As-of join operators" |
| Version-aware archives | epiprocess and honestclock address neighbouring problems. Neither was evaluated in depth, and neither is a dependency |

## What AsOfLab offers, stated as a hypothesis

The contribution is engineering and integration: an explicit, versioned
temporal contract; three independently implemented engines that must agree; a
fixture corpus with hand-calculated answers and seeded bugs that prove the
corpus can fail; patient-clustered evaluation of timing-policy differences;
performance measurements that require equal answers; and audit outputs meant to
sit beside existing feature and data infrastructure.

It is not a leakage detector in general. Timestamp rules cannot establish
clinical appropriateness, causality or data validity.

## Explicit unknowns

- The body of the inspiring article, and whether its reported speed-up was
  measured.
- Whether Feast, Tecton, Chronon, Featureform, Feathr or Databricks handle
  retractions or per-row availability beyond what is cited.
- Whether honestclock or VINTAGE-TS's software handle event-level revisions.
- Whether another tool already provides the combination described above.
- Not checked: Netflix's DeLorean, Zipline, ArcticDB, NannyML, CapyMOA.

## Corrections made during the search

- The article's title says "High-Lag Backfills", not "historical backfills".
- Feast's filter is released (v0.66.0), not only merged.
- dbt's `timestamp` strategy takes validity times from the source's
  `updated_at` column, not from the time the snapshot ran.
