/**
 * Scales, ticks and lane packing for the hand-written SVG charts.
 * The tick rules mirror src/asoflab/audit/svg.py so the UI and the static
 * report draw the same axes for the same numbers.
 */

import { DAY, HOUR, MINUTE } from "./format";

export function clamp(value: number, low: number, high: number): number {
  return Math.min(Math.max(value, low), high);
}

/** An exact power of ten. `10 ** -5` is not exactly 1e-5 in every engine; the literal is. */
function pow10(exponent: number): number {
  return Number(`1e${exponent}`);
}

/** Round tick values (1, 2 or 5 times a power of ten) covering [low, high]. */
export function niceTicks(low: number, high: number, target = 5): number[] {
  if (!Number.isFinite(low) || !Number.isFinite(high)) return [0, 1];
  if (high <= low) {
    const padding = Math.abs(low) * 0.1 || 1;
    low -= padding;
    high += padding;
  }
  const raw = (high - low) / Math.max(target, 1);
  const magnitude = pow10(Math.floor(Math.log10(raw)));
  const step = [1, 2, 5, 10].map((multiple) => multiple * magnitude).find((candidate) => candidate >= raw) ?? magnitude * 10;
  const round = (value: number) => Number(value.toPrecision(12));
  const ticks: number[] = [];
  for (let index = Math.floor(low / step); index * step <= high + step * 1e-9; index += 1) {
    ticks.push(round(index * step));
  }
  const last = ticks[ticks.length - 1];
  if (last !== undefined && last < high - step * 1e-9) ticks.push(round(last + step));
  return ticks;
}

export function tickStep(ticks: readonly number[]): number {
  const [first, second] = ticks;
  return first !== undefined && second !== undefined ? second - first : 1;
}

/** A tick label with just enough decimals for the step. Zero prints as "0". */
export function formatTick(value: number, step: number): string {
  const decimals = step > 0 ? Math.max(0, -Math.floor(Math.log10(step) + 1e-9)) : 0;
  const text = value.toFixed(Math.min(decimals, 6));
  return Number(text) === 0 ? "0" : text;
}

/** Powers of ten covering [low, high]; both must be positive. */
export function logTicks(low: number, high: number): number[] {
  if (!(low > 0) || !(high > 0)) return [1, 10];
  const first = Math.floor(Math.log10(Math.min(low, high)) + 1e-9);
  const last = Math.max(Math.ceil(Math.log10(Math.max(low, high)) - 1e-9), first + 1);
  const ticks: number[] = [];
  for (let exponent = first; exponent <= last; exponent += 1) ticks.push(pow10(exponent));
  return ticks;
}

export type Scale = (value: number) => number;

/** Linear map from [d0, d1] to [r0, r1]. A zero-width domain maps to the middle of the range. */
export function linear(d0: number, d1: number, r0: number, r1: number): Scale {
  const span = d1 - d0;
  if (span === 0) return () => (r0 + r1) / 2;
  return (value) => r0 + ((value - d0) / span) * (r1 - r0);
}

/** Logarithmic map from [d0, d1] (both positive) to [r0, r1]. */
export function logarithmic(d0: number, d1: number, r0: number, r1: number): Scale {
  const inner = linear(Math.log10(d0), Math.log10(d1), r0, r1);
  return (value) => inner(Math.log10(value));
}

/* ------------------------------------------------------------- time axes */

export interface TimeRange {
  start: number;
  end: number;
}

/** Position of an instant inside a horizontal span of pixels. */
export function timeToX(ms: number, range: TimeRange, x0: number, x1: number): number {
  return linear(range.start, range.end, x0, x1)(ms);
}

/** The inverse of timeToX. */
export function xToTime(x: number, range: TimeRange, x0: number, x1: number): number {
  if (x1 === x0) return range.start;
  return range.start + ((x - x0) / (x1 - x0)) * (range.end - range.start);
}

/** Round an instant to the nearest multiple of `step` milliseconds (from the UTC epoch). */
export function snapTime(ms: number, step: number): number {
  return Math.round(ms / step) * step;
}

const TIME_STEPS = [
  5 * MINUTE,
  10 * MINUTE,
  15 * MINUTE,
  30 * MINUTE,
  HOUR,
  2 * HOUR,
  3 * HOUR,
  6 * HOUR,
  12 * HOUR,
  DAY,
  2 * DAY,
  7 * DAY,
];

/** Tick instants on whole multiples of a round step (UTC), at most `maxTicks` of them. */
export function timeTicks(range: TimeRange, maxTicks: number): { ticks: number[]; step: number } {
  const span = range.end - range.start;
  const limit = Math.max(maxTicks, 2);
  const step = TIME_STEPS.find((candidate) => span / candidate <= limit) ?? Math.ceil(span / limit / (7 * DAY)) * 7 * DAY;
  const ticks: number[] = [];
  for (let tick = Math.ceil(range.start / step) * step; tick <= range.end; tick += step) ticks.push(tick);
  return { ticks, step };
}

/* ----------------------------------------------------------------- lanes */

/**
 * Assign each horizontal span to the first lane where it does not overlap an
 * earlier span (greedy interval partitioning). Returns one lane index per span,
 * in the order given.
 */
export function packLanes(spans: readonly { start: number; end: number }[], gap = 0): number[] {
  const order = spans.map((_, index) => index).sort((a, b) => (spans[a]?.start ?? 0) - (spans[b]?.start ?? 0));
  const laneEnds: number[] = [];
  const lanes = new Array<number>(spans.length).fill(0);
  for (const index of order) {
    const span = spans[index];
    if (!span) continue;
    let lane = laneEnds.findIndex((end) => end + gap <= span.start);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(span.end);
    } else {
      laneEnds[lane] = span.end;
    }
    lanes[index] = lane;
  }
  return lanes;
}
