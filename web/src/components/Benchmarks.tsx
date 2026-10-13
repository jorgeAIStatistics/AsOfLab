/**
 * Benchmark results of one file under results/benchmarks.
 *
 * A timing is only comparable when the run finished and gave the same answer
 * as the reference engine, so the charts plot those runs only. Every run,
 * including failed, timed-out and skipped ones, is in the table at the end.
 */

import type { Benchmark, BenchmarkRun, BenchmarkWorkload, Environment } from "../api";
import { BarChart, type BarGroup } from "../charts/BarChart";
import { LineChart, type LineSeries } from "../charts/LineChart";
import { Figure } from "../charts/common";
import { formatBytes, formatCount, formatIso, formatNumber, humanise, median, plural, shortHash } from "../format";
import { CommandBlock, Empty, Facts, Table, Tag, type Column } from "./ui";

const isNumber = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);

/** A fixed colour slot per engine, so an engine has the same colour in every chart. */
const ENGINE_SLOT: Record<string, number> = { batch: 0, replay: 1, oracle: 2 };

const comparable = (run: BenchmarkRun) =>
  run.status === "ok" && run.equivalent_to_reference === true && isNumber(run.median_end_to_end_seconds);

const peakBytes = (run: BenchmarkRun) => median(run.peak_rss_bytes ?? []);
const MIB = 1024 * 1024;

function layoutText(run: BenchmarkRun): string {
  const workers = isNumber(run.workers) ? plural(run.workers, "worker") : "workers not recorded";
  return isNumber(run.partitions) ? `${workers}, ${plural(run.partitions, "partition")}` : workers;
}

const isPlain = (run: BenchmarkRun) => run.variant === undefined || run.variant === null || run.variant === "default";

/** "batch", or "batch (no time buckets)" for a named variant of an engine's configuration. */
function engineText(run: BenchmarkRun): string {
  return isPlain(run) ? run.engine : `${run.engine} (${humanise(run.variant ?? "").toLowerCase()})`;
}

/** The run that stands for an engine on a workload: the plain configuration, one worker, fewest partitions. */
function representative(runs: readonly BenchmarkRun[]): BenchmarkRun | undefined {
  return runs
    .filter(isPlain)
    .sort((a, b) => (a.workers ?? 1) - (b.workers ?? 1) || (a.partitions ?? 0) - (b.partitions ?? 0))[0];
}

function sizeOf(workload: BenchmarkWorkload): number | null {
  return workload.event_versions ?? workload.target_events ?? null;
}

function inputBytes(workload: BenchmarkWorkload): number | null {
  const value = workload.input_bytes;
  if (typeof value === "number") return value;
  if (value && typeof value === "object") return Object.values(value).reduce((sum, part) => sum + (isNumber(part) ? part : 0), 0);
  return null;
}

export function EnvironmentFacts({ environment }: { environment: Environment | undefined }) {
  if (!environment) return <Empty>The environment was not recorded in this file.</Empty>;
  const libraries = Object.entries(environment.libraries ?? {})
    .map(([name, version]) => `${name} ${version}`)
    .join(", ");
  return (
    <Facts
      items={[
        ["Python", `${environment.implementation ?? ""} ${environment.python ?? "not recorded"}`.trim()],
        ["Platform", environment.platform ?? "not recorded"],
        ["Processor", environment.processor || environment.machine || "not recorded"],
        [
          "CPUs",
          isNumber(environment.logical_cpus)
            ? `${environment.logical_cpus} logical${isNumber(environment.physical_cpus) ? `, ${environment.physical_cpus} physical` : ""}`
            : "not recorded",
        ],
        ["Memory", formatBytes(environment.memory_bytes)],
        ["Libraries", libraries || "not recorded"],
        [
          "Code commit",
          environment.code_commit ? (
            <>
              <code title={environment.code_commit}>{shortHash(environment.code_commit)}</code>{" "}
              {environment.code_dirty === true && <Tag tone="warn">working tree had uncommitted changes</Tag>}
              {environment.code_dirty === false && "clean working tree"}
            </>
          ) : (
            "not recorded"
          ),
        ],
      ]}
    />
  );
}

export function BenchmarkFile({ benchmark }: { benchmark: Benchmark }) {
  const workloads = benchmark.workloads ?? [];
  const runs = benchmark.runs ?? [];
  const plotted = runs.filter(comparable);
  const settings = Object.entries(benchmark.settings ?? {}).filter(([, value]) => value !== null && value !== undefined);
  const machine = benchmark.environment?.processor || benchmark.environment?.platform || "machine not recorded";
  const sizes = workloads.map(sizeOf).filter(isNumber);
  const repetitions = benchmark.settings?.repetitions;
  const medianOf = isNumber(repetitions) ? ` Each value is the median of ${plural(repetitions, "repetition")}.` : "";
  const sample =
    `Simulated workloads on one machine (${machine}): ${plural(workloads.length, "workload")}` +
    (sizes.length > 0 ? ` of ${formatCount(Math.min(...sizes))} to ${formatCount(Math.max(...sizes))} event versions` : "") +
    `.${medianOf}`;
  // Why each run that is not plotted was left out, in words: "3 skipped, 1 not equivalent".
  const leftOut = new Map<string, number>();
  for (const run of runs) {
    if (comparable(run)) continue;
    const why = run.status !== "ok" ? (run.status ?? "status not recorded") : run.equivalent_to_reference === false ? "not equivalent" : "not compared";
    leftOut.set(why, (leftOut.get(why) ?? 0) + 1);
  }
  const leftOutText = [...leftOut].map(([why, count]) => `${count} ${why}`).join(", ");

  const engines = [...new Set(runs.map((run) => run.engine))].sort(
    (a, b) => (ENGINE_SLOT[a] ?? 9) - (ENGINE_SLOT[b] ?? 9) || a.localeCompare(b),
  );
  const chartEngines = engines.filter((engine) => ENGINE_SLOT[engine] !== undefined);
  const skews = [...new Set(workloads.map((workload) => workload.skew ?? "unspecified"))];

  interface EngineRow {
    workload: BenchmarkWorkload;
    engine: string;
    run: BenchmarkRun;
  }
  const engineRows = (skew: string): EngineRow[] =>
    workloads
      .filter((workload) => (workload.skew ?? "unspecified") === skew)
      .sort((a, b) => (sizeOf(a) ?? 0) - (sizeOf(b) ?? 0))
      .flatMap((workload) =>
        engines.flatMap((engine) => {
          const run = representative(plotted.filter((one) => one.workload_id === workload.workload_id && one.engine === engine));
          return run ? [{ workload, engine, run }] : [];
        }),
      );

  /* Throughput by worker count: per workload, the replay layout with the most worker counts. */
  interface WorkerRow {
    workload: BenchmarkWorkload;
    run: BenchmarkRun;
  }
  const workerSeries = workloads.map((workload) => {
    const replay = plotted.filter(
      (run) =>
        run.workload_id === workload.workload_id &&
        run.engine === "replay" &&
        isPlain(run) &&
        isNumber(run.workers) &&
        isNumber(run.throughput_events_per_second),
    );
    const partitions = [...new Set(replay.map((run) => run.partitions ?? null))];
    const best = partitions
      .map((value) => ({ value, runs: replay.filter((run) => (run.partitions ?? null) === value) }))
      .sort((a, b) => new Set(b.runs.map((run) => run.workers)).size - new Set(a.runs.map((run) => run.workers)).size || (b.value ?? 0) - (a.value ?? 0))[0];
    return { workload, partitions: best?.value ?? null, runs: [...(best?.runs ?? [])].sort((a, b) => (a.workers ?? 0) - (b.workers ?? 0)) };
  });
  const scalable = workerSeries.filter((entry) => new Set(entry.runs.map((run) => run.workers)).size >= 2);
  const throughputMax = Math.max(0, ...scalable.flatMap((entry) => entry.runs.map((run) => run.throughput_events_per_second ?? 0)));
  const workerRows: WorkerRow[] = scalable.flatMap((entry) => entry.runs.map((run) => ({ workload: entry.workload, run })));

  const speedup = (run: BenchmarkRun) => {
    if (run.equivalent_to_reference !== true) {
      return isNumber(run.speedup_vs_single_worker) ? <span className="not-estimable">not shown: output not equivalent</span> : <span className="muted">not applicable</span>;
    }
    return isNumber(run.speedup_vs_single_worker) ? `${run.speedup_vs_single_worker.toFixed(2)}x` : <span className="muted">not applicable</span>;
  };
  const seconds = (value: number | null | undefined) =>
    isNumber(value) ? formatNumber(value, value < 10 ? 3 : 2) : <span className="muted">not measured</span>;

  const runColumns: Column<BenchmarkRun>[] = [
    { key: "workload", header: "Workload", rowHeader: true, cell: (run) => <code>{run.workload_id}</code> },
    { key: "engine", header: "Engine", cell: engineText },
    { key: "workers", header: "Workers", numeric: true, cell: (run) => (isNumber(run.workers) ? run.workers : "") },
    { key: "partitions", header: "Partitions", numeric: true, cell: (run) => (isNumber(run.partitions) ? run.partitions : "") },
    {
      key: "status",
      header: "Status",
      cell: (run) =>
        run.status === "ok" ? "ok" : <Tag tone={run.status === "skipped" ? "warn" : "high"}>{run.status ?? "not recorded"}</Tag>,
    },
    { key: "median", header: "Median end to end (s)", wrapHeader: true, numeric: true, cell: (run) => seconds(run.median_end_to_end_seconds) },
    { key: "prepare", header: "Prepare (s)", numeric: true, cell: (run) => seconds(median(run.phases_seconds?.prepare ?? [])) },
    { key: "compute", header: "Compute (s)", numeric: true, cell: (run) => seconds(median(run.phases_seconds?.compute ?? [])) },
    { key: "export", header: "Export (s)", numeric: true, cell: (run) => seconds(median(run.phases_seconds?.export ?? [])) },
    { key: "repetitions", header: "Repetitions", numeric: true, cell: (run) => formatCount(run.end_to_end_seconds?.length ?? 0) },
    {
      key: "memory",
      header: "Peak memory",
      numeric: true,
      cell: (run) => (isNumber(peakBytes(run)) ? formatBytes(peakBytes(run)) : <span className="muted">not measured</span>),
    },
    { key: "rows", header: "Rows out", numeric: true, cell: (run) => (isNumber(run.rows_out) ? formatCount(run.rows_out) : <span className="muted">none</span>) },
    {
      key: "equivalent",
      header: `Equivalent to ${runs[0]?.reference_engine ?? "reference"}`,
      wrapHeader: true,
      cell: (run) =>
        run.equivalent_to_reference === true ? "yes" : run.equivalent_to_reference === false ? <Tag tone="high">NO</Tag> : <span className="muted">not compared</span>,
    },
    {
      key: "throughput",
      header: "Event versions per second", wrapHeader: true,
      numeric: true,
      cell: (run) => (isNumber(run.throughput_events_per_second) ? formatCount(run.throughput_events_per_second) : <span className="muted">not measured</span>),
    },
    { key: "speedup", header: "Speedup against one worker", wrapHeader: true, numeric: true, cell: speedup },
    { key: "reason", header: "Reason", className: "wrap", cell: (run) => run.reason ?? "" },
  ];

  return (
    <article>
      <h3>
        <code>{benchmark.file}</code>
        {benchmark.profile ? `: profile ${benchmark.profile}` : ""}
      </h3>
      <p className="muted">Generated {formatIso(benchmark.generated_at)}.</p>
      {benchmark.command && <CommandBlock command={benchmark.command} label={`benchmark file ${benchmark.file}`} />}
      <EnvironmentFacts environment={benchmark.environment} />
      {settings.length > 0 && <Facts items={settings.map(([name, value]) => [humanise(name), String(value)] as const)} />}

      <Table
        label={`Workloads of ${benchmark.file}`}
        caption="Synthetic workloads. Generation time is measured once per workload and is not part of any run."
        columns={[
          { key: "id", header: "Workload", rowHeader: true, cell: (workload) => <code>{workload.workload_id}</code> },
          { key: "skew", header: "Activity per entity", wrapHeader: true, cell: (workload) => workload.skew ?? "not recorded" },
          { key: "target", header: "Target events", numeric: true, cell: (workload) => formatCount(workload.target_events) },
          { key: "versions", header: "Event versions", numeric: true, cell: (workload) => formatCount(workload.event_versions) },
          { key: "logical", header: "Logical events", numeric: true, cell: (workload) => formatCount(workload.logical_events) },
          { key: "entities", header: "Entities", numeric: true, cell: (workload) => formatCount(workload.entities) },
          { key: "predictions", header: "Predictions", numeric: true, cell: (workload) => formatCount(workload.predictions) },
          { key: "features", header: "Features", numeric: true, cell: (workload) => formatCount(workload.features) },
          { key: "bytes", header: "Input size", numeric: true, cell: (workload) => formatBytes(inputBytes(workload)) },
          { key: "generation", header: "Generation (s)", numeric: true, cell: (workload) => formatNumber(workload.generation_seconds, 3) },
        ]}
        rows={workloads}
        rowKey={(workload) => workload.workload_id}
        empty="This benchmark file lists no workloads."
      />

      {plotted.length === 0 ? (
        <Empty>No run in this file both finished and matched the reference output, so there is nothing to chart. See the table of all runs.</Empty>
      ) : (
        <>
          <p className="muted">
            The charts plot {formatCount(plotted.length)} of {plural(runs.length, "run")}: those that finished and whose output
            matched the reference.{" "}
            {leftOutText ? `The others (${leftOutText}) are only in the table of all runs.` : "No run was left out."}{" "}
            A chart shows each engine's plain configuration with one worker; other configurations are in the tables.
          </p>
          {skews.map((skew) => {
            const rows = engineRows(skew);
            if (rows.length === 0) return null;
            const ordered = [...new Set(rows.map((row) => row.workload))];
            const timeSeries: LineSeries[] = chartEngines.map((engine) => ({
              name: engine === "replay" ? "replay (one worker)" : engine,
              slot: ENGINE_SLOT[engine] ?? 0,
              points: rows
                .filter((row) => row.engine === engine)
                .flatMap((row) => {
                  const x = sizeOf(row.workload);
                  const y = row.run.median_end_to_end_seconds;
                  return isNumber(x) && isNumber(y)
                    ? [{ x, y, title: `${engine}, ${row.workload.workload_id} (${layoutText(row.run)}): median ${formatNumber(y, 3)} s` }]
                    : [];
                }),
            }));
            const memoryGroups: BarGroup[] = chartEngines.map((engine) => {
              const values = ordered.map((workload) => {
                const bytes = rows.find((row) => row.engine === engine && row.workload === workload);
                const peak = bytes ? peakBytes(bytes.run) : null;
                return isNumber(peak) ? peak / MIB : null;
              });
              return {
                name: engine === "replay" ? "replay (one worker)" : engine,
                slot: ENGINE_SLOT[engine] ?? 0,
                values,
                titles: values.map((value, index) =>
                  value === null ? undefined : `${engine}, ${ordered[index]?.workload_id ?? ""}: peak ${formatNumber(value, 0)} MiB`,
                ),
              };
            });
            return (
              <div key={skew}>
                <h4>By engine, {skew} activity</h4>
                <div className="charts">
                  <Figure caption={`${sample} Both axes are logarithmic.`}>
                    <LineChart
                      title={`Median end-to-end time by workload size (${skew})`}
                      xLabel="event versions (log scale)"
                      yLabel="seconds (log scale)"
                      series={timeSeries}
                      xScale="log"
                      yScale="log"
                    />
                  </Figure>
                  <Figure caption={`${sample} Peak resident memory of the worker's process tree, as sampled.`}>
                    <BarChart
                      title={`Peak memory by workload size (${skew})`}
                      xLabel="event versions"
                      yLabel="peak memory (MiB)"
                      categories={ordered.map((workload) => formatCount(sizeOf(workload)))}
                      groups={memoryGroups}
                    />
                  </Figure>
                </div>
                <Table
                  label={`Median time and peak memory by engine, ${skew} activity`}
                  caption={`The numbers in the two charts above. ${sample}`}
                  columns={[
                    { key: "workload", header: "Workload", rowHeader: true, cell: (row) => <code>{row.workload.workload_id}</code> },
                    { key: "versions", header: "Event versions", numeric: true, cell: (row) => formatCount(sizeOf(row.workload)) },
                    { key: "engine", header: "Engine", cell: (row) => row.engine },
                    { key: "layout", header: "Layout", cell: (row) => layoutText(row.run) },
                    { key: "median", header: "Median end to end (s)", wrapHeader: true, numeric: true, cell: (row) => seconds(row.run.median_end_to_end_seconds) },
                    { key: "memory", header: "Peak memory", numeric: true, cell: (row) => formatBytes(peakBytes(row.run)) },
                  ]}
                  rows={rows}
                  rowKey={(row) => `${row.workload.workload_id}/${row.engine}`}
                />
                {engines.some((engine) => !chartEngines.includes(engine)) && (
                  <p className="muted">
                    Engines without a chart colour ({engines.filter((engine) => !chartEngines.includes(engine)).join(", ")}) are in
                    the tables only: a chart here carries at most three series.
                  </p>
                )}
              </div>
            );
          })}

          <h4>Replay throughput by worker count</h4>
          {scalable.length === 0 ? (
            <Empty>No workload has comparable replay runs at two or more worker counts, so there is no scaling curve to draw.</Empty>
          ) : (
            <>
              <div className="charts">
                {scalable.map((entry) => (
                  <Figure
                    key={entry.workload.workload_id}
                    caption={
                      `Simulated workload on one machine (${machine}): ${formatCount(entry.workload.event_versions)} event versions, ` +
                      `${formatCount(entry.workload.entities)} entities, ${formatCount(entry.workload.predictions)} predictions.${medianOf}`
                    }
                  >
                    <LineChart
                      title={`${entry.workload.workload_id}: replay throughput by workers${isNumber(entry.partitions) ? ` (${plural(entry.partitions, "partition")})` : ""}`}
                      xLabel="workers"
                      yLabel="event versions per second"
                      series={[
                        {
                          name: "replay",
                          slot: ENGINE_SLOT["replay"] ?? 1,
                          points: entry.runs.map((run) => ({
                            x: run.workers ?? 0,
                            y: run.throughput_events_per_second ?? 0,
                            title: `replay, ${layoutText(run)}: ${formatCount(run.throughput_events_per_second)} event versions per second`,
                          })),
                        },
                      ]}
                      xScale="ordinal"
                      yMax={throughputMax}
                      formatY={formatCount}
                      maxWidth={380}
                    />
                  </Figure>
                ))}
              </div>
              <Table
                label="Replay throughput by worker count"
                caption={`The numbers in the throughput charts. ${sample} Speedup is relative to one worker on the same partition layout.`}
                columns={[
                  { key: "workload", header: "Workload", rowHeader: true, cell: (row) => <code>{row.workload.workload_id}</code> },
                  { key: "partitions", header: "Partitions", numeric: true, cell: (row) => (isNumber(row.run.partitions) ? row.run.partitions : "") },
                  { key: "workers", header: "Workers", numeric: true, cell: (row) => (isNumber(row.run.workers) ? row.run.workers : "") },
                  { key: "throughput", header: "Event versions per second", wrapHeader: true, numeric: true, cell: (row) => formatCount(row.run.throughput_events_per_second) },
                  { key: "median", header: "Median end to end (s)", wrapHeader: true, numeric: true, cell: (row) => seconds(row.run.median_end_to_end_seconds) },
                  { key: "speedup", header: "Speedup against one worker", wrapHeader: true, numeric: true, cell: (row) => speedup(row.run) },
                ]}
                rows={workerRows}
                rowKey={(row) => `${row.workload.workload_id}/${row.run.partitions ?? "none"}/${row.run.workers ?? "none"}`}
              />
            </>
          )}
        </>
      )}

      <h4>All runs</h4>
      <Table
        label={`All runs of ${benchmark.file}`}
        dense
        caption={
          `${plural(runs.length, "run")}: ${["ok", "failed", "timeout", "skipped"]
            .map((status) => `${runs.filter((run) => run.status === status).length} ${status}`)
            .join(", ")}. Phase times are medians over the repetitions. ` +
          "A speedup is shown only where the output was equivalent to the reference. Simulated workloads."
        }
        columns={runColumns}
        rows={runs}
        rowKey={(run, index) => `${run.workload_id}/${engineText(run)}/${run.workers ?? "none"}/${run.partitions ?? "none"}/${index}`}
        empty="This benchmark file lists no runs."
      />
      {(benchmark.notes?.length ?? 0) > 0 && (
        <ul>
          {benchmark.notes?.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </article>
  );
}
