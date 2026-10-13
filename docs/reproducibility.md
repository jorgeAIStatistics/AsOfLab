# Reproducibility guide

## Set up

With [uv](https://docs.astral.sh/uv/) installed:

```bash
uv sync --all-extras --frozen
```

Without uv, the Makefile builds a virtual environment with pip from the
hash-pinned `requirements.lock`:

```bash
make setup
```

Python 3.12 or later is required. `uv.lock` is the lock file of record;
`requirements.lock` is exported from it with
`uv export --all-extras --frozen --no-emit-project --format requirements-txt`.

## The small demonstration

```bash
uv run asoflab demo
```

It runs every handwritten fixture through all three engines, simulates one
cohort (scenario `combined`, seed 20261, 600 patients), computes features under
four policies, evaluates, audits, and writes `runs/demo/<run id>/report.html`.
It then compares the hashes of the simulated data and of the feature tables
with the committed reference in `results/demo/` and prints whether they match.
Add `--strict` to make a mismatch an error.

## Everything else

| What | Command | Writes |
| --- | --- | --- |
| Tests | `uv run pytest -q` | nothing |
| Seeded-bug measurement | `uv run python scripts/seeded_bug_report.py --trials 10` | `results/validation/seeded_bugs.json` |
| Crash-recovery cases | `uv run python scripts/recovery_report.py` | `results/validation/recovery.json` |
| All scenarios, all seeds | `uv run asoflab experiment matrix --out results/experiments --workers 4` | one directory per run, and `summary.json` |
| Benchmarks | `uv run python benchmarks/run_benchmarks.py --profile standard` | `results/benchmarks/standard.json` |
| Tables and charts | `uv run python scripts/render_results.py` | `docs/results.md`, `docs/img/` |
| Inspection UI | `make ui && uv run asoflab serve --runs runs/demo` | nothing |

A run directory is never overwritten. To regenerate a result, delete its
directory or write to a new root.

## What is reproducible, and how exactly

| Quantity | Expectation |
| --- | --- |
| Simulated data, same platform and locked libraries | Identical content hashes. Tested |
| Feature tables from the batch backend | Bit-identical across runs and thread counts. Tested |
| Replay output | Bit-identical across chunk sizes, partition counts, worker counts and restarts. Tested |
| `statistics.json` for the same configuration | Byte-identical on the same platform. Tested |
| Batch against replay against oracle | Exact on identifiers, counts and last values; within 1e-9 relative on means and elapsed times |
| Across operating systems or CPU architectures | Not guaranteed. Floating-point library functions can differ in the last bit, which can change a simulated value or a fitted coefficient. The demo reports whether the hashes match; see the CI section of [progress.md](progress.md) for what was observed |
| Timings | Not reproducible in value. Reported with the hardware they were measured on |

## What a manifest records

Every run writes `manifest.json` with: run id, contract version, generator
version and full configuration, seed, content hashes of every input table,
schema hashes, the feature definition and its hash, every time policy with its
kind and the hash of its feature table, the label definition and cutoff, split
seeds and hashes, analysis settings, the environment (Python, platform,
processor, library versions), the code revision and whether the working tree
had uncommitted changes, and the hash and size of each output file.

A manifest supports reproduction and detects unrecorded changes. It is not
evidence that the data or the conclusions are correct.

## Local results and remote CI

Results under `results/` were produced on the development machine named in each
file's `environment` block. `.github/workflows/ci.yml` runs the tests, the
demonstration and the benchmark smoke profile on GitHub's runners. The two are
reported separately: [progress.md](progress.md) states what was run locally and
what a remote CI run was actually observed to do.
