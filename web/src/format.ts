/**
 * Formatting helpers shared by every view.
 *
 * One rule runs through all of them: a missing value is never shown as 0.
 * It is shown as "not available" or "not estimable", with the reason when the
 * data give one.
 */

export const NOT_AVAILABLE = "not available";
export const NO_REASON = "no reason recorded";

export const MINUTE = 60_000;
export const HOUR = 3_600_000;
export const DAY = 86_400_000;

function isNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/**
 * Fixed decimals. `signed` adds "+" to positive values.
 *
 * Exactly zero prints as an unsigned zero. A value that is not zero but would
 * round to zero is printed in scientific notation instead ("+2.9e-5"), so the
 * sign of a small difference or of an interval bound is never rounded away.
 */
export function formatNumber(value: number | null | undefined, digits = 3, signed = false): string {
  if (!isNumber(value)) return NOT_AVAILABLE;
  if (value === 0) return (0).toFixed(digits);
  const text = value.toFixed(digits);
  const plus = signed && value > 0 ? "+" : "";
  return Number(text) === 0 ? `${plus}${value.toExponential(1)}` : `${plus}${text}`;
}

/** A measured value with at most `maxDecimals` decimals and no trailing zeros: 90.667, 2.1, 20. */
export function formatValue(value: number | null | undefined, maxDecimals = 3): string {
  if (!isNumber(value)) return NOT_AVAILABLE;
  const rounded = Number(value.toFixed(maxDecimals));
  return Object.is(rounded, -0) ? "0" : String(rounded);
}

export function formatCount(value: number | null | undefined): string {
  if (!isNumber(value)) return NOT_AVAILABLE;
  return Math.round(value).toLocaleString("en-US");
}

/** A share in [0, 1] as a percentage. */
export function formatPercent(share: number | null | undefined, digits = 1): string {
  if (!isNumber(share)) return NOT_AVAILABLE;
  return `${(share * 100).toFixed(digits)}%`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (!isNumber(bytes)) return NOT_AVAILABLE;
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = bytes;
  let unit = 0;
  while (Math.abs(value) >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || Math.abs(value) >= 100 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unit] ?? "B"}`;
}

/* -------------------------------------------------------------- estimates */

export interface IntervalLike {
  point: number | null;
  lower: number | null;
  upper: number | null;
  reason?: string | null;
}

export type Estimate =
  | { kind: "interval"; text: string }
  | { kind: "point-only"; text: string; reason: string }
  | { kind: "not-estimable"; text: string; reason: string };

/**
 * "0.712 (0.688 to 0.735)"; or the point with the reason there is no interval;
 * or "not estimable" with the reason there is no estimate at all.
 */
export function describeEstimate(interval: IntervalLike | null | undefined, digits = 3, signed = false): Estimate {
  if (interval === null || interval === undefined) {
    const reason = "this statistic is not in the results file";
    return { kind: "not-estimable", text: `not estimable: ${reason}`, reason };
  }
  const reason = interval.reason?.trim() ? interval.reason.trim() : NO_REASON;
  if (!isNumber(interval.point)) return { kind: "not-estimable", text: `not estimable: ${reason}`, reason };
  const point = formatNumber(interval.point, digits, signed);
  if (!isNumber(interval.lower) || !isNumber(interval.upper)) {
    return { kind: "point-only", text: `${point} (no interval: ${reason})`, reason };
  }
  return {
    kind: "interval",
    text: `${point} (${formatNumber(interval.lower, digits, signed)} to ${formatNumber(interval.upper, digits, signed)})`,
  };
}

export function formatEstimate(interval: IntervalLike | null | undefined, digits = 3, signed = false): string {
  return describeEstimate(interval, digits, signed).text;
}

/** 0.95 -> "95%". Used for "95% interval" so the level always comes from the results file. */
export function formatLevel(level: number | null | undefined): string {
  if (!isNumber(level)) return "bootstrap";
  return `${Number((level * 100).toFixed(1))}%`;
}

/* ------------------------------------------------------------------- time */

const ISO = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::(\d{2})(?:\.(\d+))?)?\s*(Z|[+-]\d{2}:?\d{2})$/i;

/**
 * Milliseconds since the epoch for an ISO-8601 instant with an explicit offset.
 * Returns null for a missing value and for a string without an offset: a naive
 * time is not an instant, and guessing a zone would move every decision.
 */
export function parseTime(iso: string | null | undefined): number | null {
  if (typeof iso !== "string") return null;
  const match = ISO.exec(iso.trim());
  if (!match) return null;
  const [, date, clock, seconds, fraction, zone] = match;
  const milliseconds = fraction ? `.${fraction.slice(0, 3).padEnd(3, "0")}` : "";
  const offset = zone && zone.toUpperCase() !== "Z" && !zone.includes(":") ? `${zone.slice(0, 3)}:${zone.slice(3)}` : zone;
  const parsed = Date.parse(`${date}T${clock}:${seconds ?? "00"}${milliseconds}${(offset ?? "Z").toUpperCase()}`);
  return Number.isNaN(parsed) ? null : parsed;
}

const pad = (value: number) => String(value).padStart(2, "0");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "07:52", or "04:56:50" when the instant is not on a whole minute. Always UTC. */
export function formatClock(ms: number): string {
  const date = new Date(ms);
  const base = `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
  return date.getUTCSeconds() === 0 ? base : `${base}:${pad(date.getUTCSeconds())}`;
}

/** "17 Feb" (UTC). */
export function formatDay(ms: number): string {
  const date = new Date(ms);
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()] ?? ""}`;
}

/** "2025-02-17 07:52 UTC". Every time in the UI is UTC and says so. */
export function formatUtc(ms: number | null | undefined, withZone = true): string {
  if (!isNumber(ms)) return NOT_AVAILABLE;
  const date = new Date(ms);
  const day = `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
  return `${day} ${formatClock(ms)}${withZone ? " UTC" : ""}`;
}

export function formatIso(iso: string | null | undefined, withZone = true): string {
  return formatUtc(parseTime(iso), withZone);
}

/** The cutoff the API expects: ISO-8601, whole seconds, explicit UTC offset. */
export function toApiTime(ms: number): string {
  return `${new Date(Math.round(ms / 1000) * 1000).toISOString().slice(0, 19)}+00:00`;
}

/** An elapsed time in words: "45 s", "1 h 30 min", "2 d 3 h". Negative durations keep their sign. */
export function formatDuration(ms: number | null | undefined): string {
  if (!isNumber(ms)) return NOT_AVAILABLE;
  let seconds = Math.round(Math.abs(ms) / 1000);
  const sign = ms < 0 && seconds > 0 ? "-" : "";
  if (seconds < 60) return `${sign}${seconds} s`;
  const days = Math.floor(seconds / 86_400);
  seconds -= days * 86_400;
  const hours = Math.floor(seconds / 3600);
  seconds -= hours * 3600;
  const minutes = Math.floor(seconds / 60);
  seconds -= minutes * 60;
  const parts: string[] = [];
  if (days) parts.push(`${days} d`);
  if (hours) parts.push(`${hours} h`);
  if (minutes && !days) parts.push(`${minutes} min`);
  if (seconds && !days && !hours) parts.push(`${seconds} s`);
  return sign + parts.join(" ");
}

/** A lookback window given in seconds: "4 h", "48 h", "90 min", "14 d". Hours up to a week, as features are named. */
export function formatWindow(seconds: number): string {
  if (seconds % 86_400 === 0 && seconds >= 7 * 86_400) return `${seconds / 86_400} d`;
  if (seconds % 3600 === 0) return `${seconds / 3600} h`;
  if (seconds % 60 === 0) return `${seconds / 60} min`;
  return `${seconds} s`;
}

/* ------------------------------------------------------------------ words */

export function shortHash(hash: string | null | undefined, length = 12): string {
  if (typeof hash !== "string" || hash === "") return NOT_AVAILABLE;
  return hash.slice(0, length);
}

/** "used_before_available" -> "Used before available". For keys that have no curated label. */
export function humanise(key: string): string {
  const words = key.replace(/__/g, ", ").replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
  return `${formatCount(count)} ${count === 1 ? singular : pluralForm}`;
}

export function median(values: readonly number[]): number | null {
  const sorted = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (sorted.length === 0) return null;
  const middle = Math.floor(sorted.length / 2);
  const upper = sorted[middle];
  const lower = sorted[middle - 1];
  if (upper === undefined) return null;
  return sorted.length % 2 === 1 || lower === undefined ? upper : (lower + upper) / 2;
}
