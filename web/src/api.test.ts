import { describe, expect, it } from "vitest";
import { describeErrorBody, endpoints, isPolicy } from "./api";
import { formatHash, parseHash } from "./hooks";

describe("describeErrorBody", () => {
  it("passes on the API's plain message and technical detail", () => {
    const body = { detail: { message: "That patient is not in this run.", technical: "entity_id 'X' not found." } };
    expect(describeErrorBody(404, body)).toEqual({
      message: "That patient is not in this run.",
      technical: "entity_id 'X' not found.",
    });
  });

  it("summarises a validation error from the framework", () => {
    const body = { detail: [{ type: "less_than_equal", loc: ["query", "limit"], msg: "Input should be less than or equal to 500" }] };
    expect(describeErrorBody(422, body)).toEqual({
      message: "The request was not valid for this API.",
      technical: "query.limit: Input should be less than or equal to 500",
    });
  });

  it("falls back to a plain sentence for other bodies, keeping the body as the technical part", () => {
    expect(describeErrorBody(404, { detail: "Not Found" })).toEqual({
      message: "The requested data were not found on this machine.",
      technical: "Not Found",
    });
    expect(describeErrorBody(500, "Internal Server Error")).toEqual({
      message: "The local API failed while answering this request.",
      technical: "Internal Server Error",
    });
    expect(describeErrorBody(400, "").message).toBe("The local API rejected this request.");
  });
});

describe("endpoints", () => {
  it("encodes identifiers and the offset of the cutoff", () => {
    // An unencoded "+" in a query string would arrive at the API as a space.
    expect(endpoints.timeline("run a", "P/1", "2025-02-17T07:52:00+00:00", "naive_event_time").url).toBe(
      "/api/runs/run%20a/patients/P%2F1/timeline?cutoff=2025-02-17T07%3A52%3A00%2B00%3A00&policy=naive_event_time",
    );
    expect(endpoints.comparison("r", { onlyChanged: true, offset: 50, limit: 25 }).url).toBe(
      "/api/runs/r/comparison?only_changed=true&offset=50&limit=25",
    );
    expect(endpoints.patients("r", { search: "p 1", onlyWithFindings: false, limit: 50 }).url).toBe(
      "/api/runs/r/patients?search=p+1&only_with_findings=false&limit=50",
    );
  });

  it("recognises the four policies only", () => {
    expect(isPolicy("naive_event_time")).toBe(true);
    expect(isPolicy("pipeline_as_known")).toBe(true);
    expect(isPolicy("as_known")).toBe(false);
    expect(isPolicy(undefined)).toBe(false);
  });
});

describe("hash routes", () => {
  it("reads the view and its parameters", () => {
    expect(parseHash("#/timeline?run=a&patient=P000145&t=2025-02-17T07%3A52%3A00Z")).toEqual({
      view: "timeline",
      params: { run: "a", patient: "P000145", t: "2025-02-17T07:52:00Z" },
    });
    expect(parseHash("")).toEqual({ view: "overview", params: {} });
    expect(parseHash("#/no-such-view?run=a")).toEqual({ view: "overview", params: { run: "a" } });
  });

  it("writes a hash that reads back the same, leaving out empty parameters", () => {
    const hash = formatHash("comparison", { run: "seed 1/x", offset: "50", changed: undefined, limit: "" });
    expect(hash).toBe("#/comparison?run=seed+1%2Fx&offset=50");
    expect(parseHash(hash)).toEqual({ view: "comparison", params: { run: "seed 1/x", offset: "50" } });
    expect(formatHash("engineering")).toBe("#/engineering");
  });
});
