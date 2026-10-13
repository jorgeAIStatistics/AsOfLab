import { describe, expect, it } from "vitest";
import type { TimelineVersion } from "../api";
import { HOUR, MINUTE } from "../format";
import {
  VERSION_ROW,
  describeDelay,
  knownTime,
  layoutTimeline,
  observationsInRange,
  usedBeforePipelineHadIt,
} from "./timelineLayout";

const cutoff = Date.UTC(2025, 1, 17, 8, 0);
const view = { start: cutoff - 48 * HOUR, end: cutoff + 12 * HOUR };
const iso = (ms: number) => new Date(ms).toISOString();

/** One version of an observation, measured `eventHours` before the cutoff and known `delayMinutes` later. */
function version(id: string, eventHours: number, delayMinutes: number, overrides: Partial<TimelineVersion> = {}): TimelineVersion {
  const event = cutoff - eventHours * HOUR;
  const known = event + delayMinutes * MINUTE;
  return {
    entity_id: "P1",
    logical_event_id: id,
    version_id: "v1",
    source_revision: 1,
    operation: "upsert",
    event_type: "lab.lactate",
    event_time: iso(event),
    value: 2.1,
    unit: "mmol/L",
    source_available_time: iso(known),
    pipeline_available_time: iso(known),
    known_time: iso(known),
    status: known <= cutoff ? "used" : "not_yet_available",
    reason: "",
    used_by: [],
    ...overrides,
  };
}

const layout = (versions: TimelineVersion[]) =>
  layoutTimeline({ versions, typeOrder: ["vital.heart_rate", "lab.lactate"], view, x0: 0, x1: 600, top: 50, appliedCutoff: cutoff });

describe("layoutTimeline", () => {
  it("places the event tick and the known-time marker in proportion to time", () => {
    const [group] = layout([version("a", 12, 120)]).groups.filter((one) => one.eventType === "lab.lactate");
    const placed = group?.blocks[0]?.versions[0];
    expect(placed?.xEvent).toBeCloseTo(360, 6); // 36 of 60 hours across 600 px
    expect(placed?.xKnown).toBeCloseTo(380, 6); // two hours later
    expect(placed?.label).toBeNull(); // a single version needs no revision label
  });

  it("puts observations that would overlap on separate lanes", () => {
    const result = layout([version("a", 12, 180), version("b", 11, 180), version("c", 2, 30)]);
    const blocks = result.groups.find((one) => one.eventType === "lab.lactate")?.blocks ?? [];
    const top = Object.fromEntries(blocks.map((block) => [block.id, block.top]));
    expect(top["b"]).not.toBe(top["a"]);
    expect(top["c"]).toBe(top["a"]); // far enough to the right to reuse the first lane
  });

  it("stacks the versions of one observation in revision order and labels them", () => {
    const first = version("a", 10, 60, { status: "superseded" });
    const correction = version("a", 10, 300, { version_id: "v2", source_revision: 2, value: 2.6 });
    const retraction = version("a", 10, 420, {
      version_id: "v3",
      source_revision: 3,
      operation: "retract",
      event_time: null,
      value: null,
      status: "not_yet_available",
    });
    const block = layout([retraction, first, correction]).groups.find((one) => one.eventType === "lab.lactate")?.blocks[0];
    expect(block?.versions.map((item) => item.label)).toEqual(["v1", "v2", "v3 retraction"]);
    const [one, two, three] = block?.versions ?? [];
    expect((two?.y ?? 0) - (one?.y ?? 0)).toBe(VERSION_ROW);
    expect((three?.y ?? 0) - (two?.y ?? 0)).toBe(VERSION_ROW);
    // A retraction has no event time of its own: it is drawn from the observation's.
    expect(three?.eventBorrowed).toBe(true);
    expect(three?.xEvent).toBeCloseTo(one?.xEvent ?? -1, 6);
  });

  it("leaves out observations wholly outside the range, and counts them", () => {
    const result = layout([version("old", 60, 30), version("in", 5, 30), version("later", -20, 30)]);
    const group = result.groups.find((one) => one.eventType === "lab.lactate");
    expect(group?.blocks.map((block) => block.id)).toEqual(["in"]);
    expect(group).toMatchObject({ total: 3, earlier: 1, later: 1 });
    expect([...observationsInRange([version("old", 60, 30), version("in", 5, 30), version("later", -20, 30)], view)]).toEqual(["in"]);
  });

  it("keeps an observation whose result arrives inside the range although it was measured before it", () => {
    const late = version("late", 49, 180);
    expect(observationsInRange([late], view).has("late")).toBe(true);
    expect(layout([late]).visible).toHaveLength(1);
  });

  it("lists event types in the given order, then any others it finds", () => {
    const result = layout([version("x", 3, 0, { event_type: "lab.sodium" }), version("a", 5, 30)]);
    expect(result.groups.map((group) => group.eventType)).toEqual(["vital.heart_rate", "lab.lactate", "lab.sodium"]);
    expect(result.groups[0]?.blocks).toEqual([]);
  });
});

describe("version helpers", () => {
  it("falls back to the pipeline clock when the policy has none", () => {
    const naive = version("a", 5, 90, { known_time: null });
    expect(knownTime(naive)).toBe(cutoff - 5 * HOUR + 90 * MINUTE);
    expect(knownTime(version("a", 5, 90, { known_time: null, pipeline_available_time: null }))).toBeNull();
  });

  it("flags a version used although the pipeline received it after the cutoff", () => {
    const leak = version("a", 1, 120, { status: "used", known_time: null });
    expect(usedBeforePipelineHadIt(leak, cutoff)).toBe(true);
    expect(usedBeforePipelineHadIt(version("b", 3, 60), cutoff)).toBe(false);
    expect(usedBeforePipelineHadIt({ ...leak, status: "not_yet_available" }, cutoff)).toBe(false);
    expect(usedBeforePipelineHadIt(leak, null)).toBe(false);
  });

  it("describes the delay between measurement and availability", () => {
    expect(describeDelay(cutoff, cutoff)).toBe("no delay");
    expect(describeDelay(cutoff, cutoff + 100 * MINUTE)).toBe("1 h 40 min after the event");
    expect(describeDelay(cutoff, cutoff - 5 * MINUTE)).toBe("5 min before the event");
  });
});
