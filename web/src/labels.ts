/**
 * Plain-language names for the keys used in the result files.
 * The wording follows the static report (src/asoflab/audit/report.py). A key
 * without an entry is shown humanised rather than hidden.
 */

import { humanise } from "./format";

export const REFERENCE_POLICY = "pipeline_as_known";

const POLICY_LABELS: Record<string, string> = {
  pipeline_as_known: "As known to the pipeline",
  source_as_known: "As known in the source system",
  latest_revised: "Latest revised",
  naive_event_time: "Naive (event time only)",
};

/** One line per policy, restating section 4 of docs/temporal-contract.md. */
const POLICY_SUMMARIES: Record<string, string> = {
  pipeline_as_known: "A version is usable once the pipeline had received it; reads the highest revision usable then.",
  source_as_known: "A version is usable once it existed in the source system; reads the highest revision usable then.",
  latest_revised: "Usable once any version had arrived, but reads the highest revision in the archive.",
  naive_event_time: "Ignores availability and reads the highest revision in the archive.",
};

const METRIC_LABELS: Record<string, string> = {
  auroc: "AUROC",
  average_precision: "Average precision",
  brier: "Brier score",
  calibration_intercept: "Calibration intercept",
  calibration_slope: "Calibration slope",
};

const COMPARISON_LABELS: Record<string, string> = {
  source_as_known_minus_pipeline_as_known: "Source system minus pipeline",
  latest_revised_minus_pipeline_as_known: "Latest revised minus as-known",
  naive_event_time_minus_pipeline_as_known: "Naive minus as-known",
  revision_effect: "Revision effect (latest revised minus as-known)",
  availability_effect: "Availability effect (naive minus latest revised)",
  optimism_of_the_naive_pipeline: "Reported minus delivered, naive pipeline",
  as_known_training_minus_naive_training_at_deployment: "As-known training minus naive training, both delivered",
  reported_naive_minus_reported_as_known: "Naive pipeline's report minus as-known pipeline's report",
  naive_minus_as_known: "Naive minus as-known",
  source_minus_pipeline: "Source system minus pipeline",
};

/** Short forms for chart rows, where the chart title carries the rest. */
const COMPARISON_SHORT: Record<string, string> = {
  source_as_known_minus_pipeline_as_known: "Source system",
  latest_revised_minus_pipeline_as_known: "Latest revised",
  naive_event_time_minus_pipeline_as_known: "Naive",
  revision_effect: "Revision effect",
  availability_effect: "Availability effect",
  optimism_of_the_naive_pipeline: "Reported minus delivered (naive pipeline)",
  as_known_training_minus_naive_training_at_deployment: "As-known minus naive training (delivered)",
  reported_naive_minus_reported_as_known: "Naive report minus as-known report",
};

const CONDITION_LABELS: Record<string, string> = {
  naive_trained__naive_evaluated: "Trained naive, evaluated naive (what a naive pipeline reports)",
  naive_trained__as_known_evaluated: "Trained naive, evaluated as known (what it would deliver)",
  as_known_trained__as_known_evaluated: "Trained as known, evaluated as known",
  as_known_trained__naive_evaluated: "Trained as known, evaluated naive",
  naive_trained: "Trained on naive features",
  as_known_trained: "Trained on as-known features",
  naive_evaluated: "Evaluated on naive features",
  as_known_evaluated: "Evaluated on as-known features",
};

const lookup = (table: Record<string, string>, key: string) => table[key] ?? humanise(key);

export const policyLabel = (slug: string) => lookup(POLICY_LABELS, slug);
export const policySummary = (slug: string) => POLICY_SUMMARIES[slug] ?? "";
export const metricLabel = (name: string) => lookup(METRIC_LABELS, name);
export const comparisonLabel = (key: string) => lookup(COMPARISON_LABELS, key);
export const comparisonShort = (key: string) => COMPARISON_SHORT[key] ?? comparisonLabel(key);
export const conditionLabel = (key: string) => CONDITION_LABELS[key] ?? policyLabel(key);

/** The metrics every comparison table and forest plot shows, in this order. */
export const HEADLINE_METRICS = ["auroc", "average_precision", "brier"] as const;

/** The two differences that split the naive-minus-as-known gap. */
export const DECOMPOSITION_KEYS: readonly string[] = ["revision_effect", "availability_effect"];
export const TOTAL_GAP_KEY = "naive_event_time_minus_pipeline_as_known";

/** Decimals: Brier scores and all differences are small, so they get one more. */
export function metricDigits(metric: string, difference: boolean): number {
  if (difference) return metric === "brier" ? 5 : 4;
  return metric === "brier" ? 4 : 3;
}

/* ---------------------------------------------------------------- statuses */

export type Glyph = "disc" | "ring" | "cross" | "square" | "diamond" | "triangle" | "question" | "dot";
export type Tone = "used" | "wait" | "off" | "retract";

export interface StatusMeta {
  label: string;
  glyph: Glyph;
  tone: Tone;
}

const STATUS_META: Record<string, StatusMeta> = {
  used: { label: "Used", glyph: "disc", tone: "used" },
  not_yet_available: { label: "Not yet available", glyph: "ring", tone: "wait" },
  superseded: { label: "Superseded", glyph: "cross", tone: "off" },
  retraction_in_force: { label: "Retraction in force", glyph: "square", tone: "retract" },
  outside_window: { label: "Outside window", glyph: "diamond", tone: "off" },
  event_after_cutoff: { label: "Event after cutoff", glyph: "triangle", tone: "wait" },
  unknown_availability: { label: "Unknown availability", glyph: "question", tone: "wait" },
  not_referenced: { label: "Not referenced", glyph: "dot", tone: "off" },
};

/** Statuses in the order the legend lists them. A status the API adds later is listed after these. */
export const STATUS_ORDER: readonly string[] = Object.keys(STATUS_META);

export function statusMeta(status: string): StatusMeta {
  return STATUS_META[status] ?? { label: humanise(status), glyph: "question", tone: "off" };
}

/** CSS class for a finding severity tag; the tag always carries the severity as text too. */
export function severityClass(severity: string): string {
  if (severity === "high") return "tag high";
  if (severity === "medium") return "tag warn";
  return "tag";
}
