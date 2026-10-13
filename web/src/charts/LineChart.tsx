import { formatCount } from "../format";
import { formatTick, linear, logTicks, logarithmic, niceTicks, tickStep, type Scale } from "../scale";
import { Legend, layoutLegend, seriesColour, textWidth, useChartWidth, wrapWords, type LegendEntry } from "./common";

export interface LinePoint {
  x: number;
  y: number;
  title?: string;
}

export interface LineSeries {
  name: string;
  /** Which series colour to use (0, 1 or 2). Fixed per entity, so a colour means the same thing in every chart. */
  slot: number;
  points: readonly LinePoint[];
}

interface LineChartProps {
  title: string;
  xLabel: string;
  yLabel: string;
  /** At most three series. */
  series: readonly LineSeries[];
  /** "log": positioned by log10, with ticks at the data's own x values. "ordinal": evenly spaced categories. */
  xScale: "log" | "ordinal";
  yScale?: "linear" | "log";
  formatX?: (value: number) => string;
  formatY?: (value: number) => string;
  /** Upper end of a linear y-axis, so small multiples can share one scale. */
  yMax?: number;
  maxWidth?: number;
  plotHeight?: number;
}

const finite = (value: number) => Number.isFinite(value);

/** Lines with markers on one pair of axes. A legend is drawn when there are two or more series. */
export function LineChart({
  title,
  xLabel,
  yLabel,
  series,
  xScale,
  yScale = "linear",
  formatX = formatCount,
  formatY,
  yMax,
  maxWidth = 460,
  plotHeight = 190,
}: LineChartProps) {
  const [ref, width] = useChartWidth(maxWidth);
  const usable = (point: LinePoint) =>
    finite(point.x) && finite(point.y) && (yScale !== "log" || point.y > 0) && (xScale !== "log" || point.x > 0);
  const drawn = series.slice(0, 3).map((one) => ({
    ...one,
    points: one.points.filter(usable).sort((a, b) => a.x - b.x),
  }));
  const points = drawn.flatMap((one) => one.points);
  if (points.length === 0) {
    return (
      <div ref={ref} className="chart">
        <p className="empty">{title}: nothing to plot.</p>
      </div>
    );
  }

  const xs = [...new Set(points.map((point) => point.x))].sort((a, b) => a - b);
  const ys = points.map((point) => point.y);
  const yTicks =
    yScale === "log" ? logTicks(Math.min(...ys), Math.max(...ys)) : niceTicks(0, Math.max(yMax ?? 0, ...ys), 5);
  const yStep = tickStep(yTicks);
  const labelY = formatY ?? ((value: number) => (yScale === "log" ? String(value) : formatTick(value, yStep)));
  const yFirst = yTicks[0] ?? 0;
  const yLast = yTicks[yTicks.length - 1] ?? 1;

  const titleLines = wrapWords(title, Math.floor((width - 8) / 6.8));
  const legendEntries: LegendEntry[] =
    drawn.length > 1
      ? drawn.map((one) => ({
          label: one.name,
          swatch: (x: number, y: number) => (
            <>
              <line x1={x} y1={y} x2={x + 20} y2={y} stroke={seriesColour(one.slot)} strokeWidth={2} />
              <circle cx={x + 10} cy={y} r={4} fill={seriesColour(one.slot)} />
            </>
          ),
        }))
      : [];
  const titleHeight = titleLines.length * 16 + 4;
  const legend = layoutLegend(legendEntries, width, titleHeight);
  const top = titleHeight + legend.height + 10;
  const left = 24 + Math.max(...yTicks.map((tick) => textWidth(labelY(tick)))) + 8;
  const right = 18;
  const bottom = top + plotHeight;
  const height = bottom + 44;

  const sy: Scale =
    yScale === "log" ? logarithmic(yFirst, yLast, bottom, top) : linear(yFirst, yLast, bottom, top);
  const xFirst = xs[0] ?? 1;
  const xLast = xs[xs.length - 1] ?? 1;
  const band = (width - left - right) / xs.length;
  const sx: Scale =
    xScale === "ordinal"
      ? (value) => left + band * (xs.indexOf(value) + 0.5)
      : xs.length === 1
        ? () => (left + width - right) / 2
        : logarithmic(xFirst, xLast, left + 16, width - right - 16);

  // Skip an x label that would run into the one before it.
  let labelEdge = -Infinity;
  const xLabels = xs.map((value) => {
    const text = formatX(value);
    const half = textWidth(text) / 2;
    const centre = sx(value);
    if (centre - half < labelEdge + 6) return null;
    labelEdge = centre + half;
    return { value, text, centre };
  });

  return (
    <div ref={ref} className="chart">
      {width > 0 && (
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`${title}. ${points.length} points in ${drawn.length} series; the same numbers are in the table with this chart.`}
        >
          {titleLines.map((line, index) => (
            <text key={line} className="chart-title" x={0} y={13 + index * 16}>
              {line}
            </text>
          ))}
          <Legend entries={legendEntries} width={width} top={titleHeight} />
          {yTicks.map((tick) => (
            <g key={tick}>
              <line x1={left} y1={sy(tick)} x2={width - right} y2={sy(tick)} stroke="var(--grid)" strokeWidth={1} />
              <text className="chart-tick" x={left - 8} y={sy(tick) + 4} textAnchor="end">
                {labelY(tick)}
              </text>
            </g>
          ))}
          <line x1={left} y1={bottom} x2={width - right} y2={bottom} stroke="var(--text-muted)" strokeWidth={1} />
          {xLabels.map(
            (label) =>
              label && (
                <text key={label.value} className="chart-tick" x={label.centre} y={bottom + 16} textAnchor="middle">
                  {label.text}
                </text>
              ),
          )}
          <text className="chart-axis" x={(left + width - right) / 2} y={height - 8} textAnchor="middle">
            {xLabel}
          </text>
          <text className="chart-axis" transform={`translate(12 ${(top + bottom) / 2}) rotate(-90)`} textAnchor="middle">
            {yLabel}
          </text>
          {drawn.map((one) => (
            <g key={one.name}>
              {one.points.length > 1 && (
                <path
                  d={one.points.map((point, index) => `${index === 0 ? "M" : "L"}${sx(point.x).toFixed(1)},${sy(point.y).toFixed(1)}`).join(" ")}
                  fill="none"
                  stroke={seriesColour(one.slot)}
                  strokeWidth={2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              )}
              {one.points.map((point) => (
                <circle
                  key={point.x}
                  cx={sx(point.x)}
                  cy={sy(point.y)}
                  r={4.5}
                  fill={seriesColour(one.slot)}
                  stroke="var(--surface)"
                  strokeWidth={2}
                >
                  <title>{point.title ?? `${one.name}: ${formatX(point.x)}, ${labelY(point.y)}`}</title>
                </circle>
              ))}
            </g>
          ))}
        </svg>
      )}
    </div>
  );
}
