# Demo script (about 80 seconds)

A spoken walkthrough with the commands to run. Times are approximate. Every
number said aloud is printed by the command on screen or is in
[results.md](results.md); do not quote one from memory.

Before recording, from the repository root:

```bash
make setup && make ui
```

```bash
uv run asoflab demo
```

```bash
uv run asoflab serve --runs runs/demo
```

## 0:00 to 0:12. The problem

On screen: the terminal.

```bash
uv run asoflab fixtures show delayed_result
```

Say: "A lactate sample is drawn at eight. The result comes back at eleven. A
model predicts at nine. If I build features by event time, the nine o'clock
prediction gets a value nobody had for another two hours. The first table is
what the pipeline knew: nothing. The last table is the naive answer: 4.2."

## 0:12 to 0:27. Three engines, one answer

```bash
uv run asoflab fixtures check
```

Say: "That rule is written down as a contract and implemented three times: a
slow reference in plain loops, a DuckDB query, and an incremental replay with
checkpoints. Twenty-two handwritten cases with answers worked out by hand, and
all three engines have to match every one. The test suite also carries
deliberately wrong implementations, to prove these cases can fail."

## 0:27 to 0:45. The simulated cohort

On screen: the last lines of `asoflab demo`.

Say: "This is a simulated cohort. Six hundred synthetic patients, with delayed
lab results, later corrections and delayed outcomes. The naive policy read
something it could not have had in 6,156 of 13,225 predictions. Then I score
the same frozen model both ways, with a bootstrap that resamples patients, not
rows. The AUROC difference is minus 0.004, with an interval from minus 0.010 to
plus 0.002."

Say: "So in this simulation, nearly half the predictions were contaminated and
discrimination barely moved. Those are different quantities, and now both are
measured."

## 0:45 to 1:05. The timeline

On screen: the browser, Patient timeline view. Pick the first patient in the
list. Drag the cutoff line left and right.

Say: "Each row is one observation. The left mark is when it was measured, the
right mark is when it became available. As I drag the prediction time, each
version is classified by the reference engine: used, not yet available,
superseded by a correction, retracted. The reason is spelled out underneath."

Switch the policy to "Naive".

Say: "Under the naive policy the same cutoff uses values that are still in the
future."

## 1:05 to 1:20. What it is and is not

On screen: the Run overview, with the synthetic-data badge visible.

Say: "Everything here is synthetic. The effect size belongs to the simulation,
not to any hospital. What carries over is the method: a written time contract,
independent implementations that must agree, and uncertainty that respects the
fact that one patient contributes many predictions. The replay engine is a
local simulator. It was inspired by Stefan Vercillo's article on parallelising
backfills with Flink, and it is not a comparison with Flink."

## If there are ten more seconds

On screen: the Engineering view.

Say: "The engineering page shows the engines agreeing on this run, the crash
and restart cases, and the benchmark, with the commands that produced each."
