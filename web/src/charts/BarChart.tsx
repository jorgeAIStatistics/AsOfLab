import { formatTick, linear, niceTicks, tickStep } from "../scale";
import { Legend, layoutLegend, seriesColour, textWidth, useChartWidth, wrapWords, type LegendEntry } from "./common";

export interface BarGroup {
  name: string;
  /** Which series colour to use (0, 1 or 2). */
  slot: number;
  /** One value per category; null leaves the bar out (the table says why). */
  values: readonly (number | null)[];
  titles?: readonly (string | undefined)[];
}

interface BarChartProps {
  title: string;
  xLabel: string;
  yLabel: string;
  categories: readonly string[];
  /** At most three groups. */
  groups: readonly BarGroup[];
  formatY?: (value: number) => string;
  maxWidth?: number;
  plotHeight?: number;
}

/** Grouped columns from a single zero baseline: at most 24 px thick, rounded at the data end only. */
export function BarChart({
  title,
  xLabel,
  yLabel,
  categories,
  groups,
  formatY,
  maxWidth = 460,
  plotHeight = 190,
}: BarChartProps) {
  const [ref, width] = useChartWidth(maxWidth);
  const drawn = groups.slice(0, 3);
  const values = drawn.flatMap((group) => group.values).filter((value): value is number => value !== null && Number.isFinite(value));
  if (values.length === 0 || categories.length === 0) {
    return (
      <div ref={ref} className="chart">
        <p className="empty">{title}: nothing to plot.</p>
      </div>
    );
  }

  const yTicks = niceTicks(0, Math.max(...values), 5);
  const yStep = tickStep(yTicks);
  const labelY = formatY ?? ((value: number) => formatTick(value, yStep));
  const titleLines = wrapWords(title, Math.floor((width - 8) / 6.8));
  const legendEntries: LegendEntry[] =
    drawn.length > 1
      ? drawn.map((group) => ({
          label: group.name,
          swatch: (x: number, y: number) => <rect x={x + 4} y={y - 6} width={12} height={12} rx={2} fill={seriesColour(group.slot)} />,
        }))
      : [];
  const titleHeight = titleLines.length * 16 + 4;
  const legend = layoutLegend(legendEntries, width, titleHeight);
  const top = titleHeight + legend.height + 10;
  const left = 24 + Math.max(...yTicks.map((tick) => textWidth(labelY(tick)))) + 8;
  const right = 12;
  const bottom = top + plotHeight;
  const height = bottom + 44;
  const sy = linear(yTicks[0] ?? 0, yTicks[yTicks.length - 1] ?? 1, bottom, top);
  const band = (width - left - right) / categories.length;
  const bar = Math.max(Math.min(24, (band - 12) / drawn.length - 2), 2);
  const cluster = drawn.length * (bar + 2) - 2;
  const baseline = sy(0);

  return (
    <div ref={ref} className="chart">
      {width > 0 && (
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`${title}. ${values.length} bars; the same numbers are in the table with this chart.`}
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
          {categories.map((category, position) => {
            const centre = left + band * (position + 0.5);
            return (
              <g key={category}>
                <text className="chart-tick" x={centre} y={bottom + 16} textAnchor="middle">
                  {category}
                </text>
                {drawn.map((group, index) => {
                  const value = group.values[position];
                  if (value === null || value === undefined || !Number.isFinite(value)) return null;
                  const x = centre - cluster / 2 + index * (bar + 2);
                  const y = sy(value);
                  const radius = Math.min(4, bar / 2, Math.max(baseline - y, 0));
                  return (
                    <path
                      key={group.name}
                      d={
                        `M${x.toFixed(1)},${baseline.toFixed(1)} V${(y + radius).toFixed(1)} ` +
                        `Q${x.toFixed(1)},${y.toFixed(1)} ${(x + radius).toFixed(1)},${y.toFixed(1)} ` +
                        `H${(x + bar - radius).toFixed(1)} ` +
                        `Q${(x + bar).toFixed(1)},${y.toFixed(1)} ${(x + bar).toFixed(1)},${(y + radius).toFixed(1)} ` +
                        `V${baseline.toFixed(1)} Z`
                      }
                      fill={seriesColour(group.slot)}
                    >
                      <title>{group.titles?.[position] ?? `${group.name}, ${category}: ${labelY(value)}`}</title>
                    </path>
                  );
                })}
              </g>
            );
          })}
          <line x1={left} y1={baseline} x2={width - right} y2={baseline} stroke="var(--text-muted)" strokeWidth={1} />
          <text className="chart-axis" x={(left + width - right) / 2} y={height - 8} textAnchor="middle">
            {xLabel}
          </text>
          <text className="chart-axis" transform={`translate(12 ${(top + bottom) / 2}) rotate(-90)`} textAnchor="middle">
            {yLabel}
          </text>
        </svg>
      )}
    </div>
  );
}
