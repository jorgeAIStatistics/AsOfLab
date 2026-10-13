"""Turn the saved result files into docs/results.md and the charts under docs/img/.

    uv run python scripts/render_results.py

Every table and chart is generated from a file under results/. Nothing is
typed in by hand, so a number in docs/results.md can always be traced to the
JSON it came from. Sections whose result file is missing say so.
"""

import json
import statistics
from pathlib import Path
from typing import Any

from asoflab.audit.svg import ForestRow, Series, bar_chart, forest_plot, line_chart

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
DOCS = ROOT / "docs"
IMAGES = DOCS / "img"

_STYLE = (
    "<style>svg{--surface:#fcfcfb;--grid:#e7e6e1;--text:#0b0b0b;--text-2:#52514e;--text-muted:#6f6e68;"
    "--series-1:#2a78d6;--series-2:#eb6834;--series-3:#1baf7a;"
    'font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}</style>'
    '<rect width="100%" height="100%" fill="#fcfcfb"/>'
)


def load(relative: str) -> Any:
    path = RESULTS / relative
    return json.loads(path.read_text()) if path.exists() else None


def save_chart(name: str, svg: str) -> str:
    """Write a chart as a standalone SVG file and return the Markdown that embeds it."""
    IMAGES.mkdir(parents=True, exist_ok=True)
    opening, rest = svg.split(">", 1)
    standalone = f'{opening} xmlns="http://www.w3.org/2000/svg">{_STYLE}{rest}'
    (IMAGES / f"{name}.svg").write_text(standalone)
    return f"img/{name}.svg"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def signed(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    return f"{0:.{digits}f}" if round(value, digits) == 0 else f"{value:+.{digits}f}"


def interval(entry: dict[str, Any], digits: int = 4, sign: bool = True) -> str:
    if entry.get("point") is None:
        return f"not estimable ({entry.get('reason')})"
    fmt = (lambda v: signed(v, digits)) if sign else (lambda v: f"{v:.{digits}f}")
    if entry.get("lower") is None:
        return f"{fmt(entry['point'])} (no interval)"
    return f"{fmt(entry['point'])} ({fmt(entry['lower'])} to {fmt(entry['upper'])})"


def provenance(document: dict[str, Any], path: str) -> str:
    environment = document.get("environment", {})
    commit = str(environment.get("code_commit"))[:12]
    dirty = ", with uncommitted code changes" if environment.get("code_dirty") else ""
    return (
        f"Source: `results/{path}`. Generated {document.get('generated_at')} at commit `{commit}`{dirty} "
        f"by `{document.get('command')}`."
    )


def validation_section() -> str:
    parts = ["## 1. Validation\n"]
    seeded = load("validation/seeded_bugs.json")
    if seeded is None:
        parts.append(
            "Seeded-bug results have not been generated. Run `scripts/seeded_bug_report.py`.\n"
        )
    else:
        bugs = seeded["python_bugs"]
        rows = [
            [
                f"`{name}`",
                entry["what_it_gets_wrong"],
                len(entry["fixtures_that_expose_it"]),
                f"{entry['discovery']['found_in_trials']} of {entry['discovery']['trials']}",
                f"{entry['discovery']['median']:g}"
                if entry["discovery"]["median"] is not None
                else "n/a",
                entry["discovery"]["worst"] if entry["discovery"]["worst"] is not None else "n/a",
            ]
            for name, entry in bugs.items()
        ]
        worst = max(entry["discovery"]["worst"] or 0 for entry in bugs.values())
        all_found = all(
            e["discovery"]["found_in_trials"] == e["discovery"]["trials"] for e in bugs.values()
        )
        parts += [
            "### Seeded bugs in the selection logic\n",
            f"{len(bugs)} deliberately wrong implementations. \"Fixtures\" is how many handwritten fixtures "
            "contradict the bug. \"Found\" is how many independent random searches produced an archive that "
            f"exposes it within {seeded['archive_budget_per_trial']:,} generated archives; the next two columns "
            "are how many archives the search needed.\n",
            table(["Bug", "What it gets wrong", "Fixtures", "Found", "Median archives", "Worst case"], rows),
            "",
            f"Every bug found in every trial: **{'yes' if all_found else 'no'}**. Largest number of archives "
            f"needed in any trial: **{worst:,}**. The correct control implementation was contradicted by "
            f"{len(seeded['control']['fixtures_that_contradict_it'])} fixtures and was "
            f"{'' if seeded['control']['exposed_within_budget'] else 'not '}flagged by the random search.\n",
            provenance(seeded, "validation/seeded_bugs.json") + "\n",
            "### One-line mutations of the batch SQL\n",
            table(
                ["Mutation", "Fixtures that expose it"],
                [
                    [f"`{name}`", ", ".join(f"`{f}`" for f in entry["fixtures_that_expose_it"][:3])
                     + (f" and {len(entry['fixtures_that_expose_it']) - 3} more" if len(entry["fixtures_that_expose_it"]) > 3 else "")]
                    for name, entry in seeded["sql_mutations"].items()
                ],
            ),
            "",
        ]  # fmt: skip
    recovery = load("validation/recovery.json")
    if recovery is None:
        parts.append(
            "Crash-recovery results have not been generated. Run `scripts/recovery_report.py`.\n"
        )
    else:
        workload = recovery["workload"]
        parts += [
            "### Crash and restart\n",
            f"{recovery['scope']}\n",
            f"Workload: {workload['event_versions']:,} event versions, {workload['predictions']:,} predictions, "
            f"{workload['partitions']} partitions, {workload['chunk_size']:,} log rows per commit.\n",
            table(
                ["Crash point", "Chunk", "Exit code", "Resumed from log row", "Outputs equal an uninterrupted run"],
                [
                    [f"`{case['crash_point']}`", case["chunk"], case["exit_code"], f"{case['resumed_from_offset']:,}",
                     "yes" if case["outputs_equal_uninterrupted"] else "**NO**"]
                    for case in recovery["cases"]
                ],
            ),
            "",
            f"All cases recovered: **{'yes' if recovery['all_recovered'] else 'no'}**.\n",
            provenance(recovery, "validation/recovery.json") + "\n",
        ]  # fmt: skip
    return "\n".join(parts)


def experiments_section() -> str:
    summary = load("experiments/summary.json")
    if summary is None:
        return (
            "## 2. Statistical demonstration\n\nNot generated. Run `asoflab experiment matrix`.\n"
        )
    scenarios = summary["scenarios"]
    first = next(iter(scenarios.values()))["per_seed"][0]
    parts = [
        "## 2. Statistical demonstration\n",
        f"**Simulated data.** {summary['runs_included']} runs: {len(scenarios)} scenarios, each with the same "
        f"prespecified seeds. {summary['selection']} {summary['interpretation']}\n",
        "Contrasts are absolute differences in the metric. Intervals are 95% patient-bootstrap intervals for "
        "one cohort with its fitted models held fixed.\n",
        "### Naive minus as-known, same frozen model (experiment A)\n",
    ]
    rows = []
    for name, scenario in scenarios.items():
        across = scenario["across_seeds"]
        gap = across["contrasts"]["naive_minus_as_known"]["auroc"]
        revision = across["contrasts"]["revision_effect"]["auroc"]
        availability = across["contrasts"]["availability_effect"]["auroc"]
        rows.append(
            [
                f"`{name}`",
                gap["seeds"],
                f"{signed(gap['mean'])} ({signed(gap['min'])} to {signed(gap['max'])})",
                f"{gap['interval_above_zero']} / {gap['interval_includes_zero']} / {gap['interval_below_zero']}",
                signed(revision["mean"]),
                signed(availability["mean"]),
                f"{across['share_of_predictions_with_a_finding']['mean']:.1%}",
            ]
        )
    parts += [
        table(
            ["Scenario", "Seeds", "AUROC difference: mean (min to max) across seeds",
             "Seeds with interval above / including / below zero", "Revision effect, mean",
             "Availability effect, mean", "Predictions with a finding, mean"],
            rows,
        ),
        "",
        "The share of predictions with a finding counts predictions where the naive policy read at least one "
        "observation, or one revision, that the as-known policy says was not available.\n",
    ]  # fmt: skip

    share_values = [
        s["across_seeds"]["share_of_predictions_with_a_finding"]["mean"] * 100
        for s in scenarios.values()
    ]
    chart = bar_chart(
        [name.replace("_", " ") for name in scenarios],
        [("share of predictions", share_values)],
        title="Predictions where the naive policy read something unavailable (%)",
        y_label="percent of predictions",
        tooltips=[[f"{name}: {value:.1f}% (mean of {s['across_seeds']['share_of_predictions_with_a_finding']['seeds']} seeds)"
                   for name, value, s in zip(scenarios, share_values, scenarios.values(), strict=True)]],
        width=760,
    )  # fmt: skip
    parts.append(
        f"![Share of predictions with a timing finding, by scenario. Simulated data.]({save_chart('findings_share', chart)})\n"
    )

    for name, scenario in scenarios.items():
        per_seed = scenario["per_seed"]
        parts += [f"### Scenario `{name}`\n", f"{scenario['description']}\n"]
        seed_rows = []
        forest = []
        for entry in per_seed:
            sample = entry["test_sample"]
            gap = entry["contrasts"]["naive_minus_as_known"]
            seed_rows.append(
                [
                    entry["seed"],
                    f"{sample['patients']:,}",
                    f"{sample['predictions']:,}",
                    f"{sample['events']:,}",
                    interval(entry["reference_metrics"]["auroc"], 3, sign=False),
                    interval(gap["auroc"]),
                    interval(gap["average_precision"]),
                    interval(gap["brier"], 5),
                    f"{entry['share_of_predictions_with_a_finding']:.1%}",
                    "yes" if entry["engines_agree"] else "**NO**",
                ]
            )
            forest.append(
                ForestRow(
                    f"seed {entry['seed']}",
                    gap["auroc"]["point"],
                    gap["auroc"]["lower"],
                    gap["auroc"]["upper"],
                )
            )
        parts += [
            table(
                ["Seed", "Test patients", "Predictions", "Events", "AUROC, as-known", "AUROC, naive minus as-known",
                 "Average precision, naive minus as-known", "Brier, naive minus as-known",
                 "Predictions with a finding", "Engines agree"],
                seed_rows,
            ),
            "",
        ]  # fmt: skip
        if any(row.point for row in forest):
            chart = forest_plot(
                forest,
                title=f"{name}: AUROC, naive minus as-known, by seed",
                axis_label="difference in AUROC",
                width=520,
            )
            parts.append(
                f"![AUROC difference by seed for scenario {name}. Simulated data.]({save_chart('auroc_gap_' + name, chart)})\n"
            )
        b = scenario["across_seeds"]["contrasts"]
        optimism, training = (
            b["optimism_of_the_naive_pipeline"]["auroc"],
            b["as_known_training_minus_naive_training_at_deployment"]["auroc"],
        )
        timing = scenario["across_seeds"]["label_timing_auroc_contrast"]
        premature = scenario["across_seeds"]["premature_negative_share_of_positives"]
        parts.append(
            f"Experiment B, AUROC, mean across seeds (min to max): what a naive pipeline reports minus what its model "
            f"delivers on as-known features {signed(optimism['mean'])} ({signed(optimism['min'])} to {signed(optimism['max'])}); "
            f"as-known training minus naive training, both on as-known features {signed(training['mean'])} "
            f"({signed(training['min'])} to {signed(training['max'])}). "
            f"Labels: {premature['mean']:.1%} of positive labels would have been recorded as negative by a pipeline that "
            f"labels when the horizon ends; scoring against those labels changes AUROC by {signed(timing['mean'])} on average.\n"
        )
    commits = sorted(
        {str(entry["code_commit"])[:12] for s in scenarios.values() for entry in s["per_seed"]}
    )
    dirty = any(entry["code_dirty"] for s in scenarios.values() for entry in s["per_seed"])
    parts.append(
        f"Source: `results/experiments/summary.json` and the run directories beside it. Runs were produced at commit "
        f"`{', '.join(commits)}`{' with uncommitted code changes' if dirty else ''} by "
        "`uv run asoflab experiment matrix --out results/experiments --workers 4`. "
        f"First run listed: `{first['run']}`.\n"
    )
    return "\n".join(parts)


def benchmark_section() -> str:
    document = load("benchmarks/standard.json")
    if document is None:
        return "## 3. Performance\n\nNot generated. Run `benchmarks/run_benchmarks.py --profile standard`.\n"
    environment, settings = document["environment"], document["settings"]
    workloads = {workload["workload_id"]: workload for workload in document["workloads"]}
    runs = document["runs"]
    parts = [
        "## 3. Performance\n",
        "**Synthetic workloads on one machine.** These timings describe a single-machine simulator. They are "
        "not a measurement of any distributed system, and they will differ on other hardware.\n",
        f"Machine: {environment['processor']}, {environment['physical_cpus']} physical cores, "
        f"{environment['memory_bytes'] / 2**30:.0f} GiB memory, {environment['platform']}. Python {environment['python']}, "
        f"DuckDB {environment['libraries']['duckdb']}, PyArrow {environment['libraries']['pyarrow']}.\n",
        f"Method: {settings['isolation']}; {settings['warmup']}; {settings['repetitions']} measured repetitions; "
        f"{settings['cache']}; timeout {settings['timeout_seconds']} s; policy `{settings['policy']}`; "
        f"memory: {settings['peak_memory_method']}.\n",
        "### Workloads\n",
        table(
            ["Workload", "Event versions", "Entities", "Largest entity's share", "Predictions", "Features",
             "Input size", "Generation", "Write"],
            [
                [f"`{w['workload_id']}`", f"{w['event_versions']:,}", f"{w['entities']:,}",
                 f"{w['largest_entity_share_of_versions']:.1%}", f"{w['predictions']:,}", w["features"],
                 f"{(w['input_bytes']['events_parquet'] + w['input_bytes']['spine_parquet']) / 1e6:.1f} MB",
                 f"{w['generation_seconds']:.2f} s", f"{w['write_seconds']:.2f} s"]
                for w in document["workloads"]
            ],
        ),
        "",
        "### Runs\n",
        "Times are seconds. \"End to end\" is the median over repetitions with the range in brackets, and is the "
        "sum of the three phases inside the worker process. A speedup is shown only when the output was equivalent "
        "to the batch backend's.\n",
    ]  # fmt: skip

    def label(run: dict[str, Any]) -> str:
        if run["engine"] == "replay":
            return f"replay, {run['partitions']} partition{'s' if run['partitions'] != 1 else ''}, {run['workers']} worker{'s' if run['workers'] != 1 else ''}"
        return (
            "batch, no time buckets" if run.get("variant") == "no_time_buckets" else run["engine"]
        )

    rows = []
    for run in runs:
        if run.get("status") != "ok":
            rows.append(
                [
                    f"`{run['workload_id']}`",
                    label(run),
                    f"**{run.get('status')}**",
                    "",
                    "",
                    "",
                    "",
                    "",
                    run.get("reason") or "",
                ]
            )
            continue
        median = lambda phase: statistics.median(run["phases_seconds"][phase])  # noqa: E731
        rows.append(
            [
                f"`{run['workload_id']}`",
                label(run),
                f"{run['median_end_to_end_seconds']:.2f} ({run['min_end_to_end_seconds']:.2f} to {run['max_end_to_end_seconds']:.2f})",
                f"{median('prepare'):.2f}",
                f"{median('compute'):.2f}",
                f"{median('export'):.2f}",
                f"{run['median_peak_rss_bytes'] / 2**20:,.0f} MiB",
                "yes" if run["equivalent_to_reference"] else "**NO**",
                f"{run['speedup_vs_single_worker']:.2f}x vs 1 worker"
                if run.get("speedup_vs_single_worker") and run["workers"] > 1
                else "",
            ]
        )
    parts += [
        table(
            [
                "Workload",
                "Engine",
                "End to end",
                "Prepare",
                "Compute",
                "Export",
                "Peak memory",
                "Equivalent output",
                "Note",
            ],
            rows,
        ),
        "",
    ]

    def find(
        workload_id: str,
        engine: str,
        partitions: int | None = None,
        workers: int = 1,
        variant: str = "default",
    ) -> dict[str, Any] | None:
        for run in runs:
            if (run["workload_id"], run["engine"], run["partitions"], run["workers"], run.get("variant", "default")) == (
                workload_id, engine, partitions, workers, variant,
            ) and run.get("status") == "ok":  # fmt: skip
                return run
        return None

    layouts = sorted({(r["partitions"], r["workers"]) for r in runs if r["engine"] == "replay"})
    widest = max(layouts, key=lambda layout: (layout[1], layout[0])) if layouts else None
    for skew in sorted({w["skew"] for w in document["workloads"]}):
        ids = [w["workload_id"] for w in document["workloads"] if w["skew"] == skew]
        series = []
        for name, key in [("batch", ("batch", None, 1)), ("replay, 1 worker", ("replay", 1, 1)),
                          (f"replay, {widest[1]} workers" if widest else "", ("replay", *widest) if widest else None)]:  # fmt: skip
            if key is None:
                continue
            points, tips = [], []
            for workload_id in ids:
                run = find(workload_id, key[0], key[1], key[2])
                if run:
                    points.append(
                        (workloads[workload_id]["event_versions"], run["median_end_to_end_seconds"])
                    )
                    tips.append(
                        f"{name}, {workloads[workload_id]['event_versions']:,} event versions: {run['median_end_to_end_seconds']:.2f} s median"
                    )
            if points:
                series.append(Series(name, points, tips))
        if series:
            chart = line_chart(series, title=f"End-to-end time by workload size, {skew} activity", x_label="event versions (log scale)",
                               y_label="seconds (median)", log_x=True, width=520)  # fmt: skip
            parts.append(
                f"![End-to-end seconds by workload size, {skew} activity. Synthetic workload, one machine.]({save_chart('bench_runtime_' + skew, chart)})\n"
            )

    largest = {
        skew: max(
            (w for w in document["workloads"] if w["skew"] == skew),
            key=lambda w: w["event_versions"],
        )
        for skew in {w["skew"] for w in document["workloads"]}
    }
    scaling = []
    for skew, workload in sorted(largest.items()):
        partitions = max((layout[0] for layout in layouts), default=None)
        points, tips = [], []
        for layout in layouts:
            run = find(workload["workload_id"], "replay", layout[0], layout[1])
            if layout[0] == partitions and run and run["equivalent_to_reference"]:
                points.append(
                    (
                        layout[1],
                        workload["event_versions"] / run["median_end_to_end_seconds"] / 1000,
                    )
                )
                tips.append(
                    f"{skew}, {layout[1]} workers: {points[-1][1]:,.0f} thousand event versions per second end to end"
                )
        if points:
            scaling.append(Series(f"{skew} activity", points, tips))
    if scaling:
        size = next(iter(largest.values()))["target_events"]
        chart = line_chart(scaling, title=f"Replay throughput by worker count, {size:,}-event workloads", x_label="worker processes",
                           y_label="thousand event versions per second", width=520)  # fmt: skip
        parts.append(
            f"![Replay throughput by worker count. Synthetic workload, one machine.]({save_chart('bench_scaling', chart)})\n"
        )

    categories = [f"{skew} activity" for skew in sorted(largest)]
    groups = []
    for name, key in [("batch", ("batch", None, 1)), ("replay, 1 worker", ("replay", 1, 1)),
                      (f"replay, {widest[1]} workers" if widest else "", ("replay", *widest) if widest else None)]:  # fmt: skip
        if key is None:
            continue
        values = []
        for skew in sorted(largest):
            run = find(largest[skew]["workload_id"], key[0], key[1], key[2])
            values.append(run["median_peak_rss_bytes"] / 2**20 if run else None)
        groups.append((name, values))
    if any(value is not None for _, values in groups for value in values):
        chart = bar_chart(
            categories,
            groups,
            title="Peak memory of the process tree, largest workloads (MiB)",
            y_label="MiB",
            width=520,
        )
        parts.append(
            f"![Peak resident memory by engine on the largest workloads. Synthetic workload, one machine.]({save_chart('bench_memory', chart)})\n"
        )

    parts += [
        "Notes from the harness:\n",
        "\n".join(f"- {note}" for note in document["notes"]),
        "",
        provenance(document, "benchmarks/standard.json") + "\n",
    ]
    return "\n".join(parts)


def demo_section() -> str:
    manifests = (
        sorted((RESULTS / "demo").glob("*/manifest.json")) if (RESULTS / "demo").exists() else []
    )
    if not manifests:
        return "## 4. Demonstration run\n\nNot generated. Run `asoflab demo --out results/demo`.\n"
    directory = manifests[-1].parent
    manifest = json.loads(manifests[-1].read_text())
    stats = json.loads((directory / "statistics.json").read_text())
    audit = json.loads((directory / "audit.json").read_text())
    differential = audit["differential"]
    gap = stats["experiment_a"]["prediction_weighted"]["differences"][
        "naive_event_time_minus_pipeline_as_known"
    ]
    sample = stats["experiment_a"]["sample"]
    return "\n".join(
        [
            "## 4. Demonstration run\n",
            f"The reference for `asoflab demo`: scenario `{manifest['scenario']}`, seed {manifest['seed']}, "
            f"{stats['cohort']['patients']:,} simulated patients, {stats['cohort']['event_versions']:,} event versions, "
            f"{stats['cohort']['predictions']:,} predictions.\n",
            f"- Predictions where the naive policy read something unavailable: {differential['predictions_with_findings']:,} "
            f"of {differential['predictions']:,} ({differential['predictions_with_findings'] / differential['predictions']:.1%}).",
            f"- AUROC, naive minus as-known, same frozen model: {interval(gap['auroc'])} "
            f"({sample['patients']:,} test patients, {sample['predictions']:,} predictions, {sample['events']:,} events).",
            f"- Engines agree on this cohort: {sum(e['equivalent'] for g in audit['parity'].values() for e in g.values())} of "
            f"{sum(len(g) for g in audit['parity'].values())} comparisons.",
            "",
            f"Source: `results/demo/{directory.name}/`, commit `{str(manifest['environment'].get('code_commit'))[:12]}`"
            f"{' with uncommitted code changes' if manifest['environment'].get('code_dirty') else ''}, "
            "by `uv run asoflab demo --out results/demo`.\n",
        ]
    )


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    document = "\n".join(
        [
            "# Results\n",
            "Generated by `scripts/render_results.py` from the files under `results/`. Do not edit by hand: "
            "change the code or rerun the commands, then regenerate.\n",
            "All data are synthetic. Performance figures come from one development machine. "
            "[claims-ledger.md](claims-ledger.md) maps each public claim to a row here.\n",
            validation_section(),
            experiments_section(),
            benchmark_section(),
            demo_section(),
        ]
    )
    (DOCS / "results.md").write_text(document)
    print(
        f"wrote {DOCS / 'results.md'} and {len(list(IMAGES.glob('*.svg'))) if IMAGES.exists() else 0} charts"
    )


if __name__ == "__main__":
    main()
