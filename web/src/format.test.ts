import { describe, expect, it } from "vitest";
import {
  describeEstimate,
  formatBytes,
  formatClock,
  formatCount,
  formatDuration,
  formatEstimate,
  formatLevel,
  formatNumber,
  formatPercent,
  formatUtc,
  formatValue,
  formatWindow,
  humanise,
  median,
  parseTime,
  plural,
  shortHash,
  toApiTime,
} from "./format";

describe("formatNumber", () => {
  it("uses fixed decimals and an optional sign", () => {
    expect(formatNumber(0.7163545, 3)).toBe("0.716");
    expect(formatNumber(-0.003825, 4, true)).toBe("-0.0038");
    expect(formatNumber(0.004452, 4, true)).toBe("+0.0045");
  });

  it("prints an exact zero without a sign", () => {
    expect(formatNumber(0, 4, true)).toBe("0.0000");
    expect(formatNumber(-0, 3)).toBe("0.000");
  });

  it("does not round a small non-zero value to zero", () => {
    // The lower bound of an interval that excludes zero must not read as "0.0000".
    expect(formatNumber(2.8664e-5, 4, true)).toBe("+2.9e-5");
    expect(formatNumber(2.71e-6, 5, true)).toBe("+2.7e-6");
    expect(formatNumber(-4e-7, 4)).toBe("-4.0e-7");
    expect(formatNumber(-1.5708e-5, 5, true)).toBe("-0.00002");
  });

  it("never shows a missing value as a number", () => {
    for (const missing of [null, undefined, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(formatNumber(missing)).toBe("not available");
    }
  });
});

describe("estimates with intervals", () => {
  it("shows the point with its interval", () => {
    const interval = { point: 0.7163545, lower: 0.6439514, upper: 0.7897789, reason: null };
    expect(describeEstimate(interval)).toEqual({ kind: "interval", text: "0.716 (0.644 to 0.790)" });
    expect(formatEstimate({ point: -0.003825, lower: -0.010295, upper: 0.0019226 }, 4, true)).toBe(
      "-0.0038 (-0.0103 to +0.0019)",
    );
  });

  it("gives the reason when there is a point but no interval", () => {
    const reason = "fewer than 95% of the resamples could be evaluated";
    expect(describeEstimate({ point: 0.5, lower: null, upper: null, reason })).toEqual({
      kind: "point-only",
      text: `0.500 (no interval: ${reason})`,
      reason,
    });
  });

  it("says not estimable, with the reason, when there is no point", () => {
    const reason = "only one outcome class in the sample";
    const estimate = describeEstimate({ point: null, lower: null, upper: null, reason });
    expect(estimate).toEqual({ kind: "not-estimable", text: `not estimable: ${reason}`, reason });
    expect(estimate.text).not.toMatch(/\d/); // in particular, never "0"
  });

  it("still explains itself when no reason was recorded, or the statistic is absent", () => {
    expect(formatEstimate({ point: null, lower: null, upper: null, reason: null })).toBe(
      "not estimable: no reason recorded",
    );
    expect(formatEstimate({ point: null, lower: null, upper: null, reason: "  " })).toBe(
      "not estimable: no reason recorded",
    );
    expect(formatEstimate(undefined)).toBe("not estimable: this statistic is not in the results file");
  });
});

describe("counts, shares and sizes", () => {
  it("formats counts, percentages and levels", () => {
    expect(formatCount(13225)).toBe("13,225");
    expect(formatCount(null)).toBe("not available");
    expect(formatPercent(0.4542)).toBe("45.4%");
    expect(formatPercent(undefined)).toBe("not available");
    expect(formatLevel(0.95)).toBe("95%");
    expect(formatLevel(0.975)).toBe("97.5%");
    expect(formatLevel(undefined)).toBe("bootstrap");
  });

  it("formats measured values without trailing zeros", () => {
    expect(formatValue(90.66666666666667)).toBe("90.667");
    expect(formatValue(2.1)).toBe("2.1");
    expect(formatValue(20)).toBe("20");
    expect(formatValue(-0.0001, 3)).toBe("0");
    expect(formatValue(null)).toBe("not available");
  });

  it("formats bytes in binary units", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1536)).toBe("1.5 KiB");
    expect(formatBytes(178_413_568)).toBe("170 MiB");
    expect(formatBytes(19_327_352_832)).toBe("18.0 GiB");
    expect(formatBytes(null)).toBe("not available");
  });
});

describe("time", () => {
  const instant = Date.UTC(2025, 1, 17, 7, 52, 0);

  it("parses instants with an explicit offset", () => {
    expect(parseTime("2025-02-17T07:52:00+00:00")).toBe(instant);
    expect(parseTime("2025-02-17T07:52:00Z")).toBe(instant);
    expect(parseTime("2025-02-17T02:52:00-05:00")).toBe(instant);
    expect(parseTime("2025-02-17 07:52:00+0000")).toBe(instant);
    expect(parseTime("2025-02-17T07:52:00.123456+00:00")).toBe(instant + 123);
  });

  it("refuses a time without an offset instead of guessing a zone", () => {
    expect(parseTime("2025-02-17T07:52:00")).toBeNull();
    expect(parseTime("not a time")).toBeNull();
    expect(parseTime("")).toBeNull();
    expect(parseTime(null)).toBeNull();
  });

  it("prints UTC, with seconds only when they are not zero", () => {
    expect(formatUtc(instant)).toBe("2025-02-17 07:52 UTC");
    expect(formatUtc(instant, false)).toBe("2025-02-17 07:52");
    expect(formatUtc(Date.UTC(2025, 1, 18, 4, 56, 50))).toBe("2025-02-18 04:56:50 UTC");
    expect(formatClock(instant)).toBe("07:52");
    expect(formatUtc(null)).toBe("not available");
  });

  it("writes the cutoff the API expects", () => {
    expect(toApiTime(instant)).toBe("2025-02-17T07:52:00+00:00");
    expect(parseTime(toApiTime(instant + 400))).toBe(instant);
  });

  it("describes durations and lookback windows", () => {
    expect(formatDuration(0)).toBe("0 s");
    expect(formatDuration(45_000)).toBe("45 s");
    expect(formatDuration(90_000)).toBe("1 min 30 s");
    expect(formatDuration(5_400_000)).toBe("1 h 30 min");
    expect(formatDuration((51 * 60 + 5) * 60_000)).toBe("2 d 3 h");
    expect(formatDuration(-3_600_000)).toBe("-1 h");
    expect(formatDuration(null)).toBe("not available");
    expect(formatWindow(14_400)).toBe("4 h");
    expect(formatWindow(172_800)).toBe("48 h");
    expect(formatWindow(5_400)).toBe("90 min");
    expect(formatWindow(14 * 86_400)).toBe("14 d");
    expect(formatWindow(45)).toBe("45 s");
  });
});

describe("words", () => {
  it("humanises keys and counts", () => {
    expect(humanise("used_before_available")).toBe("Used before available");
    expect(humanise("naive_trained__naive_evaluated")).toBe("Naive trained, naive evaluated");
    expect(plural(1, "finding")).toBe("1 finding");
    expect(plural(2, "finding")).toBe("2 findings");
    expect(plural(4, "policy", "policies")).toBe("4 policies");
    expect(shortHash("abcdef0123456789", 8)).toBe("abcdef01");
    expect(shortHash(null)).toBe("not available");
  });

  it("takes medians", () => {
    expect(median([3, 1, 2])).toBe(2);
    expect(median([1, 2, 3, 4])).toBe(2.5);
    expect(median([])).toBeNull();
  });
});
