import { describe, expect, it } from "vitest";
import { HOUR, MINUTE } from "./format";
import {
  clamp,
  formatTick,
  linear,
  logTicks,
  logarithmic,
  niceTicks,
  packLanes,
  snapTime,
  tickStep,
  timeTicks,
  timeToX,
  xToTime,
} from "./scale";

describe("niceTicks", () => {
  it("covers the range with steps of 1, 2 or 5 times a power of ten", () => {
    expect(niceTicks(0, 1)).toEqual([0, 0.2, 0.4, 0.6, 0.8, 1]);
    expect(niceTicks(0, 911)).toEqual([0, 200, 400, 600, 800, 1000]);
    expect(niceTicks(-0.0111, 0.0027, 4)).toEqual([-0.015, -0.01, -0.005, 0, 0.005]);
  });

  it("still brackets a single value, and survives bad input", () => {
    const ticks = niceTicks(5, 5);
    expect(ticks[0]).toBeLessThanOrEqual(5);
    expect(ticks[ticks.length - 1]).toBeGreaterThanOrEqual(5);
    expect(niceTicks(Number.NaN, 1)).toEqual([0, 1]);
  });

  it("labels ticks with the decimals the step needs", () => {
    const ticks = niceTicks(-0.0111, 0.0027, 4);
    const step = tickStep(ticks);
    expect(step).toBeCloseTo(0.005, 12);
    expect(ticks.map((tick) => formatTick(tick, step))).toEqual(["-0.015", "-0.010", "-0.005", "0", "0.005"]);
    expect(formatTick(200, 200)).toBe("200");
    expect(formatTick(0.4, 0.2)).toBe("0.4");
  });
});

describe("logTicks", () => {
  it("returns the powers of ten around the data", () => {
    expect(logTicks(0.058, 11.2)).toEqual([0.01, 0.1, 1, 10, 100]);
    expect(logTicks(100, 100)).toEqual([100, 1000]);
    expect(logTicks(1000, 0.001)).toEqual([0.001, 0.01, 0.1, 1, 10, 100, 1000]);
  });

  it("falls back for values a log axis cannot show", () => {
    expect(logTicks(0, 5)).toEqual([1, 10]);
    expect(logTicks(-1, 5)).toEqual([1, 10]);
  });
});

describe("scales", () => {
  it("maps linearly, and puts a zero-width domain in the middle", () => {
    const scale = linear(0, 10, 100, 200);
    expect(scale(0)).toBe(100);
    expect(scale(5)).toBe(150);
    expect(scale(10)).toBe(200);
    expect(linear(3, 3, 0, 80)(3)).toBe(40);
  });

  it("maps decades to equal distances", () => {
    const scale = logarithmic(0.01, 100, 0, 400);
    expect(scale(0.01)).toBeCloseTo(0, 9);
    expect(scale(1)).toBeCloseTo(200, 9);
    expect(scale(100)).toBeCloseTo(400, 9);
  });

  it("clamps", () => {
    expect(clamp(5, 0, 3)).toBe(3);
    expect(clamp(-1, 0, 3)).toBe(0);
    expect(clamp(2, 0, 3)).toBe(2);
  });
});

describe("time axis", () => {
  // The default view of the timeline: 48 hours before the cutoff, 12 after.
  const cutoff = Date.UTC(2025, 1, 17, 7, 52);
  const range = { start: cutoff - 48 * HOUR, end: cutoff + 12 * HOUR };

  it("places an instant in proportion to its position in the range", () => {
    expect(timeToX(range.start, range, 14, 314)).toBe(14);
    expect(timeToX(cutoff, range, 14, 314)).toBeCloseTo(254, 9);
    expect(timeToX(range.end, range, 14, 314)).toBe(314);
  });

  it("inverts a position back to the instant", () => {
    expect(xToTime(254, range, 14, 314)).toBeCloseTo(cutoff, 3);
    for (const x of [14, 77.5, 200, 314]) {
      expect(timeToX(xToTime(x, range, 14, 314), range, 14, 314)).toBeCloseTo(x, 9);
    }
    expect(xToTime(50, range, 20, 20)).toBe(range.start);
  });

  it("snaps a dragged position to the whole minute", () => {
    expect(snapTime(cutoff + 29_000, MINUTE)).toBe(cutoff);
    expect(snapTime(cutoff + 31_000, MINUTE)).toBe(cutoff + MINUTE);
  });

  it("puts ticks on round UTC steps and respects the limit", () => {
    const { ticks, step } = timeTicks(range, 5);
    expect(step).toBe(12 * HOUR);
    expect(ticks).toEqual([
      Date.UTC(2025, 1, 15, 12),
      Date.UTC(2025, 1, 16, 0),
      Date.UTC(2025, 1, 16, 12),
      Date.UTC(2025, 1, 17, 0),
      Date.UTC(2025, 1, 17, 12),
    ]);
    const fine = timeTicks(range, 15);
    expect(fine.step).toBe(6 * HOUR);
    expect(fine.ticks.length).toBeLessThanOrEqual(15);
    expect(fine.ticks.every((tick) => tick % (6 * HOUR) === 0 && tick >= range.start && tick <= range.end)).toBe(true);
  });
});

describe("packLanes", () => {
  it("gives overlapping spans different lanes and reuses a lane once it is free", () => {
    expect(packLanes([{ start: 0, end: 10 }, { start: 5, end: 15 }, { start: 12, end: 20 }])).toEqual([0, 1, 0]);
  });

  it("keeps the requested gap between neighbours", () => {
    expect(packLanes([{ start: 0, end: 10 }, { start: 11, end: 20 }], 3)).toEqual([0, 1]);
    expect(packLanes([{ start: 0, end: 10 }, { start: 13, end: 20 }], 3)).toEqual([0, 0]);
  });

  it("answers in the order of the input, whatever that order is", () => {
    expect(packLanes([{ start: 12, end: 20 }, { start: 0, end: 10 }, { start: 5, end: 15 }])).toEqual([0, 0, 1]);
    expect(packLanes([])).toEqual([]);
  });
});
