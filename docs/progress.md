# Progress log

This file is the resume point. It records what is finished, the exact commands
that were run, what is still open, and the next action. A milestone is listed as
complete only when its gate has been run and passed.

Dates are UTC. "Local" means this development machine (Apple M3 Pro, macOS 14.5,
Python 3.12.15). Remote CI results are recorded separately and only when a CI
run has actually been observed.

## Status

| Milestone | State | Gate |
| --- | --- | --- |
| 1. Correctness kernel | **Complete** (2026-10-13) | Passed locally |
| 2. Replay and reliability | **Complete** (2026-10-13) | Passed locally |
| 3. Statistical demo | Core complete; report and full runs pending | Passed locally on small cohorts |
| 4. Performance and interface | Not started | |
| 5. Optional interoperability | Not started | |

## Milestone 1: correctness kernel

Delivered:

- `docs/temporal-contract.md`: contract version 1.0.0.
- `src/asoflab/schema/`: record models, Arrow layouts, UTC handling, set-wise validation.
- `src/asoflab/fixtures/cases/`: 20 handwritten fixtures with hand-calculated answers.
- `src/asoflab/oracle/`: loop-based reference implementation and the eligibility explainer.
- `src/asoflab/batch/`: DuckDB backend using validity intervals.
- `src/asoflab/cli.py`: `validate`, `features`, `parity`, `sql`, `explain`, `fixtures`.
- `tests/seeded_bugs.py`: 15 deliberately wrong implementations.
- `tests/unit/test_seeded_sql_bugs.py`: 9 one-line SQL mutations of the batch query.

Gate: manually verified delayed-result and revision cases, and oracle/batch parity.

```bash
uv run pytest -q                                   # 192 passed
uv run asoflab fixtures show delayed_result        # read against the hand calculation
uv run asoflab fixtures show correction_keeps_earlier_version
uv run ruff check src tests && uv run ruff format --check src tests
```

Result: 192 tests passed locally. Every seeded Python bug and every SQL mutation
is contradicted by at least one fixture. The control implementation in the
seeded-bug harness agrees with every fixture.

Design decisions taken in this milestone:

- **D-001** The canonical fixtures live in the package (`asoflab.fixtures`), not
  under `tests/`, so the CLI and other projects can run them. `tests/fixtures/`
  is reserved for test-only data.
- **D-002** Availability provenance is recorded per clock (source and pipeline
  separately), because one can be observed while the other is assumed.
- **D-003** `latest_revised` keeps a first-availability gate. See the contract,
  section 4.
- **D-004** The batch backend computes validity intervals once instead of
  resolving per cutoff. This is a different algorithm from the oracle by design.
- **D-005** Quarantine is per version, not per logical event. The audit flags
  logical events where a quarantined version outranks an active one.
- **D-006** All SQL time arithmetic uses integer microseconds, so the DuckDB
  session time zone and calendar rules cannot change a result.

## Milestone 2: replay and reliability

Delivered:

- `src/asoflab/replay/log.py`: sorts and compacts an archive into per-partition replay logs.
- `src/asoflab/replay/state.py`: version-aware state per entity, with a never-evicted revision index.
- `src/asoflab/replay/store.py`: SQLite store. Outputs, state changes and the checkpoint commit together.
- `src/asoflab/replay/engine.py`, `runner.py`: chunked replay, crash injection, bounded process pool.
- `src/asoflab/simulation/workload.py`: engineering workload generator (uniform or Zipf activity).
- `tests/strategies.py`: adversarial archive generator.
- `tests/property/`, `tests/integration/test_replay_recovery.py`, `test_process_kill.py`.
- `scripts/seeded_bug_report.py` and `results/validation/seeded_bugs.json`.

Gate: batch, replay and oracle parity, and the temporal invariance tests.

```bash
uv run pytest -q                                  # 368 passed, about 4 minutes
uv run python scripts/seeded_bug_report.py --trials 10
```

Problems the validation work found (each one is now a test):

1. **The first archive generator was too weak.** It exposed the seeded tie-break
   bug in only 1 of 4 runs of 4,000 archives. Times were too spread out for two
   observations to land on the same instant. The generator now concentrates
   events on shared instants and window edges. After the change every seeded bug
   was found in 10 of 10 seeded runs; the slowest needed 673 archives. The parity
   test's example count (1,000) is set above that.
2. **Batch output was not bit-reproducible.** With DuckDB's default threading, a
   few hundred of about 60,000 `mean` values differed in the last bits between
   identical runs, because rows reached the sum in a different order each time.
   Feature-table hashes changed from run to run. The mean now sums in a fixed
   order (`ORDER BY event time, logical event id`). Cost on a 300,000-event
   workload: 0.31 s to 0.48 s. Test: `tests/integration/test_batch_determinism.py`.
3. **Rewinding a checkpoint is not a recovery path.** State is not versioned per
   checkpoint. The store turns a re-issued prediction with different content
   into an error instead of overwriting it. Test:
   `test_rewinding_a_checkpoint_without_its_state_is_detected`.

Further decisions:

- **D-007** Deterministic summation order in the batch mean (see 2 above).
- **D-008** The replay engine handles the retrospective and naive policies by
  changing what arrives when (today's content, at first availability or at
  event time). One engine, four policies.
- **D-009** Recovery is by atomic commit of outputs, state changes and
  checkpoint in one SQLite transaction, plus keyed idempotent output writes
  that refuse a changed re-issue. Scope: one local file.

## Milestone 3: statistical demo (core)

Delivered so far:

- `src/asoflab/simulation/clinical.py`: declared data-generating process, seven scenarios.
- `src/asoflab/evaluation/`: labels, patient-level splits, logistic baseline, weighted
  metrics, paired patient-cluster bootstrap, the two experiments, chronological design,
  subgroups, label maturity.
- `src/asoflab/audit/differential.py`, `manifest.py`, `environment.py`.

Decisions made after seeing preliminary output, recorded so they can be judged:

- The regularisation grid was extended from (0.01 ... 10) to (0.001 ... 10) after a
  trial run on 800 patients selected the lowest value. On 3,000 patients the lowest
  value was selected again; validation log loss is almost flat across the grid
  (0.1633 to 0.1639). The grid was not changed again.
- A comparison of mature labels with labels recorded the moment the horizon ends
  was added to the label-maturity section after the count of affected predictions
  was seen, and before its effect on any metric had been computed.
- No parameter of the data-generating process was changed after any policy
  comparison was run. The hazard intercept was set earlier, using only the
  outcome rate.

Still to do in this milestone: HTML report, the full prespecified run matrix
(7 scenarios x 5 seeds), the cross-seed summary.

## Environment notes

- `uv` is not installed system-wide on the development machine. It was installed
  into a session-local tools environment with `pip install uv`.
- The interpreter is a uv-managed CPython 3.12.15.
- Homebrew and the GitHub CLI are not installed. Pushing works through the git
  credential already stored in the macOS keychain.

## Unresolved limitations

Tracked in `docs/limitations.md` once written. Open items so far:

- The tolerance on `mean` assumes values of comparable magnitude. Sums with
  catastrophic cancellation can exceed any fixed relative tolerance.
- Moving an observation between entities is rejected rather than modelled.
- The replay revision index is never evicted: one small entry per logical event.
- Durability against power loss on macOS needs `fullfsync`, which is not set or tested.
- Result files under `results/` are regenerated from a clean commit at the end.

## Next action

HTML report, experiment CLI and the full run matrix; then the benchmark harness.
