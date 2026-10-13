# Claims ledger

Every quantitative statement made in the README, the article, the draft post,
the demo script and the Feast comparison, with the file that holds the number,
the command that produced the file, the commit the code was at, and the scope
of the statement.

Rules this ledger follows:

- A number that is not in a file under `results/` is not a public claim. The
  few development observations that appear in the documents are listed
  separately at the end and marked as such.
- "Local" means the development machine: Apple M3 Pro, macOS 14.5, Python
  3.12.15. Each result file records its own environment.
- All data are synthetic. No row here is a statement about real patients, a
  real hospital, a production system, or a distributed system.
- Result files were produced at commits `d9f7497`, `20b67ce` and `1624483`. The
  changes between them do not alter what any engine or analysis computes: a
  results-rendering script, a benchmark profile setting, the inspection UI, three
  tidied tests, one import moved in the replay runner, and the load average
  being recorded with benchmark results. Each result file records the commit it
  was produced at and that the code was unmodified.

## Validation

| Id | Claim | Evidence | Command | Commit | Scope |
| --- | --- | --- | --- | --- | --- |
| V1 | 22 handwritten fixtures | Files in `src/asoflab/fixtures/cases/` | `uv run asoflab fixtures list` | any | 20 with hand-calculated answers, 2 that must be refused |
| V2 | All three engines match every fixture (153 checks) | Test output | `uv run asoflab fixtures check` | `1624483` | Local |
| V3 | 382 tests pass | Test output | `uv run pytest -q` | `e0a0e17` | Local run. Remote CI is reported separately in `progress.md` |
| V4 | 15 seeded wrong implementations, each contradicted by a fixture | `results/validation/seeded_bugs.json`, `python_bugs.*.fixtures_that_expose_it` | `uv run python scripts/seeded_bug_report.py --trials 10` | `20b67ce` | The mistakes that were thought of |
| V5 | 11 one-line SQL mutations, each exposed by a fixture | same file, `sql_mutations` | same | `20b67ce` | Batch backend, as-known policies |
| V6 | 3 seeded replay-state bugs are exposed | `tests/unit/test_seeded_replay_bugs.py` | `uv run pytest -q tests/unit/test_seeded_replay_bugs.py` | `e0a0e17` | Test only; no result file |
| V7 | Every seeded bug found in 10 of 10 random trials; slowest needed 673 archives | same file, `python_bugs.*.discovery` | as V4 | `20b67ce` | Generator in `tests/strategies.py`, budget 4,000 archives per trial, seeds 0 to 9 |
| V8 | 9 of 9 crash cases recovered | `results/validation/recovery.json`, `cases`, `all_recovered` | `uv run python scripts/recovery_report.py` | `20b67ce` | Process killed with `os._exit`; one local SQLite file per partition; not power loss |

## Statistical demonstration

| Id | Claim | Evidence | Command | Commit | Scope |
| --- | --- | --- | --- | --- | --- |
| S1 | 35 runs: 7 scenarios, 5 seeds, 3,000 synthetic patients each, all reported | `results/experiments/*/manifest.json`, `summary.json` `runs_included` | `uv run asoflab experiment matrix --out results/experiments --workers 4` | `d9f7497` (4 runs), `20b67ce` (31 runs) | Simulated cohorts |
| S2 | Negative control: features bit-identical, every difference exactly zero | `results/experiments/negative_control-*/statistics.json`, `experiment_a.features_identical_to_reference` and `differences` | as S1 | as S1 | 5 seeds |
| S3 | Share of predictions where the naive policy read something unavailable: 25.0%, 19.6%, 34.0%, 46.3% | `summary.json`, `scenarios.<name>.across_seeds.share_of_predictions_with_a_finding.mean` | as S1 | as S1 | Mean of 5 seeds; `delayed_labs`, `corrections`, `site_variation`, `combined` |
| S4 | AUROC, naive minus as-known, mean (min to max): -0.0001 (-0.0015 to +0.0014); +0.0018 (+0.0011 to +0.0029); +0.0001 (-0.0015 to +0.0014); +0.0023 (-0.0005 to +0.0049) | `summary.json`, `across_seeds.contrasts.naive_minus_as_known.auroc` | as S1 | as S1 | Same four scenarios; frozen model; held-out patients; spread across 5 simulated cohorts, not a confidence interval |
| S5 | Combined scenario: all five 95% intervals include zero. Corrections: one of five excludes zero | same, `interval_above_zero`, `interval_includes_zero`, `interval_below_zero` | as S1 | as S1 | Paired patient bootstrap, 2,000 resamples, conditional on fitted models |
| S6 | About 65% of positive labels would be recorded as negatives by labelling at the horizon | `summary.json`, `across_seeds.premature_negative_share_of_positives` (65.3% `delayed_outcomes`, 65.4% `combined`) | as S1 | as S1 | A consequence of the simulated ascertainment delay (median 8 h) against a 12 h horizon |
| S7 | Scoring against those labels lowered average precision by 0.04 to 0.10 in all ten affected runs, every interval excluding zero | `summary.json`, `per_seed[*].label_timing_contrast.average_precision` in `delayed_outcomes` and `combined` | as S1 | as S1 | Same predictions, two sets of labels |
| S8 | Demo: 6,156 of 13,225 predictions (46.5%) | `results/demo/combined-seed20261-89383319/audit.json`, `differential.predictions_with_findings` | `uv run asoflab demo --out results/demo` | `20b67ce` | 600 synthetic patients, one seed |
| S9 | Demo: AUROC difference -0.0038 (-0.0103 to +0.0019); 118 test patients, 134 events | same directory, `statistics.json`, `experiment_a` | as S8 | `20b67ce` | 500 bootstrap resamples; small test set |
| S10 | Engines agree in every experiment run | each run's `audit.json`, `parity`; `summary.json` `all_engines_agree` | as S1 | as S1 | Replay against batch on all predictions; oracle against batch on the first 25 patients |

## Performance

| Id | Claim | Evidence | Command | Commit | Scope |
| --- | --- | --- | --- | --- | --- |
| P1 | One million event versions, even activity: batch 1.17 s, replay with one worker 11.06 s, replay with 8 partitions and 4 workers 3.10 s | `results/benchmarks/standard.json`, `runs[*].median_end_to_end_seconds` for `uniform-1000000` | `uv run python benchmarks/run_benchmarks.py --profile standard` | `1624483` | One laptop in normal use (load average recorded in the file); median of 3 runs after a warm-up; fresh process each; warm file cache; excludes interpreter start-up |
| P2 | Same size, skewed (largest entity 15.9% of versions): 17.59 s, 12.43 s, 3.76 s | same file, `zipf-1000000` | as P1 | `1624483` | as P1 |
| P3 | Without the time-bucket join key the skewed case took 51.52 s | same file, `variant: no_time_buckets` | as P1 | `1624483` | as P1 |
| P4 | Peak memory on the skewed case: batch 5.8 GiB, single-worker replay 0.9 GiB | same file, `median_peak_rss_bytes` | as P1 | `1624483` | Process-tree resident memory sampled every 20 ms |
| P5 | At 100,000 events four workers took 0.55 s, the same as one; at 10,000 events 0.31 s against 0.12 s | same file, `uniform-100000` and `uniform-10000`, replay with 8 partitions | as P1 | `1624483` | as P1 |
| P6 | All 45 measured configurations gave output equivalent to the batch backend's; 3 oracle runs were skipped | same file, `runs[*].equivalent_to_reference`, `status` | as P1 | `1624483` | Equivalence as defined in the temporal contract, section 7 |

## Feast comparison

| Id | Claim | Evidence | Command | Commit | Scope |
| --- | --- | --- | --- | --- | --- |
| F1 | With Feast's DuckDB offline store, equal on 4,000 of 4,000 and 3,478 of 3,478 predictions | `results/interop/feast.json`, `cases.*.stores.duckdb.modes` | `uv run python integrations/feast/run_comparison.py` | `20b67ce` | Feast 0.66.0; one feature shape; synthetic data; shared semantics only |
| F2 | Outside the shared semantics: 188 and 121 of 3,333 differ | same file, `revisions_outside_feasts_model` | as F1 | `20b67ce` | Unsupported semantics, not defects |
| F3 | Dask store: 2,137 of 4,000 predictions absent, none of which has a value in AsOfLab | same file, `delays_only.stores.dask` | as F1 | `20b67ce` | Feast 0.66.0, Dask offline store |
| F4 | Dask store, two rows sharing an event timestamp: later-created row returned for 253 of 500 and 2,548 of 5,000 entities; DuckDB store: all | `results/interop/feast_created_tiebreak.json` | `integrations/feast/.venv/bin/python integrations/feast/repro_created_tiebreak.py` | `20b67ce` | Feast 0.66.0, pandas 2.3.3, dask 2026.8.0. Not reported upstream |
| F5 | Dask store differed on 96 and 51 predictions in the corrections case, all of them another version at the same event timestamp | `results/interop/feast.json`, `corrections_in_arrival_order.stores.dask` | as F1 | `20b67ce` | as F3 |

## Remote CI

| Id | Claim | Evidence | Commit | Scope |
| --- | --- | --- | --- | --- |
| R1 | CI passed: tests, lint, type check, demo and benchmark smoke profile on Python 3.12, 3.13 and 3.14 (`ubuntu-latest`) and 3.12 (`macos-latest`); pip-only install; UI build and tests | GitHub Actions run 38357488610, read through GitHub's API on 13 October 2026 | `d51bf24` | That commit. The README badge shows the current state |
| R2 | The demo's data and feature hashes on those runners matched the reference from the development laptop | Same run: the step "Demo output is identical to the committed reference" succeeded in all four Python jobs | `d51bf24` | One observation on those platforms and library versions |
| R3 | The first CI run failed | GitHub Actions run 38351486559: a workflow tag that did not exist, and the UI not yet committed | `d9f7497` | Fixed in the workflow |

## Statements about other projects

Not measurements. Each is sourced in [prior-art.md](prior-art.md) with a mark
saying whether the source was checked directly.

| Statement | Source |
| --- | --- |
| Feast pull request #6617 was merged on 31 July 2026 and released in v0.66.0 on 21 August 2026 | GitHub API for the pull request and the release, fetched 12 and 13 October 2026 |
| The inspiring article is titled "Parallelizing High-Lag Backfills with Flink" | The author's LinkedIn post, fetched 13 October 2026. The article body could not be retrieved |

## Development observations

These appear in the documents as an account of what the work found. They were
observed during development and are **not** backed by a committed result file.
Each is now guarded by a test.

| Observation | Where it is stated | What now guards it |
| --- | --- | --- |
| The first archive generator exposed the tie-break bug in 1 of 4 runs of 4,000 archives | README, article, `progress.md` | `tests/property/test_bug_discovery.py`; the current measurement is V7 |
| Batch means differed in the last bits between identical runs (387 of about 60,000 on one 300,000-event workload); ordering the sum cost 0.31 s to 0.48 s there | article, `architecture.md`, `progress.md` | `tests/integration/test_batch_determinism.py` |
| One SQL mutation survived every fixture until `two_types_same_window` was added | README, article, `interview-guide.md` | `tests/unit/test_seeded_sql_bugs.py` |
| The demonstration takes under half a minute | README, article | Timed informally on the development machine (11 s warm, 27 s in a fresh clone); not a benchmark |

## Not claimed anywhere

No speed multiple against any other system. No data volume beyond the workload
sizes above. No production use. No patient benefit. No clinical validation. No
statement that the tool detects leakage in general.
