/** View 4. Engine agreement, seeded-bug detection, crash recovery, benchmarks, and how to reproduce them. */

import type { ReactNode } from "react";
import {
  endpoints,
  isRecord,
  type Audit,
  type Engineering,
  type ParityEntry,
  type Recovery,
  type RecoveryCase,
  type RunListing,
  type SeededBug,
  type SeededBugs,
  type SqlMutation,
} from "../api";
import { BenchmarkFile, EnvironmentFacts } from "../components/Benchmarks";
import { Async, CommandBlock, Empty, Facts, NotGenerated, Panel, Table, Tag, YesNo } from "../components/ui";
import { formatCount, formatIso, formatValue, humanise, plural } from "../format";
import { useLoad } from "../hooks";
import { policyLabel } from "../labels";

export function EngineeringView({ run }: { run: RunListing | null }) {
  const engineering = useLoad(endpoints.engineering(), { cache: true });
  const audit = useLoad(run ? endpoints.audit(run.run_id) : null, { cache: true });

  const section = (render: (document: Engineering) => ReactNode) => (
    <Async load={engineering} what="the saved engineering results">
      {render}
    </Async>
  );

  return (
    <>
      <h1 tabIndex={-1}>Engineering</h1>
      <p className="lede">
        Evidence that the engines compute the same thing, that the tests catch deliberate mistakes, that a crashed replay
        resumes correctly, and how long the engines take. All of it on synthetic data, on one machine.
      </p>

      <Panel title="Engine agreement on the selected run">
        {run === null ? (
          <Empty>No run is selected, so there is no engine comparison to show. The sections below do not depend on a run.</Empty>
        ) : (
          <Async load={audit} what="the audit results">
            {(document) => <Parity audit={document} runId={run.run_id} />}
          </Async>
        )}
      </Panel>

      <Panel title="Seeded bugs: does the validation suite catch deliberate mistakes?">
        {section((document) =>
          document.seeded_bugs ? (
            <SeededBugResults report={document.seeded_bugs} />
          ) : (
            <NotGenerated what="Seeded-bug results" file="results/validation/seeded_bugs.json" command={document.commands?.["seeded_bugs"]} />
          ),
        )}
      </Panel>

      <Panel title="Crash and restart">
        {section((document) =>
          document.recovery ? (
            <RecoveryResults report={document.recovery} />
          ) : (
            <NotGenerated what="Crash and restart outcomes" file="results/validation/recovery.json" command={document.commands?.["recovery"]} />
          ),
        )}
      </Panel>

      <Panel title="Benchmarks">
        {section((document) =>
          (document.benchmarks?.length ?? 0) > 0 ? (
            document.benchmarks?.map((benchmark) => <BenchmarkFile key={benchmark.file} benchmark={benchmark} />)
          ) : (
            <NotGenerated what="Benchmark results" file="results/benchmarks/*.json" command={document.commands?.["benchmarks"]} />
          ),
        )}
      </Panel>

      <Panel title="Reproduction commands">
        {section((document) => {
          const commands = Object.entries(document.commands ?? {});
          if (commands.length === 0) return <Empty>The API did not list any reproduction commands.</Empty>;
          return (
            <>
              <p>Run these from the repository root. They are the exact commands the API reports for regenerating each result.</p>
              {commands.map(([name, command]) => (
                <div key={name}>
                  <h3>{humanise(name)}</h3>
                  <CommandBlock command={command} label={humanise(name)} />
                </div>
              ))}
            </>
          );
        })}
      </Panel>
    </>
  );
}

/* ------------------------------------------------------------------ parity */

function Parity({ audit, runId }: { audit: Audit; runId: string }) {
  interface Row {
    group: string;
    policy: string;
    entry: ParityEntry;
  }
  const rows: Row[] = Object.entries(audit.parity ?? {}).flatMap(([group, byPolicy]) =>
    Object.entries(byPolicy).map(([policy, entry]) => ({ group, policy, entry })),
  );
  const agreeing = rows.filter((row) => row.entry.equivalent).length;
  const describe = (row: Row) => {
    const pair = row.entry.left && row.entry.right ? `${row.entry.left} against ${row.entry.right}` : humanise(row.group);
    return row.entry.patients === undefined ? `${pair}, all predictions` : `${pair}, a sample of ${plural(row.entry.patients, "patient")}`;
  };
  return (
    <>
      <p>
        The same data were run through more than one engine. Identifiers, counts and last values must match exactly;
        means and elapsed times must match within the tolerance stated in the temporal contract (section 7).
      </p>
      {rows.length > 0 && (
        <p>
          <strong>
            {agreeing} of {plural(rows.length, "comparison")} agree
          </strong>{" "}
          for run <code>{runId}</code>.
        </p>
      )}
      <Table
        label="Engine agreement on the selected run"
        caption={`Simulated data, run ${runId}. Rows compared are predictions; the largest difference is over cells that passed the tolerance.`}
        columns={[
          { key: "comparison", header: "Comparison", rowHeader: true, cell: describe },
          { key: "policy", header: "Policy", cell: (row) => policyLabel(row.policy) },
          {
            key: "rows",
            header: "Predictions compared", wrapHeader: true,
            numeric: true,
            cell: (row) =>
              row.entry.rows_left === row.entry.rows_right
                ? formatCount(row.entry.rows_left)
                : `${formatCount(row.entry.rows_left)} against ${formatCount(row.entry.rows_right)}`,
          },
          { key: "mismatches", header: "Cells that disagree", wrapHeader: true, numeric: true, cell: (row) => formatCount(row.entry.n_mismatches) },
          {
            key: "difference",
            header: "Largest difference within tolerance", wrapHeader: true,
            numeric: true,
            cell: (row) =>
              typeof row.entry.max_abs_difference_within_tolerance === "number"
                ? row.entry.max_abs_difference_within_tolerance.toExponential(2)
                : "not recorded",
          },
          { key: "result", header: "Result", cell: (row) => (row.entry.equivalent ? "agree" : <Tag tone="high">DISAGREE</Tag>) },
        ]}
        rows={rows}
        rowKey={(row) => `${row.group}/${row.policy}`}
        empty="No engine comparisons were recorded for this run."
      />
    </>
  );
}

/* ------------------------------------------------------------- seeded bugs */

function Fixtures({ names }: { names: readonly string[] | undefined }) {
  if (!names || names.length === 0) return <Tag tone="high">none</Tag>;
  return (
    <details>
      <summary>{plural(names.length, "fixture")}</summary>
      {names.map((name) => (
        <code key={name}>{name} </code>
      ))}
    </details>
  );
}

function SeededBugResults({ report }: { report: SeededBugs }) {
  const bugs = Object.entries(report.python_bugs ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const mutations = Object.entries(report.sql_mutations ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const budget = report.archive_budget_per_trial;
  const everyTrial = bugs.filter(([, bug]) => bug.discovery?.trials !== undefined && bug.discovery.found_in_trials === bug.discovery.trials).length;
  const slowest = Math.max(...bugs.map(([, bug]) => bug.discovery?.worst ?? Number.NEGATIVE_INFINITY));
  const exposedMutations = mutations.filter(([, mutation]) => (mutation.fixtures_that_expose_it?.length ?? 0) > 0).length;
  const archives = (value: number | null | undefined) =>
    typeof value === "number" ? (
      formatValue(value, 1)
    ) : (
      <span className="not-estimable">not found within the budget{budget === undefined ? "" : ` of ${formatCount(budget)} archives`}</span>
    );

  return (
    <>
      {report.scope && <p>{report.scope}</p>}
      <p>
        <strong>
          {everyTrial} of {plural(bugs.length, "seeded bug")} were exposed in every random trial
        </strong>
        {Number.isFinite(slowest) && `; the slowest first exposure took ${formatCount(slowest)} generated archives`}
        {budget !== undefined && ` (budget ${formatCount(budget)} per trial)`}. {exposedMutations} of{" "}
        {plural(mutations.length, "SQL mutation")} are exposed by at least one handwritten fixture.
      </p>
      {report.control && (
        <p>
          Control (the correct implementation, run through the same harness): contradicted by{" "}
          {plural(report.control.fixtures_that_contradict_it?.length ?? 0, "fixture")}; exposed within the budget:{" "}
          <YesNo value={report.control.exposed_within_budget} concern="yes" />.
        </p>
      )}
      <p className="muted">Generated {formatIso(report.generated_at)}.</p>
      {report.command && <CommandBlock command={report.command} label="the seeded-bug report" />}

      <Table<[string, SeededBug]>
        label="Seeded Python bugs and how the suite finds them"
        caption={`${plural(bugs.length, "deliberately wrong implementation")}. Archives are synthetic. "Found in" counts independent random trials; median and worst are generated archives until the first one that exposes the bug.`}
        columns={[
          { key: "bug", header: "Bug", rowHeader: true, cell: ([name]) => <code>{name}</code> },
          { key: "wrong", header: "What it gets wrong", className: "wrap", cell: ([, bug]) => bug.what_it_gets_wrong ?? "" },
          { key: "fixtures", header: "Fixtures that expose it", wrapHeader: true, cell: ([, bug]) => <Fixtures names={bug.fixtures_that_expose_it} /> },
          {
            key: "found",
            header: "Found in random trials", wrapHeader: true,
            numeric: true,
            cell: ([, bug]) =>
              bug.discovery?.trials === undefined ? (
                <span className="not-estimable">not recorded</span>
              ) : (
                `${formatCount(bug.discovery.found_in_trials)} of ${formatCount(bug.discovery.trials)}`
              ),
          },
          { key: "median", header: "Median archives needed", wrapHeader: true, numeric: true, cell: ([, bug]) => archives(bug.discovery?.median) },
          { key: "worst", header: "Worst case", wrapHeader: true, numeric: true, cell: ([, bug]) => archives(bug.discovery?.worst) },
        ]}
        rows={bugs}
        rowKey={([name]) => name}
        empty="The report lists no seeded Python bugs."
      />

      <h3>SQL mutations of the batch query</h3>
      <Table<[string, SqlMutation]>
        label="One-line SQL mutations and the fixtures that expose them"
        caption={`${plural(mutations.length, "one-line mutation")} of the batch backend's query, each checked against the handwritten fixtures.`}
        columns={[
          { key: "mutation", header: "Mutation", rowHeader: true, cell: ([name]) => <code>{name}</code> },
          { key: "replaced", header: "Replaced", className: "wrap", cell: ([, mutation]) => <code>{mutation.replaced ?? ""}</code> },
          { key: "with", header: "With", className: "wrap", cell: ([, mutation]) => <code>{mutation.with ?? ""}</code> },
          { key: "fixtures", header: "Fixtures that expose it", wrapHeader: true, cell: ([, mutation]) => <Fixtures names={mutation.fixtures_that_expose_it} /> },
        ]}
        rows={mutations}
        rowKey={([name]) => name}
        empty="The report lists no SQL mutations."
      />
      <details>
        <summary>Environment of the seeded-bug run</summary>
        <EnvironmentFacts environment={report.environment} />
      </details>
    </>
  );
}

/* ---------------------------------------------------------------- recovery */

function RecoveryResults({ report }: { report: Recovery }) {
  const cases = report.cases ?? [];
  const recovered = cases.filter((one) => one.outputs_equal_uninterrupted === true).length;
  // The workload is described either in words or as named settings.
  const settings: [string, string][] = isRecord(report.workload)
    ? Object.entries(report.workload).map(([name, value]) => [
        humanise(name),
        typeof value === "number" ? formatValue(value, 4) : typeof value === "string" ? value : JSON.stringify(value),
      ])
    : [];
  const blank = <span className="muted">not recorded</span>;
  return (
    <>
      {report.scope && (
        <p>
          <strong>Scope.</strong> {report.scope}
        </p>
      )}
      <p>
        <strong>
          {recovered} of {plural(cases.length, "crash case")} resumed with outputs equal to an uninterrupted run.
        </strong>{" "}
        All recovered, as recorded in the file: <YesNo value={report.all_recovered} concern="no" />.
      </p>
      <p className="muted">Generated {formatIso(report.generated_at)}.</p>
      {report.command && <CommandBlock command={report.command} label="the recovery report" />}
      {typeof report.workload === "string" && <p>Workload: {report.workload}</p>}
      {settings.length > 0 && (
        <details>
          <summary>Workload settings ({settings.length})</summary>
          <Facts items={settings} />
        </details>
      )}
      <Table<RecoveryCase>
        label="Crash and restart cases"
        caption={`${plural(cases.length, "case")}. Synthetic workload on one machine; see the scope above.`}
        columns={[
          {
            key: "point",
            header: "Crash point",
            rowHeader: true,
            className: "wrap",
            cell: (one) => {
              if (one.crash_point === null || one.crash_point === undefined) return blank;
              const meaning = report.crash_points?.[String(one.crash_point)];
              return (
                <>
                  <code>{one.crash_point}</code>
                  {meaning && (
                    <>
                      <br />
                      <span className="muted">{meaning}</span>
                    </>
                  )}
                </>
              );
            },
          },
          { key: "chunk", header: "Chunk", numeric: true, cell: (one) => one.chunk ?? blank },
          { key: "how", header: "How it was stopped", wrapHeader: true, className: "wrap", cell: (one) => one.how ?? blank },
          { key: "exit", header: "Exit code", numeric: true, cell: (one) => one.exit_code ?? blank },
          {
            key: "offset",
            header: "Resumed from offset", wrapHeader: true,
            numeric: true,
            cell: (one) => (typeof one.resumed_from_offset === "number" ? formatCount(one.resumed_from_offset) : blank),
          },
          {
            key: "equal",
            header: "Outputs equal to an uninterrupted run", wrapHeader: true,
            cell: (one) =>
              one.outputs_equal_uninterrupted === true ? "yes" : one.outputs_equal_uninterrupted === false ? <Tag tone="high">NO</Tag> : blank,
          },
          {
            key: "predictions",
            header: "Predictions",
            numeric: true,
            cell: (one) => (typeof one.predictions === "number" ? formatCount(one.predictions) : blank),
          },
        ]}
        rows={cases}
        rowKey={(one, index) => `${String(one.crash_point)}/${String(one.chunk)}/${index}`}
        empty="The recovery report lists no cases."
      />
      {report.environment && (
        <details>
          <summary>Environment of the recovery run</summary>
          <EnvironmentFacts environment={report.environment} />
        </details>
      )}
    </>
  );
}
