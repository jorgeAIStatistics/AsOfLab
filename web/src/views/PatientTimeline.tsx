/** View 2. One simulated patient's observations around a movable prediction cutoff. */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  POLICIES,
  endpoints,
  isPolicy,
  type FeatureDefinition,
  type Manifest,
  type PatientDetail,
  type PolicySlug,
  type RunListing,
  type Timeline,
  type TimelineVersion,
} from "../api";
import { StatusIcon, StatusLabel } from "../charts/StatusGlyph";
import { TimelineChart, type EncounterSpan, type OutcomeMark, type PredictionMark } from "../charts/TimelineChart";
import { describeDelay, knownTime, observationsInRange, usedBeforePipelineHadIt, versionKey } from "../charts/timelineLayout";
import { Empty, ErrorNotice, Loading, NoPatientData, Panel, Table, Tag, type Column } from "../components/ui";
import {
  HOUR,
  formatCount,
  formatNumber,
  formatUtc,
  formatValue,
  formatWindow,
  parseTime,
  plural,
  toApiTime,
} from "../format";
import { useDebounced, useLoad, type Navigate, type Route } from "../hooks";
import { REFERENCE_POLICY, STATUS_ORDER, policyLabel, policySummary, statusMeta } from "../labels";

interface ViewProps {
  run: RunListing;
  route: Route;
  navigate: Navigate;
}

export function PatientTimelineView({ run, route, navigate }: ViewProps) {
  const patientId = route.params["patient"] ?? null;
  if (!run.has_patient_data) {
    return (
      <>
        <h1 tabIndex={-1}>Patient timeline</h1>
        <NoPatientData what="The patient timeline" />
      </>
    );
  }
  return (
    <>
      <h1 tabIndex={-1}>Patient timeline</h1>
      <p className="lede">
        What was known about one simulated patient at a prediction cutoff, and which versions of each observation a
        time policy would use. Move the cutoff to see eligibility change.
      </p>
      <div className="split">
        <PatientPicker
          key={run.run_id}
          runId={run.run_id}
          selected={patientId}
          onSelect={(id, mode) => navigate("timeline", { run: run.run_id, patient: id, policy: route.params["policy"] }, mode)}
        />
        {/* Direct children of the grid: the chart sits beside the list, the two tables span the full width below. */}
        {patientId === null ? (
          <Empty>Choose a patient from the list to see its timeline.</Empty>
        ) : (
          <PatientPanel key={`${run.run_id}/${patientId}`} runId={run.run_id} patientId={patientId} route={route} navigate={navigate} />
        )}
      </div>
    </>
  );
}

/* ------------------------------------------------------------ patient list */

const PAGE = 50;
const API_LIMIT = 500;

function PatientPicker({
  runId,
  selected,
  onSelect,
}: {
  runId: string;
  selected: string | null;
  onSelect: (entityId: string, mode: "push" | "replace") => void;
}) {
  const [search, setSearch] = useState("");
  const [onlyWithFindings, setOnlyWithFindings] = useState(false);
  const [limit, setLimit] = useState(PAGE);
  const query = useDebounced(search.trim(), 200);
  const list = useLoad(endpoints.patients(runId, { search: query, onlyWithFindings, limit }), { keepPrevious: true });

  // With nothing chosen yet, open the patient with the most findings (the API's default order).
  // Wait for the list of this run: while it loads, `data` may still be the previous run's.
  const first = list.pending ? undefined : list.data?.patients[0]?.entity_id;
  useEffect(() => {
    if (selected === null && first !== undefined) onSelect(first, "replace");
    // onSelect is recreated on every render of the parent; only these two values decide.
  }, [selected, first]);

  return (
    <section className="picker" aria-labelledby="patients-heading">
      <h2 id="patients-heading">Patients</h2>
      <div className="controls">
        <label className="field">
          <span>Search by patient id</span>
          <input
            type="search"
            value={search}
            placeholder="for example P0001"
            onChange={(event) => {
              setSearch(event.target.value);
              setLimit(PAGE);
            }}
          />
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={onlyWithFindings}
            onChange={(event) => {
              setOnlyWithFindings(event.target.checked);
              setLimit(PAGE);
            }}
          />
          Only patients with findings
        </label>
      </div>
      {list.error ? (
        list.error.status === 404 ? (
          <NoPatientData what="The patient list" error={list.error} />
        ) : (
          <ErrorNotice error={list.error} what="the patient list" retry={list.retry} />
        )
      ) : list.data === undefined ? (
        <Loading what="patients" />
      ) : (
        <>
          <p className="status" role="status" aria-live="polite">
            {list.pending
              ? "Updating the list…"
              : list.data.total === 0
                ? "No patients to list."
                : `Showing ${formatCount(list.data.shown)} of ${plural(list.data.total, "patient")}, most findings first. Simulated.`}
          </p>
          {list.data.patients.length === 0 ? (
            <Empty>
              {query !== "" || onlyWithFindings
                ? "No patient matches this search. Clear the search or the findings filter."
                : "This run has no patients."}
            </Empty>
          ) : (
            <ul className="patient-list">
              {list.data.patients.map((patient) => (
                <li key={patient.entity_id}>
                  <button type="button" aria-pressed={patient.entity_id === selected} onClick={() => onSelect(patient.entity_id, "push")}>
                    {patient.entity_id}
                    <span className="sub">
                      {plural(patient.findings, "finding")}, {plural(patient.predictions, "prediction")}
                      {patient.split ? `, ${patient.split}` : ""}
                      {patient.site ? `, ${patient.site}` : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {list.data.shown < list.data.total &&
            (limit < API_LIMIT ? (
              <button type="button" onClick={() => setLimit(Math.min(limit + PAGE, API_LIMIT))}>
                Show more
              </button>
            ) : (
              <p className="muted">The API lists at most {API_LIMIT} patients at once. Narrow the search to see others.</p>
            ))}
        </>
      )}
    </section>
  );
}

/* ------------------------------------------------------------ one patient */

function PatientPanel({ runId, patientId, route, navigate }: { runId: string; patientId: string; route: Route; navigate: Navigate }) {
  const detail = useLoad(endpoints.patient(runId, patientId), { cache: true });
  const manifest = useLoad(endpoints.manifest(runId), { cache: true });

  if (detail.error) {
    // A 404 here is "not in this run" or "tables not on this machine"; the API's message says which.
    return <ErrorNotice error={detail.error} what={`patient ${patientId}`} retry={detail.retry} />;
  }
  if (detail.data === undefined) return <Loading what={`patient ${patientId}`} />;

  const predictions = predictionMarks(detail.data);
  const requested = parseTime(route.params["t"]);
  const firstEncounterEnd = parseTime(detail.data.encounters[0]?.encounter_end);
  const initial = requested ?? predictions.find((prediction) => prediction.findings > 0)?.time ?? predictions[0]?.time ?? firstEncounterEnd;
  if (initial === null) {
    return <Empty>Patient {patientId} has no predictions or encounters in this run, so there is no time to place a cutoff at.</Empty>;
  }
  return (
    <Workspace
      runId={runId}
      detail={detail.data}
      manifest={manifest.data}
      predictions={predictions}
      initialCutoff={Math.round(initial / 1000) * 1000}
      route={route}
      navigate={navigate}
    />
  );
}

function predictionMarks(detail: PatientDetail): PredictionMark[] {
  const findings = new Map<string, number>();
  for (const finding of detail.findings) findings.set(finding.prediction_id, (findings.get(finding.prediction_id) ?? 0) + 1);
  return detail.predictions
    .flatMap((prediction) => {
      const time = parseTime(prediction.prediction_time);
      return time === null ? [] : [{ id: prediction.prediction_id, time, findings: findings.get(prediction.prediction_id) ?? 0 }];
    })
    .sort((a, b) => a.time - b.time);
}

const urlTime = (ms: number) => new Date(ms).toISOString().replace(".000Z", "Z");

function Workspace({
  runId,
  detail,
  manifest,
  predictions,
  initialCutoff,
  route,
  navigate,
}: {
  runId: string;
  detail: PatientDetail;
  manifest: Manifest | undefined;
  predictions: PredictionMark[];
  initialCutoff: number;
  route: Route;
  navigate: Navigate;
}) {
  const patientId = detail.patient.entity_id;
  const requestedPolicy = route.params["policy"];
  const [policy, setPolicy] = useState<PolicySlug>(isPolicy(requestedPolicy) ? requestedPolicy : REFERENCE_POLICY);
  const [cutoff, setCutoff] = useState(initialCutoff);
  const [anchor, setAnchor] = useState(initialCutoff);
  const [range, setRange] = useState<number | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<string | null>(null);

  /* Lookback windows come from the run's own feature definitions. */
  const definitions = manifest?.feature_config?.features;
  const featuresByType = useMemo(() => {
    if (!definitions) return null;
    const map = new Map<string, FeatureDefinition[]>();
    for (const feature of definitions) map.set(feature.event_type, [...(map.get(feature.event_type) ?? []), feature]);
    return map;
  }, [definitions]);
  const typeOrder = useMemo(() => [...(featuresByType?.keys() ?? [])], [featuresByType]);
  const longestWindow = definitions?.length ? Math.max(...definitions.map((feature) => feature.window_seconds)) / 3600 : 48;
  const before = range ?? longestWindow;
  const after = before / 4;
  const view = { start: anchor - before * HOUR, end: anchor + after * HOUR };

  /* The cutoff line moves at once; the request waits until it has been still for 120 ms. */
  const settled = useDebounced(cutoff, 120);
  const timeline = useLoad(endpoints.timeline(runId, patientId, toApiTime(settled), policy), { keepPrevious: true });
  const stale = timeline.pending || settled !== cutoff;

  /* Keep cutoff and policy in the URL, and follow the URL when something else changes it. */
  const written = useRef({ t: route.params["t"], policy: requestedPolicy });
  const urlT = route.params["t"];
  useEffect(() => {
    if (urlT !== undefined && urlT !== written.current.t) {
      const time = parseTime(urlT);
      if (time !== null) {
        setCutoff(time);
        setAnchor(time);
      }
    }
    if (isPolicy(requestedPolicy) && requestedPolicy !== written.current.policy) setPolicy(requestedPolicy);
  }, [urlT, requestedPolicy]);
  useEffect(() => {
    written.current = { t: urlTime(settled), policy };
    navigate("timeline", { run: runId, patient: patientId, t: written.current.t, policy }, "replace");
  }, [settled, policy, runId, patientId, navigate]);

  const moveCutoff = (ms: number) => {
    const next = Math.round(ms / 1000) * 1000;
    setCutoff(next);
    if (next < view.start || next > view.end) setAnchor(next);
  };
  const jumpTo = (ms: number) => {
    setCutoff(ms);
    setAnchor(ms);
  };

  const atPrediction = predictions.find((prediction) => prediction.time === cutoff);
  const previous = [...predictions].reverse().find((prediction) => prediction.time < cutoff);
  const next = predictions.find((prediction) => prediction.time > cutoff);
  const stored = atPrediction ? detail.predictions.find((prediction) => prediction.prediction_id === atPrediction.id) : undefined;

  const encounters: EncounterSpan[] = detail.encounters.flatMap((encounter) => {
    const start = parseTime(encounter.encounter_start);
    const end = parseTime(encounter.encounter_end);
    return start === null || end === null ? [] : [{ id: encounter.encounter_id, start, end }];
  });
  const outcomes: OutcomeMark[] = detail.outcomes.flatMap((outcome) => {
    const time = parseTime(outcome.event_time);
    return time === null
      ? []
      : [{ id: outcome.outcome_event_id, type: outcome.outcome_type, time, ascertained: parseTime(outcome.ascertainment_time) }];
  });

  const data = timeline.data;
  const applied = data ? parseTime(data.cutoff) : null;
  const pipelineClockShown = data !== undefined && data.versions.length > 0 && data.versions.every((version) => version.known_time === null);
  const sampleNote = `Simulated data: 1 patient, ${plural(detail.event_versions, "event version")}, ${plural(detail.predictions.length, "prediction")}.`;

  return (
    <>
      <div className="workspace">
        <h2>
          Patient {patientId} <Tag tone="warn">simulated</Tag>
        </h2>
        <p className="muted">
          {[detail.patient.site, detail.patient.age_band && `age band ${detail.patient.age_band}`].filter(Boolean).join(", ")}
          {detail.patient.site || detail.patient.age_band ? ". " : ""}
          {plural(detail.encounters.length, "encounter")}, {plural(detail.event_versions, "event version")},{" "}
          {plural(detail.predictions.length, "prediction")}, {plural(detail.findings.length, "audit finding")} (naive policy
          against the reference).
        </p>

        <div className="controls">
          <fieldset className="field">
            <legend>Time policy</legend>
            <div className="segmented">
              {POLICIES.map((slug) => (
                <label key={slug} title={policySummary(slug)}>
                  <input type="radio" name="policy" value={slug} checked={policy === slug} onChange={() => setPolicy(slug)} />
                  {policyLabel(slug)}
                </label>
              ))}
            </div>
          </fieldset>
        </div>
        <div className="controls">
          <label className="field">
            <span>Jump to a prediction time (UTC)</span>
            <select
              value={atPrediction?.id ?? ""}
              onChange={(event) => {
                const target = predictions.find((prediction) => prediction.id === event.target.value);
                if (target) jumpTo(target.time);
              }}
            >
              {!atPrediction && <option value="">Cutoff is between prediction times</option>}
              {predictions.map((prediction) => (
                <option key={prediction.id} value={prediction.id}>
                  {formatUtc(prediction.time, false)}
                  {prediction.findings > 0 ? `, ${plural(prediction.findings, "finding")}` : ""}
                </option>
              ))}
            </select>
          </label>
          <div className="button-row">
            <button type="button" disabled={!previous} onClick={() => previous && jumpTo(previous.time)}>
              Previous prediction
            </button>
            <button type="button" disabled={!next} onClick={() => next && jumpTo(next.time)}>
              Next prediction
            </button>
            <button type="button" disabled={anchor === cutoff} onClick={() => setAnchor(cutoff)}>
              Centre view on cutoff
            </button>
          </div>
          <label className="field">
            <span>Visible range</span>
            <select value={String(before)} onChange={(event) => setRange(Number(event.target.value))}>
              {[longestWindow / 4, longestWindow / 2, longestWindow, longestWindow * 2].map((hours) => (
                <option key={hours} value={String(hours)}>
                  {formatWindow(hours * 3600)} before, {formatWindow((hours / 4) * 3600)} after
                </option>
              ))}
            </select>
          </label>
        </div>

        {timeline.error && <ErrorNotice error={timeline.error} what="eligibility at this cutoff" retry={timeline.retry} />}
        {data === undefined && !timeline.error && <Loading what={`eligibility at ${formatUtc(cutoff)}`} />}
        {data && (
          <figure className="figure">
            <TimelineChart
              versions={data.versions}
              typeOrder={typeOrder}
              featuresByType={featuresByType}
              view={view}
              cutoff={cutoff}
              appliedCutoff={applied}
              stale={stale}
              pipelineClockShown={pipelineClockShown}
              predictions={predictions}
              encounters={encounters}
              outcomes={outcomes}
              selected={selectedEvent}
              onSelect={setSelectedEvent}
              onCutoff={moveCutoff}
              onJump={jumpTo}
            />
            {/* The readout sits below the chart, so text that changes length never moves the chart under the pointer. */}
            <p className="readout">
              <span role="status" aria-live="polite">
                Cutoff {formatUtc(applied)}, {policyLabel(data.policy)} ({data.strictness};{" "}
                {data.faithful_replay ? "faithful replay" : "not a faithful replay"}). Computed by the {data.computed_by}:{" "}
                {statusSummary(data)}.
              </span>
              {stale && <span className="muted"> Updating for cutoff {formatUtc(cutoff)}…</span>}
            </p>
            <p className="muted">
              {stored ? (
                <>
                  The cutoff is the time of prediction <code>{stored.prediction_id}</code> (label: {stored.label_status}
                  {stored.split ? `, ${stored.split} set` : ""}). Stored risk from the as-known model:{" "}
                  {formatNumber(stored.p_as_known_model__pipeline_as_known, 4)} on as-known features,{" "}
                  {formatNumber(stored.p_as_known_model__naive_event_time, 4)} on naive features.
                </>
              ) : (
                "The cutoff is between this patient's prediction times, so there is no stored prediction for it: eligibility and feature values are computed on request for this instant."
              )}
            </p>
            <Legend timeline={data} applied={applied} />
            <figcaption>
              {sampleNote} Times are UTC. Each row is one version of one observation: a tick at its event time, a line for
              the delay, and a marker where the version became known
              {pipelineClockShown ? " to the pipeline (this policy has no clock of its own and ignores it)" : " on this policy's clock"}.
              Shaded bands are the lookback windows of the features, ending at the cutoff. Drag the cutoff, or focus it and
              press the arrow keys (15 minutes; with Shift, 1 minute; Page Up and Page Down, 1 hour).
              {featuresByType === null && " Lookback windows are not drawn because the run manifest did not load."}
            </figcaption>
          </figure>
        )}
      </div>

      {data && (
        <div className="workspace-wide">
          <Panel title="Feature values at the cutoff">
            <FeatureValues timeline={data} policy={policy} reference={manifest?.reference_policy ?? REFERENCE_POLICY} definitions={definitions} note={sampleNote} />
          </Panel>

          <Panel title="Versions and why each was or was not used">
            <VersionDetails
              timeline={data}
              view={view}
              applied={applied}
              typeOrder={typeOrder}
              pipelineClockShown={pipelineClockShown}
              selected={selectedEvent}
              onSelect={setSelectedEvent}
              note={sampleNote}
            />
          </Panel>
        </div>
      )}
    </>
  );
}

function orderedStatuses(counts: Record<string, number>): string[] {
  const known = STATUS_ORDER.filter((status) => counts[status] !== undefined);
  const other = Object.keys(counts).filter((status) => !STATUS_ORDER.includes(status)).sort();
  return [...known, ...other];
}

function statusSummary(timeline: Timeline): string {
  return orderedStatuses(timeline.status_counts)
    .map((status) => `${formatCount(timeline.status_counts[status])} ${statusMeta(status).label.toLowerCase()}`)
    .join(", ");
}

/* ------------------------------------------------------------------ legend */

function Legend({ timeline, applied }: { timeline: Timeline; applied: number | null }) {
  const late = timeline.versions.filter((version) => usedBeforePipelineHadIt(version, applied)).length;
  return (
    <ul className="legend" aria-label="Legend: status of each version at the cutoff, with counts over all versions of this patient">
      {orderedStatuses(timeline.status_counts).map((status) => (
        <li key={status}>
          <StatusIcon status={status} />
          {statusMeta(status).label} <span className="count">{formatCount(timeline.status_counts[status])}</span>
        </li>
      ))}
      <li>
        <svg width={18} height={14} viewBox="0 0 18 14" aria-hidden="true" focusable="false">
          <circle className="late-ring" cx={9} cy={7} r={6} />
          <circle className="glyph filled tone-used" cx={9} cy={7} r={3.5} />
        </svg>
        Used, but the pipeline received it after the cutoff <span className="count">{formatCount(late)}</span>
      </li>
      <li>
        <svg width={26} height={14} viewBox="0 0 26 14" aria-hidden="true" focusable="false">
          <line className="event-tick" x1={3} y1={1} x2={3} y2={13} />
          <line className="delay" x1={3} y1={7} x2={20} y2={7} />
        </svg>
        Event time, then delay until known
      </li>
      <li>
        <svg className="prediction-icon flagged" width={10} height={9} viewBox="0 0 10 9" aria-hidden="true" focusable="false">
          <path d="M1,1 H9 L5,8 Z" />
        </svg>
        Prediction time with audit findings (hollow: without)
      </li>
      <li>
        <svg width={22} height={14} viewBox="0 0 22 14" aria-hidden="true" focusable="false">
          <rect className="encounter" x={1} y={5} width={20} height={5} rx={2} />
        </svg>
        Encounter
      </li>
      <li>
        <svg width={14} height={14} viewBox="0 0 14 14" aria-hidden="true" focusable="false">
          <path className="outcome" d="M7,1 L13,7 L7,13 L1,7 Z" />
        </svg>
        Simulated outcome event (never a feature input)
      </li>
      <li>
        <svg width={22} height={14} viewBox="0 0 22 14" aria-hidden="true" focusable="false">
          <rect className="lane" x={0} y={1} width={22} height={12} />
          <rect className="window" x={0} y={1} width={22} height={12} />
          <line className="window-edge" x1={0.5} y1={0} x2={0.5} y2={14} />
        </svg>
        Lookback window of a feature
      </li>
    </ul>
  );
}

/* ---------------------------------------------------------------- features */

function FeatureValues({
  timeline,
  policy,
  reference,
  definitions,
  note,
}: {
  timeline: Timeline;
  policy: string;
  reference: string;
  definitions: FeatureDefinition[] | undefined;
  note: string;
}) {
  const referenceValues = timeline.features[reference];
  const selectedValues = timeline.features[policy];
  const same = policy === reference;
  const missing = (value: number | null | undefined) => value === null || value === undefined;
  const show = (value: number | null | undefined) =>
    missing(value) ? <span className="not-estimable">missing: nothing in the window</span> : formatValue(value, 3);

  const columns: Column<string>[] = [
    { key: "feature", header: "Feature", rowHeader: true, cell: (name) => <code>{name}</code> },
    {
      key: "reads",
      header: "Reads",
      cell: (name) => {
        const definition = definitions?.find((feature) => feature.name === name);
        if (!definition) return <span className="muted">definition not loaded</span>;
        const unit = definition.aggregation === "time_since_last" ? ", in hours" : "";
        return `${definition.aggregation} of ${definition.event_type} over ${formatWindow(definition.window_seconds)}${unit}`;
      },
    },
    {
      key: "reference",
      header: same ? `${policyLabel(reference)} (reference, selected)` : `${policyLabel(reference)} (reference)`,
      wrapHeader: true,
      numeric: true,
      cell: (name) => (referenceValues ? show(referenceValues[name]) : <span className="not-estimable">not returned</span>),
    },
  ];
  if (!same) {
    columns.push(
      {
        key: "selected",
        header: `${policyLabel(policy)} (selected)`,
      wrapHeader: true,
        numeric: true,
        cell: (name) => (selectedValues ? show(selectedValues[name]) : <span className="not-estimable">not returned</span>),
      },
      {
        key: "difference",
        header: "Selected against reference",
      wrapHeader: true,
        cell: (name) => {
          const a = referenceValues?.[name];
          const b = selectedValues?.[name];
          if (missing(a) && missing(b)) return "same (both missing)";
          if (missing(a) || missing(b)) return <Tag tone="warn">differs: missing under {missing(a) ? "the reference" : "the selected policy"}</Tag>;
          if (a === b) return "same";
          return <Tag tone="warn">differs by {formatNumber((b ?? 0) - (a ?? 0), 3, true)}</Tag>;
        },
      },
    );
  }
  return (
    <Table
      label="Feature values at the cutoff"
      caption={
        `Values at cutoff ${formatUtc(parseTime(timeline.cutoff))}, computed on request by the ${timeline.computed_by}. ${note}` +
        (same ? " The selected policy is the reference; choose another policy to compare." : "")
      }
      columns={columns}
      rows={timeline.feature_names}
      rowKey={(name) => name}
      empty="The API returned no feature values for this cutoff."
    />
  );
}

/* ---------------------------------------------------------------- versions */

function VersionDetails({
  timeline,
  view,
  applied,
  typeOrder,
  pipelineClockShown,
  selected,
  onSelect,
  note,
}: {
  timeline: Timeline;
  view: { start: number; end: number };
  applied: number | null;
  typeOrder: readonly string[];
  pipelineClockShown: boolean;
  selected: string | null;
  onSelect: (id: string | null) => void;
  note: string;
}) {
  const [scope, setScope] = useState<"range" | "all">("range");
  const [status, setStatus] = useState("all");
  const container = useRef<HTMLDivElement | null>(null);

  const inRange = useMemo(() => observationsInRange(timeline.versions, view), [timeline.versions, view.start, view.end]);
  const rows = useMemo(() => {
    const eventTime = new Map<string, number>();
    for (const version of timeline.versions) {
      const time = parseTime(version.event_time);
      if (time !== null && !eventTime.has(version.logical_event_id)) eventTime.set(version.logical_event_id, time);
    }
    const rank = (type: string) => {
      const index = typeOrder.indexOf(type);
      return index === -1 ? typeOrder.length : index;
    };
    return timeline.versions
      .filter((version) => (scope === "all" || inRange.has(version.logical_event_id)) && (status === "all" || version.status === status))
      .sort(
        (a, b) =>
          rank(a.event_type) - rank(b.event_type) ||
          a.event_type.localeCompare(b.event_type) ||
          (eventTime.get(a.logical_event_id) ?? 0) - (eventTime.get(b.logical_event_id) ?? 0) ||
          a.logical_event_id.localeCompare(b.logical_event_id) ||
          a.source_revision - b.source_revision,
      );
  }, [timeline.versions, scope, status, inRange, typeOrder]);

  // Bring the observation picked in the chart into view inside the table.
  useEffect(() => {
    if (selected === null) return;
    container.current?.querySelector<HTMLElement>("tr.selected")?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  const inRangeVersions = timeline.versions.filter((version) => inRange.has(version.logical_event_id)).length;
  const columns: Column<TimelineVersion>[] = [
    {
      key: "event",
      header: "Event",
      rowHeader: true,
      cell: (version) => (
        <>
          <button
            type="button"
            className="link-button"
            aria-pressed={version.logical_event_id === selected}
            title="Highlight this observation in the timeline"
            onClick={() => onSelect(version.logical_event_id === selected ? null : version.logical_event_id)}
          >
            <code>{version.logical_event_id}</code>
          </button>
          <br />
          <span className="muted">{version.event_type}</span>
        </>
      ),
    },
    {
      key: "revision",
      header: "Revision",
      cell: (version) =>
        `${version.source_revision}${version.operation === "retract" ? " (retraction)" : version.source_revision > 1 ? " (correction)" : ""}`,
    },
    {
      key: "event_time",
      header: "Event time (UTC)",
      className: "nowrap",
      cell: (version) =>
        version.event_time === null ? <span className="muted">none: a retraction has no event time</span> : formatUtc(parseTime(version.event_time), false),
    },
    {
      key: "known",
      header: pipelineClockShown ? "Known to the pipeline (UTC)" : "Known time (UTC)",
      wrapHeader: true,
      className: "nowrap",
      cell: (version) => {
        const known = knownTime(version);
        const event = parseTime(version.event_time);
        if (known === null) return <span className="not-estimable">unknown availability</span>;
        return (
          <>
            {formatUtc(known, false)}
            {event !== null && (
              <>
                <br />
                <span className="muted">{describeDelay(event, known)}</span>
              </>
            )}
            {usedBeforePipelineHadIt(version, applied) && (
              <>
                <br />
                <Tag tone="warn">after the cutoff</Tag>
              </>
            )}
          </>
        );
      },
    },
    {
      key: "value",
      header: "Value",
      numeric: true,
      cell: (version) =>
        version.value === null ? <span className="muted">none</span> : `${formatValue(version.value)}${version.unit ? ` ${version.unit}` : ""}`,
    },
    { key: "status", header: "Status", cell: (version) => <StatusLabel status={version.status} /> },
    { key: "reason", header: "Reason", className: "wrap", cell: (version) => version.reason },
    {
      key: "used_by",
      header: "Used by",
      cell: (version) =>
        version.used_by.length === 0 ? <span className="muted">no feature</span> : version.used_by.map((name) => <code key={name}>{name} </code>),
    },
  ];

  return (
    <div ref={container}>
      <div className="controls">
        <label className="field">
          <span>Rows</span>
          <select value={scope} onChange={(event) => setScope(event.target.value === "all" ? "all" : "range")}>
            <option value="range">Versions in the visible range ({formatCount(inRangeVersions)})</option>
            <option value="all">All versions of this patient ({formatCount(timeline.versions.length)})</option>
          </select>
        </label>
        <label className="field">
          <span>Status</span>
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="all">Every status</option>
            {orderedStatuses(timeline.status_counts).map((name) => (
              <option key={name} value={name}>
                {statusMeta(name).label} ({formatCount(timeline.status_counts[name])} in all)
              </option>
            ))}
          </select>
        </label>
      </div>
      <Table
        label="Versions and their eligibility at the cutoff"
        dense
        maxHeight={520}
        caption={
          `${plural(rows.length, "version")} listed, at cutoff ${formatUtc(applied)} under ${policyLabel(timeline.policy)}. ${note}` +
          (pipelineClockShown ? " This policy has no availability clock: the known time shown is the pipeline's, which the policy ignores." : "")
        }
        columns={columns}
        rows={rows}
        rowKey={versionKey}
        rowClass={(version) => (version.logical_event_id === selected ? "selected" : undefined)}
        empty="No version matches these filters. Widen the visible range, or list all versions of this patient."
      />
    </div>
  );
}
