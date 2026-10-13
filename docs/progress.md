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
| 2. Replay and reliability | In progress | Not yet run |
| 3. Statistical demo | Not started | |
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

## Next action

Milestone 2: implement `src/asoflab/replay/` (log preparation, version-aware
state, SQLite store with transactional checkpoints), then the Hypothesis
strategies and parity, invariance, and crash-restart tests.
