# Benchmarks

```bash
uv run python benchmarks/run_benchmarks.py --profile smoke
```

```bash
uv run python benchmarks/run_benchmarks.py --profile standard
```

`smoke` takes under a minute and exists to check that the harness runs.
`standard` takes about 20 minutes on the development laptop and writes
`results/benchmarks/standard.json`, which `scripts/render_results.py` turns
into the tables and charts in `docs/results.md`.

## What a run measures

Each configuration is run once unmeasured (a warm-up), then three times, each
time in a fresh process (`worker.py`). The parent samples the resident memory
of the worker's whole process tree every 20 ms.

| Phase | Batch | Replay | Oracle |
| --- | --- | --- | --- |
| prepare | nothing | sort and compact the archive into partition logs | load rows into record objects |
| compute | the DuckDB query | replay the logs, with checkpoints | nested loops |
| export | write the feature file | collect outputs from the stores, write the feature file | write the feature file |

Workload generation and writing the input files are timed once per workload
and reported separately. Interpreter start-up is excluded from "end to end" and
included in `wall_seconds_with_startup`.

## Rules the harness enforces

- **Equal answers first.** Every run's output is compared with the batch
  backend's. A speedup is computed only between runs whose outputs are
  equivalent.
- **Nothing is dropped.** A run that fails, times out or is skipped stays in the
  results with its reason. The oracle is skipped at sizes where its quadratic
  cost would take too long; that is recorded as skipped, not omitted.
- **The setup is recorded.** Hardware, operating system, Python and library
  versions, commit, seed, input sizes, entity and prediction counts, feature
  count, partitions, workers, repetitions, warm-up and cache conditions are all
  in the result file.
- **Preparation is not hidden.** Replay's sort-and-compact step is a separate
  phase and counts toward end to end. `amortised_seconds_per_replay` states how
  many replays it is spread over.

## What these numbers are not

They are timings of a single-machine simulator on synthetic data. They do not
measure any distributed system, and they will differ on other hardware. The
oracle is a correctness reference; its times are shown for scale only.

Workloads come from `asoflab.simulation.workload`: random values with realistic
timing structure (delays, late arrivals, corrections, retractions, out-of-order
revisions, duplicates), with activity spread evenly or by a Zipf law across
entities.
