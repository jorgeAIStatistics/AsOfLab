import { formatNumber } from "../format";
import { formatTick, linear, niceTicks, tickStep } from "../scale";
import { Legend, layoutLegend, useChartWidth, wrapWords, type LegendEntry } from "./common";

export interface ForestEstimate {
  point: number | null;
  lower: number | null;
  upper: number | null;
  reason?: string | null;
}

export interface ForestRow {
  key: string;
  label: string;
  /** One estimate per series. A missing entry is drawn as "not estimable". */
  estimates: readonly (ForestEstimate | null | undefined)[];
}

interface ForestPlotProps {
  title: string;
  axisLabel: string;
  rows: readonly ForestRow[];
  /** Names of the estimates in a row, when a row carries more than one. */
  series?: readonly string[];
  /** Decimals in the tooltips. */
  digits?: number;
  /** Interval level for the tooltips, for example "95%". */
  level?: string;
  maxWidth?: number;
}

const LABEL_LINE = 15;
const TRACK = 17;
const SIDE = 30;

function Marker({ x, y, hollow }: { x: number; y: number; hollow: boolean }) {
  if (!hollow) return <circle cx={x} cy={y} r={5.5} fill="var(--series-1)" stroke="var(--surface)" strokeWidth={2} />;
  return (
    <>
      <circle cx={x} cy={y} r={6.5} fill="var(--surface)" />
      <circle cx={x} cy={y} r={4} fill="var(--surface)" stroke="var(--series-1)" strokeWidth={2} />
    </>
  );
}

const isNumber = (value: number | null | undefined): value is number =>
  typeof value === "number" && Number.isFinite(value);

/**
 * Point estimates with interval whiskers on one shared axis; one row per comparison.
 * The vertical line at zero is "no difference", the value every row is read against.
 */
export function ForestPlot({
  title,
  axisLabel,
  rows,
  series = [],
  digits = 4,
  level = "",
  maxWidth = 440,
}: ForestPlotProps) {
  const [ref, width] = useChartWidth(maxWidth);
  if (rows.length === 0) {
    return (
      <div ref={ref} className="chart">
        <p className="empty">{title}: nothing to plot.</p>
      </div>
    );
  }

  const tracks = Math.max(series.length, 1);
  const charsPerLine = Math.floor((width - 8) / 6.8);
  const titleLines = wrapWords(title, charsPerLine);
  const legendEntries: LegendEntry[] =
    series.length > 1
      ? series.map((name, index) => ({
          label: name,
          swatch: (x: number, y: number) => (
            <>
              <line x1={x} y1={y} x2={x + 20} y2={y} stroke="var(--series-1)" strokeWidth={2} />
              <Marker x={x + 10} y={y} hollow={index > 0} />
            </>
          ),
        }))
      : [];
  const titleHeight = titleLines.length * 16 + 4;
  const legend = layoutLegend(legendEntries, width, titleHeight);
  const top = titleHeight + legend.height + 6;

  const labelLines = rows.map((row) => wrapWords(row.label, charsPerLine));
  const rowHeights = labelLines.map((lines) => lines.length * LABEL_LINE + tracks * TRACK + 6);
  const rowTops: number[] = [];
  let plotHeight = 0;
  for (const rowHeight of rowHeights) {
    rowTops.push(top + plotHeight);
    plotHeight += rowHeight;
  }
  const axisY = top + plotHeight;
  const height = axisY + 40;

  const values = rows
    .flatMap((row) => row.estimates.flatMap((estimate) => (estimate ? [estimate.point, estimate.lower, estimate.upper] : [])))
    .filter(isNumber);
  const low = values.length ? Math.min(0, ...values) : -1;
  const high = values.length ? Math.max(0, ...values) : 1;
  const padding = (high - low) * 0.08 || 0.001;
  const ticks = niceTicks(low - padding, high + padding, width < 340 ? 3 : 4);
  const step = tickStep(ticks);
  const x = linear(ticks[0] ?? -1, ticks[ticks.length - 1] ?? 1, SIDE, Math.max(width - SIDE, SIDE + 1));

  return (
    <div ref={ref} className="chart">
      {width > 0 && (
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`${title}. ${rows.length} rows; the same numbers are in the table with this chart.`}
        >
          {titleLines.map((line, index) => (
            <text key={line} className="chart-title" x={0} y={13 + index * 16}>
              {line}
            </text>
          ))}
          <Legend entries={legendEntries} width={width} top={titleHeight} />
          {ticks.map((tick) => (
            <g key={tick}>
              <line x1={x(tick)} y1={top} x2={x(tick)} y2={axisY} stroke="var(--grid)" strokeWidth={1} />
              <text className="chart-tick" x={x(tick)} y={axisY + 16} textAnchor="middle">
                {formatTick(tick, step)}
              </text>
            </g>
          ))}
          <line x1={x(0)} y1={top - 3} x2={x(0)} y2={axisY} stroke="var(--text-muted)" strokeWidth={1} />
          <text className="chart-axis" x={width / 2} y={axisY + 34} textAnchor="middle">
            {axisLabel}
          </text>
          {rows.map((row, rowIndex) => {
            const lines = labelLines[rowIndex] ?? [row.label];
            const rowTop = rowTops[rowIndex] ?? top;
            const tracksTop = rowTop + lines.length * LABEL_LINE + 2;
            return (
              <g key={row.key}>
                {lines.map((line, index) => (
                  <text key={line} className="chart-label halo" x={0} y={rowTop + 12 + index * LABEL_LINE}>
                    {line}
                  </text>
                ))}
                {Array.from({ length: tracks }, (_, track) => {
                  const estimate = row.estimates[track];
                  const y = tracksTop + track * TRACK + TRACK / 2;
                  const name = series.length > 1 ? `, ${series[track] ?? ""}` : "";
                  if (!estimate || !isNumber(estimate.point)) {
                    return (
                      <text key={track} className="chart-axis" x={SIDE} y={y + 4}>
                        not estimable: {estimate?.reason || "no estimate in the results file"}
                      </text>
                    );
                  }
                  const hasInterval = isNumber(estimate.lower) && isNumber(estimate.upper);
                  const tooltip =
                    `${row.label}${name}: ${formatNumber(estimate.point, digits, true)}` +
                    (hasInterval
                      ? ` (${level ? `${level} ` : ""}interval ${formatNumber(estimate.lower, digits, true)} to ${formatNumber(estimate.upper, digits, true)})`
                      : ` (no interval: ${estimate.reason || "no reason recorded"})`);
                  return (
                    <g key={track}>
                      <title>{tooltip}</title>
                      {hasInterval && (
                        <line
                          x1={x(estimate.lower ?? 0)}
                          y1={y}
                          x2={x(estimate.upper ?? 0)}
                          y2={y}
                          stroke="var(--series-1)"
                          strokeWidth={2}
                          strokeLinecap="round"
                        />
                      )}
                      <Marker x={x(estimate.point)} y={y} hollow={track > 0} />
                    </g>
                  );
                })}
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}
