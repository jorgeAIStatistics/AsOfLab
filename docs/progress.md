# Progress log

This file is the resume point. It records what is finished, the exact commands
that were run, what is still open, and the next action. A milestone is listed
as complete only when its gate has been run and passed.

Dates are UTC. "Local" means the development machine (Apple M3 Pro, macOS 14.5,
Python 3.12.15). Remote CI is reported in its own section, and only what a CI
run was actually observed to do.

## Status

| Milestone | State | Gate |
| --- | --- | --- |
| 1. Correctness kernel | Complete (2026-10-13) | Passed locally |
| 2. Replay and reliability | Complete (2026-10-13) | Passed locally |
| 3. Statistical demo | Complete (2026-10-13) | Passed locally |
| 4. Performance and interface | Complete (2026-10-13) | Passed locally; see the notes on the UI below |
| 5. Optional interoperability | Feast comparison delivered. MIMIC-IV and Flink adapters not started | Feast comparison run locally |

## What was executed, and where

All local, on the development machine.

| Command | Result | Code commit |
| --- | --- | --- |
| `uv run pytest -q` | 382 passed in about 4 minutes | `84718bf` |
| `uv run ruff check src tests benchmarks scripts`, `ruff format --check`, `uv run mypy` | Clean | `84718bf` |
| `uv run asoflab fixtures check` | 153 of 153 checks match the hand calculations | `84718bf` |
| `uv run python scripts/seeded_bug_report.py --trials 10` | Every seeded bug found in 10 of 10 trials | `20b67ce` |
| `uv run python scripts/recovery_report.py` | 9 of 9 crash cases recovered | `20b67ce` |
| `uv run asoflab experiment matrix --out results/experiments --workers 4` | 35 runs in 613 s | `d9f7497`, `20b67ce` |
| `uv run asoflab demo --out results/demo` | Reference demo run written | `20b67ce` |
| `uv run python benchmarks/run_benchmarks.py --profile standard` | 45 measured runs, all equivalent; 3 skipped | `84718bf` |
| `uv run python integrations/feast/run_comparison.py` | Agreement inside shared semantics with the DuckDB store | `20b67ce` |
| `uv run python scripts/render_results.py` | `docs/results.md` and 9 charts | working tree |

Every result file records the commit it was produced at and that the code
paths were unmodified. The three commits above differ only in a
results-rendering script and one benchmark profile setting.

## Remote CI

Workflow: `.github/workflows/ci.yml`. Results are read from GitHub's API, not
assumed.

| Run | Commit | Outcome |
| --- | --- | --- |
| 38351486559 | `d9f7497` | **Failed.** The "Install without uv" job passed on ubuntu with Python 3.12: pip install from `requirements.lock`, the unit tests, and `asoflab demo`. The four uv matrix jobs failed before running anything, because the workflow referenced `astral-sh/setup-uv@v10`, a tag that does not exist. The UI job failed because `web/` had not been committed yet. |

The workflow now pins `astral-sh/setup-uv` to the commit of release v10.3.0.
Later runs are appended here as they are observed.

Not yet established by CI: that the full test suite passes on Linux, on Python
3.13 or 3.14, and whether the demo's hashes match the macOS reference on
another platform.

## Milestone 1: correctness kernel

Delivered: the temporal contract (`docs/temporal-contract.md`, version 1.0.0);
record models, table layouts, UTC handling and set-wise validation
(`src/asoflab/schema/`); 22 handwritten fixtures
(`src/asoflab/fixtures/cases/`); the reference oracle and the eligibility
explainer; the DuckDB backend; the command line.

Gate: manually verified delayed-result and revision cases, and oracle and
batch parity.

```bash
uv run asoflab fixtures show delayed_result
```

```bash
uv run asoflab fixtures show correction_keeps_earlier_version
```

## Milestone 2: replay and reliability

Delivered: replay log preparation, version-aware state with a never-evicted
revision index, SQLite store with atomic commit of outputs, state and
checkpoint, crash injection, bounded process pool
(`src/asoflab/replay/`); the engineering workload generator; the adversarial
archive generator (`tests/strategies.py`); property, recovery and
process-kill tests.

Gate: batch, replay and oracle parity, and the temporal invariance tests.

## Milestone 3: statistical demo

Delivered: the declared simulation with seven scenarios
(`src/asoflab/simulation/clinical.py`); labels with explicit unknowns;
patient-level and chronological splits; logistic baseline; weighted metrics;
paired patient-cluster bootstrap; frozen-model and training-pipeline
experiments; subgroups; label timing; differential audit; run manifests; the
self-contained HTML report; the prespecified protocol and cross-seed summary.

Gate: reproducible results (`statistics.json` is byte-identical across two
runs of one configuration), unknown-label checks, and a negative control in
which every difference is exactly zero in all five seeds.

## Milestone 4: performance and interface

Delivered: the benchmark harness (`benchmarks/`); the read-only API
(`src/asoflab/api/`); the inspection UI (`web/`); the documentation set; the
demo command with its reference check.

Gate: every displayed claim traces to a saved output
(`docs/claims-ledger.md`); a fresh environment reproduces the small demo.

- Fresh-environment check, remote: the pip-only CI job installed from
  `requirements.lock` on a clean ubuntu runner and ran `asoflab demo`
  successfully (run 38351486559).
- The UI was built by a delegated agent to a written brief and then reviewed.
  See the handoff notes at the end of this file for what was and was not
  verified by hand.

## Milestone 5: optional interoperability

Delivered: a comparison with Feast 0.66.0 in an isolated environment
(`integrations/feast/`), with results in `results/interop/`.

Not delivered: a MIMIC-IV adapter (no restricted data were accessed) and a
Flink adapter.

## What the validation work found

Each of these is now a test.

1. **The first archive generator was too weak.** It exposed the seeded
   tie-break bug in 1 of 4 runs of 4,000 archives, because two observations
   almost never landed on the same instant. It now concentrates events on shared
   instants and window edges.
2. **Batch output was not bit-reproducible.** With DuckDB's default threading,
   a few hundred of about 60,000 means differed in the last bits between
   identical runs. The mean now sums in a fixed order.
3. **Rewinding a checkpoint is not a recovery path.** State is not versioned
   per checkpoint. The store turns a re-issued prediction with different content
   into an error.
4. **The batch join was quadratic within an entity.** Found by timing a skewed
   workload. The join is now keyed on a time bucket as well.
5. **A one-line SQL mutation survived every fixture.** Dropping the event type
   from the join double-counts only when two event types share a window length.
   Fixture `two_types_same_window` was added.
6. **An untracked script made runs record a modified tree.** The dirty check
   now covers the code paths only.

## Decisions made after seeing preliminary output

Recorded so that they can be judged.

- The regularisation grid was extended one decade downward after a trial run
  selected its lowest value. It was not changed again.
- The comparison of mature labels with labels recorded at the horizon was added
  after the count of affected predictions was seen and before its effect on any
  metric was computed.
- The number of audit examples stored per run was reduced from 200 to 25.
- No parameter of the data-generating process was changed after any policy
  comparison was run.

## Environment notes

- `uv` is not installed system-wide on the development machine. It was
  installed into a session-local tools environment with `pip install uv`.
- The interpreter is a uv-managed CPython 3.12.15. The `python3.12` that was on
  the PATH pointed at a deleted temporary directory and was left untouched.
- Homebrew and the GitHub CLI are not installed. Pushing works through the git
  credential in the macOS keychain. CI results are read from GitHub's public API.
- Commits are authored with the machine's global git identity.

## Unresolved limitations

The full list is in [limitations.md](limitations.md). The ones most worth
working on:

1. The replay revision index is never evicted. A lateness bound or a
   disk-backed index is needed for long runs.
2. The batch backend aggregates every in-window observation per prediction.
   An ASOF-style plan for `last` would remove most of that for skewed data.
3. Nothing has been run on real data. Every availability time in this
   repository is one the simulation invented.

## Next action

If work resumes: read the latest CI run and record it above.

```bash
curl -s "https://api.github.com/repos/jorgeAIStatistics/AsOfLab/actions/runs?per_page=3"
```

Then, in order of value: an adapter with a real availability clock; a lateness
bound for the replay revision index; reporting the Feast Dask-store observation
upstream, if Jorge decides to.
