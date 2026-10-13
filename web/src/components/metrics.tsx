/** Tables and forest plots for metric estimates and paired differences, shared by two views. */

import type { ReactNode } from "react";
import type { Difference, Interval, MetricSet, Statistics } from "../api";
import { ForestPlot, type ForestEstimate, type ForestRow } from "../charts/ForestPlot";
import { Figure } from "../charts/common";
import { formatCount, formatLevel } from "../format";
import { HEADLINE_METRICS, comparisonLabel, comparisonShort, conditionLabel, metricDigits, metricLabel } from "../labels";
import { Est, Table, Tag, type Column } from "./ui";

/** "95% percentile intervals from 500 resamples of patients", read from the results file. */
export function intervalNote(bootstrap: Statistics["bootstrap"]): string {
  if (!bootstrap) return "Interval settings are not recorded in the results file.";
  const method = bootstrap.interval_method ? `${bootstrap.interval_method} ` : "";
  const resamples =
    bootstrap.replicates_requested === undefined ? "bootstrap resamples" : `${formatCount(bootstrap.replicates_requested)} bootstrap resamples`;
  return `Each cell is the estimate with its ${formatLevel(bootstrap.level)} ${method}interval, from ${resamples} of ${bootstrap.unit_resampled ?? "unit"}s.`;
}

/** Metric by condition, with intervals. */
export function ConditionsTable({
  label,
  conditions,
  metrics,
  reference,
  caption,
  firstColumn,
}: {
  label: string;
  conditions: Record<string, MetricSet>;
  metrics: readonly string[];
  /** The condition every difference is taken against; its row is marked. */
  reference?: string | undefined;
  caption: ReactNode;
  firstColumn: string;
}) {
  const rows = Object.entries(conditions);
  const columns: Column<[string, MetricSet]>[] = [
    {
      key: "condition",
      header: firstColumn,
      rowHeader: true,
      className: "wrap",
      cell: ([name]) => (
        <>
          {conditionLabel(name)} {name === reference && <Tag>reference</Tag>}
        </>
      ),
    },
    ...metrics.map(
      (metric): Column<[string, MetricSet]> => ({
        key: metric,
        header: metricLabel(metric),
        numeric: true,
        cell: ([, set]) => <Est value={set[metric]} digits={metricDigits(metric, false)} />,
      }),
    ),
  ];
  return (
    <Table
      label={label}
      caption={caption}
      columns={columns}
      rows={rows}
      rowKey={([name]) => name}
      rowClass={([name]) => (name === reference ? "reference" : undefined)}
      empty="No conditions are recorded for this comparison in the results file."
    />
  );
}

export interface DifferenceRow {
  key: string;
  /** Shown before the comparison name, to tell two weightings apart in one table. */
  group?: string;
  difference: Difference | undefined;
}

/** Paired differences with intervals: one row per comparison, one column per metric. */
export function DifferencesTable({
  label,
  rows,
  caption,
}: {
  label: string;
  rows: readonly DifferenceRow[];
  caption: ReactNode;
}) {
  const grouped = rows.some((row) => row.group !== undefined);
  const columns: Column<DifferenceRow>[] = [
    {
      key: "comparison",
      header: "Comparison",
      rowHeader: true,
      className: "wrap",
      cell: (row) => (
        <>
          {comparisonLabel(row.key)}
          {row.difference?.minuend && row.difference.subtrahend && (
            <span className="muted">
              <br />
              <code>{row.difference.minuend}</code> minus <code>{row.difference.subtrahend}</code>
            </span>
          )}
        </>
      ),
    },
    ...(grouped
      ? [{ key: "group", header: "Weighting", className: "nowrap", cell: (row: DifferenceRow) => row.group ?? "" } satisfies Column<DifferenceRow>]
      : []),
    ...HEADLINE_METRICS.map(
      (metric): Column<DifferenceRow> => ({
        key: metric,
        header: metricLabel(metric),
        numeric: true,
        cell: (row) => <Est value={row.difference?.[metric]} digits={metricDigits(metric, true)} signed />,
      }),
    ),
  ];
  return (
    <Table
      label={label}
      caption={caption}
      columns={columns}
      rows={rows}
      rowKey={(row) => `${row.group ?? ""}/${row.key}`}
      empty="No paired differences are recorded for this comparison in the results file."
    />
  );
}

function toForest(interval: Interval | undefined): ForestEstimate | null {
  if (!interval) return null;
  return { point: interval.point, lower: interval.lower, upper: interval.upper, reason: interval.reason ?? null };
}

/**
 * One small forest plot per headline metric. `sources` holds one set of
 * differences per series (for example prediction-weighted and patient-weighted).
 */
export function DifferenceForests({
  keys,
  sources,
  series,
  labels,
  titleSuffix,
  level,
  caption,
}: {
  keys: readonly string[];
  sources: readonly (Record<string, Difference> | undefined)[];
  series?: readonly string[];
  /** Row labels that differ from the usual short name of a comparison. */
  labels?: Readonly<Record<string, string>>;
  titleSuffix: string;
  level: string;
  caption: string;
}) {
  if (keys.length === 0) return null;
  return (
    <div className="charts">
      {HEADLINE_METRICS.map((metric) => {
        const rows: ForestRow[] = keys.map((key) => ({
          key,
          label: labels?.[key] ?? comparisonShort(key),
          estimates: sources.map((source) => toForest(source?.[key]?.[metric])),
        }));
        return (
          <Figure
            key={metric}
            caption={`${caption} Dot: estimate. Bar: ${level} interval. The line at zero means no difference.`}
          >
            <ForestPlot
              title={`${metricLabel(metric)}: ${titleSuffix}`}
              axisLabel={`difference in ${metricLabel(metric)}`}
              rows={rows}
              {...(series ? { series } : {})}
              digits={metricDigits(metric, true)}
              level={level}
            />
          </Figure>
        );
      })}
    </div>
  );
}
