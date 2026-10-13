/**
 * Geometry of the patient timeline: which observations fall in the visible
 * time range, and where each version is drawn.
 *
 * One logical event (one observation with all of its versions) is a block.
 * Each version gets its own row inside the block: a tick at its event time, a
 * connector, and a status marker at the time it became known. Blocks of one
 * event type are packed into lanes so that neighbours never overlap.
 */

import type { TimelineVersion } from "../api";
import { formatDuration, parseTime } from "../format";
import { packLanes, timeToX, type TimeRange } from "../scale";

export const VERSION_ROW = 18;
export const GROUP_HEADER = 20;
export const WINDOW_ROW = 14;
export const GROUP_GAP = 10;

/** The time a version became known: on the policy's clock when it has one, otherwise on the pipeline's. */
export function knownTime(version: TimelineVersion): number | null {
  return parseTime(version.known_time) ?? parseTime(version.pipeline_available_time);
}

export const versionKey = (version: TimelineVersion) => `${version.logical_event_id}|${version.version_id}`;

/** The gap between a measurement and the moment its version became known, in words. */
export function describeDelay(eventMs: number, knownMs: number): string {
  const gap = knownMs - eventMs;
  if (Math.abs(gap) < 1000) return "no delay";
  return gap > 0 ? `${formatDuration(gap)} after the event` : `${formatDuration(-gap)} before the event`;
}

/** True when the version is marked "used" although the pipeline received it only after `cutoff`. */
export function usedBeforePipelineHadIt(version: TimelineVersion, cutoff: number | null): boolean {
  const received = parseTime(version.pipeline_available_time);
  return version.status === "used" && cutoff !== null && received !== null && received > cutoff;
}

/**
 * Ids of the observations with any instant (an event time or a known time of
 * any version) inside the range. The chart and the details table use the same
 * rule, so they always show the same observations.
 */
export function observationsInRange(versions: readonly TimelineVersion[], range: TimeRange): Set<string> {
  const extents = new Map<string, TimeRange>();
  for (const version of versions) {
    for (const time of [parseTime(version.event_time), knownTime(version)]) {
      if (time === null) continue;
      const extent = extents.get(version.logical_event_id);
      if (!extent) extents.set(version.logical_event_id, { start: time, end: time });
      else {
        extent.start = Math.min(extent.start, time);
        extent.end = Math.max(extent.end, time);
      }
    }
  }
  const inside = new Set<string>();
  for (const [id, extent] of extents) {
    if (extent.end >= range.start && extent.start <= range.end) inside.add(id);
  }
  return inside;
}

export interface PlacedVersion {
  version: TimelineVersion;
  key: string;
  /** The version's own event time. A retraction has none and borrows the observation's, for drawing only. */
  eventMs: number | null;
  eventBorrowed: boolean;
  knownMs: number | null;
  xEvent: number | null;
  xKnown: number | null;
  /** "v2", "v2 retraction": shown when the observation has more than one version. */
  label: string | null;
  y: number;
  /** Marked "used", yet the pipeline received this version only after the cutoff the statuses are for. */
  lateForPipeline: boolean;
}

export interface Block {
  id: string;
  versions: PlacedVersion[];
  x0: number;
  x1: number;
  top: number;
  height: number;
}

export interface Group {
  eventType: string;
  unit: string | null;
  top: number;
  lanesTop: number;
  lanesHeight: number;
  blocks: Block[];
  /** Observations of this type for the patient, and how many lie wholly outside the visible range. */
  total: number;
  earlier: number;
  later: number;
}

export interface TimelineLayout {
  groups: Group[];
  bottom: number;
  /** Versions in the visible range, in drawing order. */
  visible: PlacedVersion[];
}

interface LayoutInput {
  versions: readonly TimelineVersion[];
  /** Event types in display order; types found only in the data follow, alphabetically. */
  typeOrder: readonly string[];
  view: TimeRange;
  x0: number;
  x1: number;
  top: number;
  /** The cutoff the statuses were computed for. */
  appliedCutoff: number | null;
}

const LABEL_CHAR = 6.2;

export function layoutTimeline({ versions, typeOrder, view, x0, x1, top, appliedCutoff }: LayoutInput): TimelineLayout {
  const byEvent = new Map<string, TimelineVersion[]>();
  for (const version of versions) {
    const list = byEvent.get(version.logical_event_id);
    if (list) list.push(version);
    else byEvent.set(version.logical_event_id, [version]);
  }

  const types = [...typeOrder];
  for (const type of [...new Set(versions.map((version) => version.event_type))].sort()) {
    if (!types.includes(type)) types.push(type);
  }

  interface Draft {
    id: string;
    versions: PlacedVersion[];
    x0: number;
    x1: number;
  }
  const drafts = new Map<string, Draft[]>();
  const counts = new Map<string, { total: number; earlier: number; later: number }>();
  const units = new Map<string, string>();
  for (const type of types) {
    drafts.set(type, []);
    counts.set(type, { total: 0, earlier: 0, later: 0 });
  }

  for (const [id, list] of byEvent) {
    const sorted = [...list].sort((a, b) => a.source_revision - b.source_revision);
    const first = sorted[0];
    if (!first) continue;
    const count = counts.get(first.event_type);
    if (!count) continue;
    count.total += 1;
    const unit = sorted.find((version) => version.unit)?.unit;
    if (unit && !units.has(first.event_type)) units.set(first.event_type, unit);

    const ownEvent = sorted.map((version) => parseTime(version.event_time));
    const anyEvent = ownEvent.find((time) => time !== null) ?? null;
    let lastEvent: number | null = null;
    const placed: PlacedVersion[] = sorted.map((version, index) => {
      const own = ownEvent[index] ?? null;
      if (own !== null) lastEvent = own;
      const eventMs = own ?? lastEvent ?? anyEvent;
      const knownMs = knownTime(version);
      const operation = version.operation === "retract" ? " retraction" : "";
      return {
        version,
        key: versionKey(version),
        eventMs,
        eventBorrowed: own === null && eventMs !== null,
        knownMs,
        xEvent: eventMs === null ? null : timeToX(eventMs, view, x0, x1),
        xKnown: knownMs === null ? null : timeToX(knownMs, view, x0, x1),
        label: sorted.length > 1 || operation ? `v${version.source_revision}${operation}` : null,
        y: 0,
        lateForPipeline: usedBeforePipelineHadIt(version, appliedCutoff),
      };
    });

    const times = placed.flatMap((item) => [item.eventMs, item.knownMs]).filter((time): time is number => time !== null);
    if (times.length === 0) continue; // nothing to position it by; it stays in the table
    if (Math.max(...times) < view.start) {
      count.earlier += 1;
      continue;
    }
    if (Math.min(...times) > view.end) {
      count.later += 1;
      continue;
    }
    const left = Math.min(...placed.flatMap((item) => [item.xEvent, item.xKnown]).filter((x): x is number => x !== null));
    const right = Math.max(
      ...placed.map((item) => {
        const marker = item.xKnown ?? item.xEvent ?? x0;
        return Math.max(marker, item.xEvent ?? marker) + 8 + (item.label ? item.label.length * LABEL_CHAR + 6 : 0);
      }),
    );
    drafts.get(first.event_type)?.push({ id, versions: placed, x0: Math.max(left - 8, x0 - 40), x1: Math.min(right, x1 + 200) });
  }

  const groups: Group[] = [];
  const visible: PlacedVersion[] = [];
  let cursor = top;
  for (const type of types) {
    const list = drafts.get(type) ?? [];
    const count = counts.get(type) ?? { total: 0, earlier: 0, later: 0 };
    list.sort((a, b) => a.x0 - b.x0 || a.id.localeCompare(b.id));
    const lanes = packLanes(
      list.map((draft) => ({ start: draft.x0, end: draft.x1 })),
      3,
    );
    const laneRows: number[] = [];
    list.forEach((draft, index) => {
      const lane = lanes[index] ?? 0;
      laneRows[lane] = Math.max(laneRows[lane] ?? 0, draft.versions.length);
    });
    const lanesTop = cursor + GROUP_HEADER + WINDOW_ROW;
    const laneTops: number[] = [];
    let lanesHeight = 0;
    for (const rows of laneRows) {
      laneTops.push(lanesTop + lanesHeight);
      lanesHeight += (rows ?? 1) * VERSION_ROW;
    }
    lanesHeight = Math.max(lanesHeight, VERSION_ROW);
    const blocks: Block[] = list.map((draft, index) => {
      const blockTop = laneTops[lanes[index] ?? 0] ?? lanesTop;
      draft.versions.forEach((item, row) => {
        item.y = blockTop + row * VERSION_ROW + VERSION_ROW / 2;
        visible.push(item);
      });
      return { ...draft, top: blockTop, height: draft.versions.length * VERSION_ROW };
    });
    groups.push({
      eventType: type,
      unit: units.get(type) ?? null,
      top: cursor,
      lanesTop,
      lanesHeight,
      blocks,
      total: count.total,
      earlier: count.earlier,
      later: count.later,
    });
    cursor = lanesTop + lanesHeight + GROUP_GAP;
  }
  return { groups, bottom: cursor, visible };
}
