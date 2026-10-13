/**
 * Shared pieces of the inline-SVG charts.
 *
 * Conventions, as in src/asoflab/audit/svg.py: 2 px lines, markers of radius
 * about 4.5 with a 2 px surface-coloured ring, hairline solid gridlines, one
 * y-axis per chart, text in text colours and never in a series colour, a
 * legend whenever there are two or more series, and a native tooltip (<title>)
 * on every mark. Charts are drawn at their real pixel width, so 12 px labels
 * stay 12 px on a narrow screen.
 */

import type { ReactNode, RefObject } from "react";
import { useElementWidth } from "../hooks";

export const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)"] as const;

export function seriesColour(slot: number): string {
  return SERIES[slot % SERIES.length] ?? SERIES[0];
}

/** Rough width of UI text in pixels. SVG text cannot be measured before it is painted. */
export function textWidth(text: string, size = 12): number {
  return text.length * size * 0.56;
}

/** Break a label into at most `maxLines` lines of about `maxChars` characters, on spaces. */
export function wrapWords(text: string, maxChars: number, maxLines = 2): string[] {
  const limit = Math.max(maxChars, 8);
  const lines: string[] = [];
  let line = "";
  for (const word of text.split(/\s+/)) {
    const candidate = line ? `${line} ${word}` : word;
    if (candidate.length <= limit || line === "") {
      line = candidate;
    } else {
      lines.push(line);
      line = word;
    }
  }
  if (line) lines.push(line);
  if (lines.length <= maxLines) return lines;
  const kept = lines.slice(0, maxLines);
  const last = kept[maxLines - 1] ?? "";
  kept[maxLines - 1] = `${last.slice(0, Math.max(limit - 1, 1))}…`;
  return kept;
}

/** The chart's width: the container's, capped at `maxWidth`. Zero until the container is measured. */
export function useChartWidth(maxWidth: number): [RefObject<HTMLDivElement | null>, number] {
  const [ref, measured] = useElementWidth<HTMLDivElement>();
  return [ref, Math.min(measured, maxWidth)];
}

export interface LegendEntry {
  label: string;
  /** Draws the swatch in a 20 px wide box whose left edge is x and whose centre line is y. */
  swatch: (x: number, y: number) => ReactNode;
}

/** Legend entries flowed left to right, wrapping when the chart is narrow. */
export function layoutLegend(entries: readonly LegendEntry[], width: number, top: number) {
  const positions: { x: number; y: number }[] = [];
  let x = 0;
  let y = top;
  for (const entry of entries) {
    const needed = 26 + textWidth(entry.label) + 14;
    if (x > 0 && x + needed > width) {
      x = 0;
      y += 18;
    }
    positions.push({ x, y });
    x += needed;
  }
  return { positions, height: entries.length === 0 ? 0 : y - top + 18 };
}

export function Legend({ entries, width, top }: { entries: readonly LegendEntry[]; width: number; top: number }) {
  const { positions } = layoutLegend(entries, width, top);
  return (
    <g aria-hidden="true">
      {entries.map((entry, index) => {
        const position = positions[index] ?? { x: 0, y: top };
        return (
          <g key={entry.label}>
            {entry.swatch(position.x, position.y + 9)}
            <text className="chart-label" x={position.x + 26} y={position.y + 13}>
              {entry.label}
            </text>
          </g>
        );
      })}
    </g>
  );
}

/** A chart with its caption. The caption states that the data are simulated and gives the sample size. */
export function Figure({ caption, children }: { caption: ReactNode; children: ReactNode }) {
  return (
    <figure className="figure">
      {children}
      <figcaption>{caption}</figcaption>
    </figure>
  );
}
