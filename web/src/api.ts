/**
 * Types for the JSON the local AsOfLab API returns, and typed endpoints.
 *
 * The shapes follow src/asoflab/api/app.py and the files a run writes
 * (manifest.json, statistics.json, audit.json). Files under results/ that may
 * not have been generated yet (recovery, benchmarks, cross-seed summary) are
 * typed with optional fields, and the views check before they read.
 */

export const POLICIES = ["pipeline_as_known", "source_as_known", "latest_revised", "naive_event_time"] as const;
export type PolicySlug = (typeof POLICIES)[number];

export function isPolicy(value: string | undefined): value is PolicySlug {
  return value !== undefined && (POLICIES as readonly string[]).includes(value);
}

/* ------------------------------------------------------------------ errors */

export type ApiErrorKind = "http" | "network" | "parse";

/** A failed request: `message` is plain language, `technical` is for a developer. */
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly technical: string;
  readonly url: string;

  constructor(kind: ApiErrorKind, message: string, technical: string, url: string, status: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
    this.technical = technical;
    this.url = url;
  }
}

/** True for a JSON object (not an array, not null). */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Turn an error body into the two fields the UI shows.
 * The API sends {"detail": {"message", "technical"}}; FastAPI's own validation
 * errors send {"detail": [{loc, msg, ...}]}, and a proxy may send plain text.
 */
export function describeErrorBody(status: number, body: unknown): { message: string; technical: string } {
  const fallback =
    status === 404
      ? "The requested data were not found on this machine."
      : status >= 500
        ? "The local API failed while answering this request."
        : "The local API rejected this request.";
  const detail = isRecord(body) ? body["detail"] : undefined;
  if (isRecord(detail)) {
    const message = typeof detail["message"] === "string" ? detail["message"] : fallback;
    const technical = typeof detail["technical"] === "string" ? detail["technical"] : JSON.stringify(detail);
    return { message, technical };
  }
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (!isRecord(item)) return String(item);
      const where = Array.isArray(item["loc"]) ? item["loc"].join(".") : "request";
      return `${where}: ${typeof item["msg"] === "string" ? item["msg"] : JSON.stringify(item)}`;
    });
    return { message: "The request was not valid for this API.", technical: parts.join("; ") };
  }
  if (typeof detail === "string") return { message: fallback, technical: detail };
  if (typeof body === "string" && body.trim() !== "") return { message: fallback, technical: body.trim().slice(0, 500) };
  return { message: fallback, technical: "The response had no error detail." };
}

export async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, { signal: signal ?? null, headers: { Accept: "application/json" } });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(
      "network",
      "The local API did not answer. Check that the asoflab serve command is still running on this machine.",
      `GET ${url} failed before a response arrived: ${error instanceof Error ? error.message : String(error)}`,
      url,
    );
  }
  const text = await response.text();
  let body: unknown = text;
  let parsed = false;
  try {
    body = JSON.parse(text);
    parsed = true;
  } catch {
    // Not JSON: handled below, as an error body or as a parse failure.
  }
  if (!response.ok) {
    const { message, technical } = describeErrorBody(response.status, body);
    throw new ApiError("http", message, `GET ${url} returned HTTP ${response.status}. ${technical}`, url, response.status);
  }
  if (!parsed) {
    throw new ApiError(
      "parse",
      "The local API answered, but not with data this page can read.",
      `GET ${url} returned HTTP ${response.status} with a body that is not JSON: ${text.slice(0, 200)}`,
      url,
      response.status,
    );
  }
  return body as T;
}

/* --------------------------------------------------------------- endpoints */

/** A URL together with the type of the document it returns. */
export interface Endpoint<T> {
  readonly url: string;
  /** Phantom field: carries the response type, never set at run time. */
  readonly __response?: T;
}

function endpoint<T>(url: string): Endpoint<T> {
  return { url };
}

const run = (runId: string) => `/api/runs/${encodeURIComponent(runId)}`;

export const endpoints = {
  health: () => endpoint<Health>("/api/health"),
  runs: () => endpoint<RunListing[]>("/api/runs"),
  manifest: (runId: string) => endpoint<Manifest>(`${run(runId)}/manifest`),
  statistics: (runId: string) => endpoint<Statistics>(`${run(runId)}/statistics`),
  audit: (runId: string) => endpoint<Audit>(`${run(runId)}/audit`),
  patients: (runId: string, query: { search: string; onlyWithFindings: boolean; limit: number }) =>
    endpoint<PatientList>(
      `${run(runId)}/patients?${new URLSearchParams({
        search: query.search,
        only_with_findings: String(query.onlyWithFindings),
        limit: String(query.limit),
      })}`,
    ),
  patient: (runId: string, entityId: string) =>
    endpoint<PatientDetail>(`${run(runId)}/patients/${encodeURIComponent(entityId)}`),
  /** `cutoff` must be ISO-8601 with an explicit offset; URLSearchParams encodes the "+". */
  timeline: (runId: string, entityId: string, cutoff: string, policy: string) =>
    endpoint<Timeline>(
      `${run(runId)}/patients/${encodeURIComponent(entityId)}/timeline?${new URLSearchParams({ cutoff, policy })}`,
    ),
  comparison: (runId: string, query: { onlyChanged: boolean; offset: number; limit: number }) =>
    endpoint<Comparison>(
      `${run(runId)}/comparison?${new URLSearchParams({
        only_changed: String(query.onlyChanged),
        offset: String(query.offset),
        limit: String(query.limit),
      })}`,
    ),
  engineering: () => endpoint<Engineering>("/api/engineering"),
};

/* ------------------------------------------------------------ shared types */

/** One estimate with its bootstrap interval. `point`, `lower`, `upper` may be null; `reason` then says why. */
export interface Interval {
  point: number | null;
  lower: number | null;
  upper: number | null;
  estimable?: boolean;
  bootstrap_requested?: number | null;
  bootstrap_completed?: number | null;
  reason?: string | null;
}

export interface Health {
  status: string;
  asoflab_version: string;
  contract_version: string;
}

export interface RunListing {
  run_id: string;
  scenario: string | null;
  seed: number | null;
  created_at: string | null;
  synthetic_data: boolean | null;
  patients: number | null;
  predictions: number | null;
  has_patient_data: boolean;
}

export interface Environment {
  asoflab_version?: string;
  contract_version?: string;
  python?: string;
  implementation?: string;
  platform?: string;
  machine?: string;
  processor?: string;
  logical_cpus?: number;
  physical_cpus?: number;
  memory_bytes?: number;
  libraries?: Record<string, string>;
  code_commit?: string | null;
  code_dirty?: boolean | null;
}

export interface Scenario {
  name: string;
  description: string;
  [setting: string]: string | boolean;
}

export interface FeatureDefinition {
  name: string;
  event_type: string;
  aggregation: string;
  window_seconds: number;
  scope?: string;
}

/* ---------------------------------------------------------------- manifest */

export interface TimePolicyEntry {
  policy: string;
  strictness: string;
  faithful_replay: boolean;
  feature_table_hash?: string;
}

export interface Manifest {
  manifest_version?: number;
  run_id: string;
  kind?: string;
  created_at?: string;
  synthetic_data?: boolean;
  identity?: {
    generator_version?: string;
    generator_config_hash?: string;
    feature_config_hash?: string;
    contract_version?: string;
    asoflab_version?: string;
    analysis_settings_hash?: string;
  };
  generator?: {
    version?: string;
    config?: {
      scenario?: Scenario;
      seed?: number;
      n_patients?: number;
      study_start?: string;
      study_days?: number;
    };
  };
  seed?: number;
  scenario?: string;
  data_hashes?: Record<string, string>;
  feature_config?: { feature_set_version?: string; features?: FeatureDefinition[] };
  time_policies?: TimePolicyEntry[];
  reference_policy?: string;
  environment?: Environment;
  note?: string;
}

/* -------------------------------------------------------------- statistics */

export type MetricSet = Record<string, Interval | undefined>;

export interface Difference {
  minuend?: string;
  subtrahend?: string;
  auroc?: Interval;
  average_precision?: Interval;
  brier?: Interval;
}

export interface MetricBlock {
  conditions?: Record<string, MetricSet>;
  differences?: Record<string, Difference>;
}

export interface Sample {
  predictions: number;
  patients: number;
  events: number;
  non_events?: number;
  prevalence?: number;
  patients_with_an_event?: number;
}

export interface ExperimentA {
  question?: string;
  interpretation?: string;
  model_trained_under?: string;
  evaluated_on?: string;
  sample?: Sample;
  instability_flags?: string[];
  prediction_weighted?: MetricBlock;
  patient_weighted?: MetricBlock;
  decomposition_note?: string;
}

export interface ExperimentB {
  question?: string;
  design?: string;
  deployment_faithful_cell?: string;
  sample?: Sample;
  prediction_weighted?: MetricBlock;
  can_attribute?: string;
  cannot_attribute?: string;
}

export interface Statistics {
  run_id?: string;
  synthetic_data?: boolean;
  scenario?: Scenario;
  seed?: number;
  target?: Record<string, string | number>;
  cohort?: Record<string, number>;
  label_coverage?: {
    predictions?: number;
    labelled?: number;
    unknown?: number;
    coverage?: number;
    by_status?: Record<string, number>;
  };
  weighting?: string;
  bootstrap?: {
    unit_resampled?: string;
    paired_across_conditions?: boolean;
    replicates_requested?: number;
    seed?: number;
    level?: number;
    interval_method?: string;
    min_completed_fraction?: number;
  };
  experiment_a?: ExperimentA;
  experiment_b?: ExperimentB;
  limits?: string[];
}

/* ------------------------------------------------------------------- audit */

export interface FindingType {
  count: number;
  severity: string;
  meaning: string;
  remediation?: string;
}

export interface Differential {
  reference_policy?: string;
  candidate_policy?: string;
  scope?: string;
  predictions: number;
  predictions_with_findings: number;
  findings_total: number;
  findings_shown?: number;
  by_type?: Record<string, FindingType>;
  by_event_type?: Record<string, Record<string, number>>;
}

export interface ParityEntry {
  patients?: number;
  left?: string;
  right?: string;
  rows_left?: number;
  rows_right?: number;
  equivalent: boolean;
  n_mismatches?: number;
  max_abs_difference_within_tolerance?: number | null;
}

export interface ValidationFinding {
  code: string;
  count?: number;
  message?: string;
  severity?: string;
}

export interface Audit {
  synthetic_data?: boolean;
  validation?: { ok?: boolean; counts?: Record<string, number>; findings?: ValidationFinding[] };
  policies?: Record<string, { strictness: string; faithful_replay: boolean }>;
  feature_differences_from_reference?: Record<
    string,
    { predictions_with_any_feature_changed: number; share_of_predictions: number; by_feature?: Record<string, number> }
  >;
  parity?: Record<string, Record<string, ParityEntry>>;
  differential?: Differential;
  differential_latest_revised?: Differential;
}

/* ---------------------------------------------------------------- patients */

export interface PatientRow {
  entity_id: string;
  site?: string | null;
  age_band?: string | null;
  predictions: number;
  findings: number;
  split: string | null;
}

export interface PatientList {
  total: number;
  shown: number;
  patients: PatientRow[];
}

export interface Encounter {
  entity_id: string;
  encounter_id: string;
  encounter_start: string | null;
  encounter_end: string | null;
}

export interface Outcome {
  outcome_event_id: string;
  entity_id: string;
  encounter_id?: string | null;
  outcome_type: string;
  event_time: string | null;
  ascertainment_time: string | null;
}

export interface PatientPrediction {
  prediction_id: string;
  entity_id: string;
  encounter_id?: string | null;
  prediction_time: string;
  label: number | null;
  label_status: string;
  label_mature_time?: string | null;
  split: string | null;
  p_as_known_model__pipeline_as_known?: number | null;
  p_as_known_model__naive_event_time?: number | null;
}

export interface PatientFinding {
  prediction_id: string;
  entity_id: string;
  logical_event_id: string;
  event_type: string;
  finding_type: string;
}

export type FeatureValues = Record<string, number | null | undefined>;

export interface PatientDetail {
  synthetic_data: boolean;
  patient: { entity_id: string; site?: string | null; age_band?: string | null };
  encounters: Encounter[];
  outcomes: Outcome[];
  event_versions: number;
  predictions: PatientPrediction[];
  feature_names: string[];
  findings: PatientFinding[];
}

/** One version of one observation, with the oracle's decision at the requested cutoff. */
export interface TimelineVersion {
  entity_id: string;
  encounter_id?: string | null;
  logical_event_id: string;
  version_id: string;
  source_revision: number;
  operation: string;
  event_type: string;
  event_time: string | null;
  value: number | null;
  unit?: string | null;
  source?: string;
  source_available_time: string | null;
  source_availability_provenance?: string;
  pipeline_available_time: string | null;
  pipeline_availability_provenance?: string;
  /** Availability on the selected policy's clock. Null when the policy has no clock (naive). */
  known_time: string | null;
  status: string;
  reason: string;
  used_by: string[];
}

export interface Timeline {
  synthetic_data: boolean;
  entity_id: string;
  cutoff: string;
  policy: string;
  strictness: string;
  faithful_replay: boolean;
  computed_by: string;
  status_counts: Record<string, number>;
  versions: TimelineVersion[];
  features: Record<string, FeatureValues>;
  feature_names: string[];
}

/* -------------------------------------------------------------- comparison */

export interface ComparisonRow {
  prediction_id: string;
  entity_id: string;
  prediction_time: string;
  split: string | null;
  label: number | null;
  label_status: string;
  risk_as_known: number | null;
  risk_naive: number | null;
  features_changed: number;
  changed: string[];
  as_known: FeatureValues;
  naive: FeatureValues;
}

export interface Comparison {
  synthetic_data: boolean;
  reference_policy: string;
  candidate_policy: string;
  risk_model: string;
  feature_names: string[];
  predictions_total: number;
  predictions_matching: number;
  offset: number;
  rows: ComparisonRow[];
}

/* ------------------------------------------------------------- engineering */

export interface SeededBug {
  what_it_gets_wrong?: string;
  fixtures_that_expose_it?: string[];
  discovery?: {
    trials?: number;
    found_in_trials?: number;
    archives_until_first_exposure?: (number | null)[];
    median?: number | null;
    worst?: number | null;
  };
}

export interface SqlMutation {
  replaced?: string;
  with?: string;
  fixtures_that_expose_it?: string[];
}

export interface SeededBugs {
  generated_at?: string;
  command?: string;
  environment?: Environment;
  scope?: string;
  archive_budget_per_trial?: number;
  control?: { fixtures_that_contradict_it?: string[]; exposed_within_budget?: boolean };
  python_bugs?: Record<string, SeededBug>;
  sql_mutations?: Record<string, SqlMutation>;
}

export interface RecoveryCase {
  crash_point?: string | number | null;
  chunk?: number | null;
  how?: string | null;
  exit_code?: number | null;
  resumed_from_offset?: number | null;
  outputs_equal_uninterrupted?: boolean | null;
  predictions?: number | null;
}

export interface Recovery {
  generated_at?: string;
  command?: string;
  environment?: Environment;
  scope?: string;
  /** The workload's settings: a description, or a set of named values. */
  workload?: unknown;
  /** What each crash point means, by name. */
  crash_points?: Record<string, string>;
  cases?: RecoveryCase[];
  all_recovered?: boolean | null;
}

export interface BenchmarkWorkload {
  workload_id: string;
  skew?: string;
  target_events?: number;
  event_versions?: number;
  logical_events?: number;
  entities?: number;
  predictions?: number;
  features?: number;
  /** A byte count, or one count per input file. */
  input_bytes?: number | Record<string, number> | null;
  generation_seconds?: number;
}

export type BenchmarkStatus = "ok" | "timeout" | "failed" | "skipped";

export interface BenchmarkRun {
  workload_id: string;
  engine: string;
  /** A named variation of an engine's configuration; "default" or absent for the plain one. */
  variant?: string | null;
  workers?: number | null;
  partitions?: number | null;
  status?: BenchmarkStatus | string;
  reason?: string | null;
  phases_seconds?: { prepare?: number[]; compute?: number[]; export?: number[] };
  end_to_end_seconds?: number[];
  median_end_to_end_seconds?: number | null;
  peak_rss_bytes?: number[];
  rows_out?: number | null;
  equivalent_to_reference?: boolean | null;
  reference_engine?: string;
  throughput_events_per_second?: number | null;
  speedup_vs_single_worker?: number | null;
}

export interface Benchmark {
  file: string;
  schema_version?: number;
  generated_at?: string;
  command?: string;
  profile?: string;
  environment?: Environment;
  settings?: {
    repetitions?: number;
    warmup?: string;
    cache?: string;
    timeout_seconds?: number;
    policy?: string;
    [other: string]: string | number | boolean | null | undefined;
  };
  workloads?: BenchmarkWorkload[];
  runs?: BenchmarkRun[];
  notes?: string[];
}

/** Mean and range of one quantity across seeds. `sd` is null with a single seed. */
export interface Spread {
  seeds?: number;
  mean?: number | null;
  sd?: number | null;
  min?: number | null;
  max?: number | null;
  interval_above_zero?: number;
  interval_below_zero?: number;
  interval_includes_zero?: number;
  no_interval?: number;
}

export interface SeedSummary {
  seed?: number;
  run?: string;
  code_commit?: string | null;
  code_dirty?: boolean | null;
  test_sample?: Sample;
  reference_metrics?: MetricSet;
  share_of_predictions_with_a_finding?: number;
  engines_agree?: boolean;
  contrasts?: Record<string, MetricSet>;
}

export interface ScenarioSummary {
  description?: string;
  seeds?: number[];
  per_seed?: SeedSummary[];
  across_seeds?: {
    contrasts?: Record<string, Record<string, Spread | null>>;
    share_of_predictions_with_a_finding?: Spread;
    reference_auroc?: Spread;
    all_engines_agree?: boolean;
  };
}

export interface ExperimentSummary {
  generated_at?: string;
  synthetic_data?: boolean;
  runs_included?: number;
  selection?: string;
  interpretation?: string;
  scenarios?: Record<string, ScenarioSummary>;
}

export interface Engineering {
  seeded_bugs: SeededBugs | null;
  recovery: Recovery | null;
  benchmarks: Benchmark[] | null;
  experiment_summary: ExperimentSummary | null;
  commands: Record<string, string> | null;
}
