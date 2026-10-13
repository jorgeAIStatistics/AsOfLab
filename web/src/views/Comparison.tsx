/** View 3. Paired metric differences with intervals, and the as-known and naive features side by side. */

import { useState, type MouseEvent } from "react";
import {
  endpoints,
  type Comparison,
  type ComparisonRow,
  type ExperimentSummary,
  type MetricSet,
  type RunListing,
  type ScenarioSummary,
  type Spread,
  type Statistics,
} from "../api";
import { ConditionsTable, DifferenceForests, DifferencesTable, intervalNote, type DifferenceRow } from "../components/metrics";
import {
  Async,
  Empty,
  ErrorNotice,
  Est,
  Loading,
  NoPatientData,
  NotGenerated,
  Panel,
  Table,
  YesNo,
  sampleText,
  type Column,
} from "../components/ui";
import { formatCount, formatIso, formatLevel, formatNumber, formatPercent, formatValue, plural } from "../format";
import { formatHash, useLoad, type Navigate, type Route } from "../hooks";
import {
  DECOMPOSITION_KEYS,
  HEADLINE_METRICS,
  TOTAL_GAP_KEY,
  comparisonLabel,
  conditionLabel,
  metricDigits,
  metricLabel,
} from "../labels";

interface ViewProps {
  run: RunListing;
  route: Route;
  navigate: Navigate;
}

export function ComparisonView({ run, route, navigate }: ViewProps) {
  const statistics = useLoad(endpoints.statistics(run.run_id), { cache: true });
  return (
    <>
      <h1 tabIndex={-1}>Comparison</h1>
      <p className="lede">
        How much the evaluation of one simulated cohort moves when only the handling of time changes: first as paired
        differences in the metrics, then prediction by prediction.
      </p>
      <Async load={statistics} what="the computed statistics">
        {(document) => <MetricDifferences statistics={document} />}
      </Async>
      <Panel title="Across seeds: the same contrasts in other simulated cohorts">
        <CrossSeed scenario={run.scenario} />
      </Panel>
      <Panel title="Prediction by prediction: as-known and naive features">
        <AlignedTable key={run.run_id} run={run} route={route} navigate={navigate} />
      </Panel>
    </>
  );
}

/* ------------------------------------------------ (a) metric differences */

const WEIGHTINGS = ["Prediction-weighted", "Patient-weighted"] as const;

function MetricDifferences({ statistics }: { statistics: Statistics }) {
  const a = statistics.experiment_a;
  const b = statistics.experiment_b;
  const level = formatLevel(statistics.bootstrap?.level);
  const intervals = intervalNote(statistics.bootstrap);

  const byPrediction = a?.prediction_weighted?.differences;
  const byPatient = a?.patient_weighted?.differences;
  const allKeys = [...new Set([...Object.keys(byPrediction ?? {}), ...Object.keys(byPatient ?? {})])];
  const referenceKeys = allKeys.filter((key) => !DECOMPOSITION_KEYS.includes(key));
  const decompositionKeys = [...DECOMPOSITION_KEYS.filter((key) => allKeys.includes(key)), ...(allKeys.includes(TOTAL_GAP_KEY) ? [TOTAL_GAP_KEY] : [])];
  const bothWeightings = (keys: readonly string[]): DifferenceRow[] =>
    keys.flatMap((key) => [
      { key, group: WEIGHTINGS[0], difference: byPrediction?.[key] },
      { key, group: WEIGHTINGS[1], difference: byPatient?.[key] },
    ]);

  const bDifferences = b?.prediction_weighted?.differences ?? {};
  const bKeys = Object.keys(bDifferences);

  return (
    <>
      <Panel title="Experiment A: paired differences against the reference policy">
        {!a ? (
          <Empty>This run's statistics file has no experiment A section.</Empty>
        ) : (
          <>
            {a.question && <p>{a.question}</p>}
            {a.interpretation && <p>{a.interpretation}</p>}
            {statistics.weighting && <p className="muted">{statistics.weighting} Patient-weighted: every patient has equal total weight.</p>}
            <DifferenceForests
              keys={referenceKeys}
              sources={[byPrediction, byPatient]}
              series={WEIGHTINGS}
              titleSuffix="policy minus as-known"
              level={level}
              caption={sampleText(a.sample)}
            />
            <DifferencesTable
              label="Experiment A: paired differences, prediction-weighted and patient-weighted"
              rows={bothWeightings(referenceKeys)}
              caption={`${sampleText(a.sample)} ${intervals}`}
            />
          </>
        )}
      </Panel>

      <Panel title="Experiment A: revision effect and availability effect">
        {!a || decompositionKeys.length === 0 ? (
          <Empty>This run's statistics file has no revision and availability decomposition.</Empty>
        ) : (
          <>
            {a.decomposition_note && <p>{a.decomposition_note}</p>}
            <DifferenceForests
              keys={decompositionKeys}
              sources={[byPrediction, byPatient]}
              series={WEIGHTINGS}
              labels={{ [TOTAL_GAP_KEY]: "Sum: naive minus as-known" }}
              titleSuffix="parts of the naive gap"
              level={level}
              caption={sampleText(a.sample)}
            />
            <DifferencesTable
              label="Experiment A: revision effect, availability effect, and their sum"
              rows={bothWeightings(decompositionKeys)}
              caption={`${sampleText(a.sample)} ${intervals}`}
            />
          </>
        )}
      </Panel>

      <Panel title="Experiment B: two training pipelines, evaluated both ways">
        {!b ? (
          <Empty>This run's statistics file has no experiment B section.</Empty>
        ) : (
          <>
            {b.question && <p>{b.question}</p>}
            {b.design && <p>{b.design}</p>}
            <TwoByTwo conditions={b.prediction_weighted?.conditions ?? {}} caption={`${sampleText(b.sample)} ${intervals}`} />
            {b.deployment_faithful_cell && <p className="muted">Deployment-faithful: {b.deployment_faithful_cell}.</p>}
            <h3>Contrasts between the cells</h3>
            <DifferenceForests keys={bKeys} sources={[bDifferences]} titleSuffix="contrast" level={level} caption={sampleText(b.sample)} />
            <DifferencesTable
              label="Experiment B: contrasts between the cells"
              rows={bKeys.map((key) => ({ key, difference: bDifferences[key] }))}
              caption={`${sampleText(b.sample)} Prediction-weighted. ${intervals}`}
            />
            {b.can_attribute && (
              <p>
                <strong>What this can attribute.</strong> {b.can_attribute}
              </p>
            )}
            {b.cannot_attribute && (
              <p>
                <strong>What it cannot.</strong> {b.cannot_attribute}
              </p>
            )}
          </>
        )}
      </Panel>
    </>
  );
}

/** Experiment B as a table of training policy by evaluation policy. Falls back to a list for other key shapes. */
function TwoByTwo({ conditions, caption }: { conditions: Record<string, MetricSet>; caption: string }) {
  const cells = Object.keys(conditions).map((key) => {
    const [trained, evaluated, ...rest] = key.split("__");
    return { key, trained, evaluated, regular: trained !== undefined && evaluated !== undefined && rest.length === 0 };
  });
  const metrics = HEADLINE_METRICS.filter((metric) => Object.values(conditions).some((set) => set[metric] !== undefined));
  if (cells.length === 0 || cells.some((cell) => !cell.regular)) {
    return (
      <ConditionsTable
        label="Experiment B: metric by model and evaluation"
        firstColumn="Model and evaluation"
        conditions={conditions}
        metrics={metrics}
        caption={caption}
      />
    );
  }
  const trainedLevels = [...new Set(cells.map((cell) => cell.trained ?? ""))];
  const evaluatedLevels = [...new Set(cells.map((cell) => cell.evaluated ?? ""))];
  const columns: Column<string>[] = [
    { key: "trained", header: "Model", rowHeader: true, cell: (trained) => conditionLabel(trained) },
    ...evaluatedLevels.map(
      (evaluated): Column<string> => ({
        key: evaluated,
        header: conditionLabel(evaluated),
        cell: (trained) => {
          const set = conditions[`${trained}__${evaluated}`];
          if (!set) return <span className="not-estimable">not estimable: this cell was not computed</span>;
          return (
            <div className="cell-2x2">
              {metrics.map((metric) => (
                <div key={metric}>
                  <span className="metric">{metricLabel(metric)}</span> <Est value={set[metric]} digits={metricDigits(metric, false)} />
                </div>
              ))}
            </div>
          );
        },
      }),
    ),
  ];
  return (
    <Table
      label="Experiment B: training policy by evaluation policy"
      caption={caption}
      columns={columns}
      rows={trainedLevels}
      rowKey={(trained) => trained}
    />
  );
}

/* ----------------------------------------------------- cross-seed summary */

const MATRIX_COMMAND = "uv run asoflab experiment matrix --out results/experiments";

function CrossSeed({ scenario }: { scenario: string | null }) {
  const engineering = useLoad(endpoints.engineering(), { cache: true });
  return (
    <Async load={engineering} what="the cross-seed summary">
      {(document) => {
        const summary = document.experiment_summary;
        if (!summary || Object.keys(summary.scenarios ?? {}).length === 0) {
          const named = document.commands?.["experiment_summary"] ?? document.commands?.["experiments"];
          return (
            <>
              <NotGenerated what="Cross-seed summaries" file="results/experiments/summary.json" command={named ?? MATRIX_COMMAND} />
              {!named && (
                <p className="muted">
                  The API lists no command for this file. The one shown is the CLI command that runs every prespecified
                  scenario with every prespecified seed and then writes the summary.
                </p>
              )}
            </>
          );
        }
        return <CrossSeedTables summary={summary} preferred={scenario} />;
      }}
    </Async>
  );
}

function CrossSeedTables({ summary, preferred }: { summary: ExperimentSummary; preferred: string | null }) {
  const scenarios = summary.scenarios ?? {};
  const names = Object.keys(scenarios);
  const [chosen, setChosen] = useState<string | null>(null);
  const name = chosen ?? (preferred !== null && names.includes(preferred) ? preferred : names[0]) ?? "";
  const scenario: ScenarioSummary | undefined = scenarios[name];
  const seeds = scenario?.per_seed ?? [];

  interface ContrastRow {
    contrast: string;
    metric: string;
    spread: Spread | null | undefined;
  }
  const rows: ContrastRow[] = Object.entries(scenario?.across_seeds?.contrasts ?? {}).flatMap(([contrast, byMetric]) =>
    HEADLINE_METRICS.map((metric) => ({ contrast, metric, spread: byMetric[metric] })),
  );
  const number = (row: ContrastRow, value: number | null | undefined) =>
    row.spread ? formatNumber(value, metricDigits(row.metric, true), true) : "";
  const sample = `Simulated data. ${plural(seeds.length, "seed")} of scenario ${name}; ${formatCount(summary.runs_included)} runs in the summary.`;

  return (
    <>
      {summary.interpretation && <p>{summary.interpretation}</p>}
      <p className="muted">
        {summary.selection} Generated {formatIso(summary.generated_at)}.
      </p>
      {names.length > 1 && (
        <div className="controls">
          <label className="field">
            <span>Scenario</span>
            <select value={name} onChange={(event) => setChosen(event.target.value)}>
              {names.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
      {preferred !== null && !names.includes(preferred) && (
        <p className="muted">
          The selected run's scenario (<code>{preferred}</code>) is not in the summary; showing <code>{name}</code>.
        </p>
      )}
      {scenario?.description && <p>{scenario.description}</p>}
      <Table
        label="Contrasts across seeds"
        caption={`${sample} Mean and spread of the point estimates across seeds; not a confidence interval.`}
        columns={[
          { key: "contrast", header: "Contrast", rowHeader: true, className: "wrap", cell: (row) => comparisonLabel(row.contrast) },
          { key: "metric", header: "Metric", className: "nowrap", cell: (row) => metricLabel(row.metric) },
          {
            key: "seeds",
            header: "Seeds",
            numeric: true,
            cell: (row) => (row.spread ? formatCount(row.spread.seeds) : <span className="not-estimable">not estimable: no seed has an estimate</span>),
          },
          { key: "mean", header: "Mean", numeric: true, cell: (row) => number(row, row.spread?.mean) },
          {
            key: "sd",
            header: "SD",
            numeric: true,
            cell: (row) =>
              !row.spread ? (
                ""
              ) : typeof row.spread.sd === "number" ? (
                formatNumber(row.spread.sd, metricDigits(row.metric, true))
              ) : (
                <span className="not-estimable">not estimable: needs at least two seeds</span>
              ),
          },
          { key: "min", header: "Min", numeric: true, cell: (row) => number(row, row.spread?.min) },
          { key: "max", header: "Max", numeric: true, cell: (row) => number(row, row.spread?.max) },
          { key: "above", header: "Intervals above 0", wrapHeader: true, numeric: true, cell: (row) => (row.spread ? formatCount(row.spread.interval_above_zero) : "") },
          { key: "below", header: "Below 0", wrapHeader: true, numeric: true, cell: (row) => (row.spread ? formatCount(row.spread.interval_below_zero) : "") },
          { key: "includes", header: "Including 0", wrapHeader: true, numeric: true, cell: (row) => (row.spread ? formatCount(row.spread.interval_includes_zero) : "") },
          { key: "none", header: "No interval", wrapHeader: true, numeric: true, cell: (row) => (row.spread ? formatCount(row.spread.no_interval) : "") },
        ]}
        rows={rows}
        rowKey={(row) => `${row.contrast}/${row.metric}`}
        empty="The summary has no contrasts for this scenario."
      />
      <h3>Each seed</h3>
      <Table
        label="Per-seed results"
        caption={`${sample} One row per simulated cohort, with its own fitted models.`}
        columns={[
          { key: "seed", header: "Seed", rowHeader: true, cell: (seed) => seed.seed ?? "not recorded" },
          { key: "run", header: "Run", cell: (seed) => <code>{seed.run ?? "not recorded"}</code> },
          { key: "patients", header: "Test patients", wrapHeader: true, numeric: true, cell: (seed) => formatCount(seed.test_sample?.patients) },
          { key: "predictions", header: "Predictions", numeric: true, cell: (seed) => formatCount(seed.test_sample?.predictions) },
          { key: "events", header: "Events", numeric: true, cell: (seed) => formatCount(seed.test_sample?.events) },
          { key: "auroc", header: "Reference AUROC", wrapHeader: true, numeric: true, cell: (seed) => <Est value={seed.reference_metrics?.["auroc"]} /> },
          {
            key: "gap",
            header: "Naive minus as-known AUROC", wrapHeader: true,
            numeric: true,
            cell: (seed) => <Est value={seed.contrasts?.["naive_minus_as_known"]?.["auroc"]} digits={4} signed />,
          },
          {
            key: "findings",
            header: "Predictions with a finding", wrapHeader: true,
            numeric: true,
            cell: (seed) => formatPercent(seed.share_of_predictions_with_a_finding),
          },
          { key: "engines", header: "Engines agree", wrapHeader: true, cell: (seed) => <YesNo value={seed.engines_agree} concern="no" /> },
        ]}
        rows={seeds}
        rowKey={(seed, index) => `${seed.seed ?? index}`}
        empty="The summary lists no seeds for this scenario."
      />
    </>
  );
}

/* --------------------------------------------------- (b) aligned features */

const PAGE_SIZES = [25, 50, 100];

function AlignedTable({ run, route, navigate }: ViewProps) {
  const onlyChanged = route.params["changed"] !== "0";
  const requestedLimit = Number(route.params["limit"]);
  const limit = PAGE_SIZES.includes(requestedLimit) ? requestedLimit : 50;
  const requestedOffset = Number(route.params["offset"]);
  const offset = Number.isInteger(requestedOffset) && requestedOffset > 0 ? requestedOffset : 0;
  const comparison = useLoad(run.has_patient_data ? endpoints.comparison(run.run_id, { onlyChanged, offset, limit }) : null, {
    keepPrevious: true,
  });
  const update = (next: { onlyChanged?: boolean; offset?: number; limit?: number }) => {
    const changed = next.onlyChanged ?? onlyChanged;
    navigate(
      "comparison",
      {
        run: run.run_id,
        changed: changed ? undefined : "0",
        offset: String(next.offset ?? offset),
        limit: String(next.limit ?? limit),
      },
      "replace",
    );
  };

  if (!run.has_patient_data) return <NoPatientData what="The prediction-by-prediction table" />;
  if (comparison.error) {
    return comparison.error.status === 404 ? (
      <NoPatientData what="The prediction-by-prediction table" error={comparison.error} />
    ) : (
      <ErrorNotice error={comparison.error} what="the prediction-by-prediction comparison" retry={comparison.retry} />
    );
  }
  const data = comparison.data;
  if (data === undefined) return <Loading what="the prediction-by-prediction comparison" />;

  const first = data.rows.length === 0 ? 0 : data.offset + 1;
  const last = data.offset + data.rows.length;
  const open = (row: ComparisonRow) =>
    ({ run: run.run_id, patient: row.entity_id, t: row.prediction_time, policy: data.candidate_policy }) as const;

  return (
    <>
      <p>
        One row per prediction. A cell shows the feature value under <code>{data.reference_policy}</code>; where the value
        under <code>{data.candidate_policy}</code> differs, the cell is marked ≠ and shows both, as-known first. Risks
        come from the {data.risk_model}. Select a row to open that patient's timeline at that prediction time.
      </p>
      <div className="controls">
        <label className="check">
          <input type="checkbox" checked={onlyChanged} onChange={(event) => update({ onlyChanged: event.target.checked, offset: 0 })} />
          Only predictions whose features changed
        </label>
        <label className="field">
          <span>Rows per page</span>
          <select value={String(limit)} onChange={(event) => update({ limit: Number(event.target.value), offset: 0 })}>
            {PAGE_SIZES.map((size) => (
              <option key={size} value={String(size)}>
                {size}
              </option>
            ))}
          </select>
        </label>
      </div>
      <Pager
        summary={
          `Rows ${formatCount(first)} to ${formatCount(last)} of ${formatCount(data.predictions_matching)} ` +
          `${onlyChanged ? "predictions whose features changed" : "predictions"} ` +
          `(${formatCount(data.predictions_total)} predictions in this run). Simulated data.`
        }
        pending={comparison.pending}
        onPrevious={data.offset > 0 ? () => update({ offset: Math.max(0, data.offset - limit) }) : undefined}
        onNext={last < data.predictions_matching ? () => update({ offset: data.offset + limit }) : undefined}
      />
      <FeatureComparisonTable data={data} href={(row) => formatHash("timeline", open(row))} onOpen={(row) => navigate("timeline", open(row))} />
    </>
  );
}

function Pager({
  summary,
  pending,
  onPrevious,
  onNext,
}: {
  summary: string;
  pending: boolean;
  onPrevious: (() => void) | undefined;
  onNext: (() => void) | undefined;
}) {
  return (
    <div className="pager">
      <span className="button-row">
        <button type="button" disabled={!onPrevious} onClick={onPrevious}>
          Previous page
        </button>
        <button type="button" disabled={!onNext} onClick={onNext}>
          Next page
        </button>
      </span>
      <span role="status" aria-live="polite">
        {pending ? "Loading rows…" : summary}
      </span>
    </div>
  );
}

function FeatureComparisonTable({
  data,
  href,
  onOpen,
}: {
  data: Comparison;
  href: (row: ComparisonRow) => string;
  onOpen: (row: ComparisonRow) => void;
}) {
  const show = (value: number | null | undefined) => (value === null || value === undefined ? "missing" : formatValue(value, 3));
  const columns: Column<ComparisonRow>[] = [
    {
      key: "prediction",
      header: "Prediction",
      rowHeader: true,
      className: "nowrap",
      cell: (row) => (
        <a href={href(row)} title="Open this patient's timeline at this prediction time">
          <code>{row.prediction_id}</code>
        </a>
      ),
    },
    { key: "time", header: "Prediction time (UTC)", className: "nowrap", cell: (row) => formatIso(row.prediction_time, false) },
    { key: "split", header: "Split", cell: (row) => row.split ?? "none" },
    { key: "label", header: "Label", cell: (row) => row.label_status },
    { key: "changed", header: "Features changed", wrapHeader: true, numeric: true, cell: (row) => formatCount(row.features_changed) },
    ...data.feature_names.map(
      (name): Column<ComparisonRow> => ({
        key: name,
        header: name,
        numeric: true,
        cellClass: (row) => (row.changed.includes(name) ? "changed" : undefined),
        cell: (row) => {
          const asKnown = show(row.as_known[name]);
          if (!row.changed.includes(name)) return asKnown;
          const naive = show(row.naive[name]);
          return (
            <>
              <span aria-hidden="true">
                <span className="mark">≠</span>
                {asKnown} → {naive}
              </span>
              <span className="visually-hidden">
                changed: {asKnown} as known, {naive} naive
              </span>
            </>
          );
        },
      }),
    ),
    { key: "risk_as_known", header: "Risk, as-known features", wrapHeader: true, numeric: true, cell: (row) => formatNumber(row.risk_as_known, 4) },
    { key: "risk_naive", header: "Risk, naive features", wrapHeader: true, numeric: true, cell: (row) => formatNumber(row.risk_naive, 4) },
    {
      key: "risk_difference",
      header: "Naive minus as-known", wrapHeader: true,
      numeric: true,
      cell: (row) =>
        row.risk_as_known === null || row.risk_naive === null ? formatNumber(null) : formatNumber(row.risk_naive - row.risk_as_known, 4, true),
    },
  ];
  return (
    <Table
      label="As-known and naive feature values and predicted risks, one row per prediction"
      dense
      columns={columns}
      rows={data.rows}
      rowKey={(row) => row.prediction_id}
      rowClass={() => "link"}
      rowProps={(row) => ({
        onClick: (event: MouseEvent<HTMLTableRowElement>) => {
          // The link in the first cell already navigates; anything else in the row opens the same place.
          if (event.target instanceof Element && event.target.closest("a, button")) return;
          onOpen(row);
        },
      })}
      empty="No prediction in this run has a feature that differs between the two policies. Clear the filter to list every prediction."
    />
  );
}
