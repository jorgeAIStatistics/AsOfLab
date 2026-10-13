/** View 1. What this run is, what the audit found, and the computed metrics of experiment A. */

import type { Audit, Manifest, RunListing, Statistics } from "../api";
import { endpoints } from "../api";
import { ConditionsTable, DifferenceForests, DifferencesTable, intervalNote } from "../components/metrics";
import { Async, Empty, Facts, Panel, Table, Tag, YesNo, sampleText, type Column } from "../components/ui";
import { formatCount, formatIso, formatLevel, formatPercent, humanise, plural, shortHash } from "../format";
import { formatHash, useLoad } from "../hooks";
import {
  DECOMPOSITION_KEYS,
  HEADLINE_METRICS,
  REFERENCE_POLICY,
  policyLabel,
  policySummary,
  severityClass,
} from "../labels";

export function OverviewView({ run }: { run: RunListing }) {
  const manifest = useLoad(endpoints.manifest(run.run_id), { cache: true });
  const statistics = useLoad(endpoints.statistics(run.run_id), { cache: true });
  const audit = useLoad(endpoints.audit(run.run_id), { cache: true });

  return (
    <>
      <span className="badge large">Synthetic data</span>
      <h1 tabIndex={-1}>Run overview</h1>
      <p className="lede">
        Run <code>{run.run_id}</code>. Every number below was computed from a simulated cohort and is read from the
        files this run wrote.
      </p>
      <p className="notice">
        Everything here comes from a computer simulation. It is not a validated physiological model, and no number is
        evidence about a real patient, hospital, or model.
      </p>

      <Panel title="Temporal policies">
        <Async load={manifest} what="the run manifest">
          {(document) => <Policies manifest={document} />}
        </Async>
      </Panel>

      <Panel title="Dataset and run provenance">
        <Async load={manifest} what="the run manifest">
          {(document) => <Provenance manifest={document} statistics={statistics.data} />}
        </Async>
      </Panel>

      <Panel title="Audit: where the candidate policy departs from what was known">
        <Async load={audit} what="the audit results">
          {(document) => <AuditSummary audit={document} />}
        </Async>
      </Panel>

      <Panel title="Experiment A: one frozen model, features built under each policy">
        <Async load={statistics} what="the computed statistics">
          {(document) => <ExperimentA statistics={document} runId={run.run_id} />}
        </Async>
      </Panel>
    </>
  );
}

/* ---------------------------------------------------------------- policies */

function Policies({ manifest }: { manifest: Manifest }) {
  const policies = manifest.time_policies ?? [];
  const reference = manifest.reference_policy ?? REFERENCE_POLICY;
  type Row = (typeof policies)[number];
  const columns: Column<Row>[] = [
    {
      key: "policy",
      header: "Policy",
      rowHeader: true,
      cell: (row) => (
        <>
          {policyLabel(row.policy)} {row.policy === reference && <Tag>reference</Tag>}
          <br />
          <code>{row.policy}</code>
        </>
      ),
    },
    { key: "reads", header: "What it treats as known", className: "wrap", cell: (row) => policySummary(row.policy) },
    { key: "kind", header: "Kind", cell: (row) => row.strictness },
    {
      key: "faithful",
      header: "Faithful replay",
      cell: (row) => (row.faithful_replay ? "yes" : <Tag tone="warn">no: not a historical replay</Tag>),
    },
    {
      key: "hash",
      header: "Feature table hash", wrapHeader: true,
      cell: (row) => <code title={row.feature_table_hash}>{shortHash(row.feature_table_hash, 16)}</code>,
    },
  ];
  return (
    <>
      <p>
        A time policy decides which versions of an observation count as known at a prediction cutoff. Differences are
        always taken against the reference policy.
      </p>
      <Table
        label="Temporal policies of this run"
        caption={`${plural(policies.length, "policy", "policies")} recorded in the run manifest. Simulated data.`}
        columns={columns}
        rows={policies}
        rowKey={(row) => row.policy}
        rowClass={(row) => (row.policy === reference ? "reference" : undefined)}
        empty="The manifest of this run lists no time policies."
      />
    </>
  );
}

/* -------------------------------------------------------------- provenance */

function Provenance({ manifest, statistics }: { manifest: Manifest; statistics: Statistics | undefined }) {
  const config = manifest.generator?.config;
  const scenario = config?.scenario ?? statistics?.scenario;
  const environment = manifest.environment;
  const commit = environment?.code_commit;
  const hashes = Object.entries(manifest.data_hashes ?? {});
  const cohort = Object.entries(statistics?.cohort ?? {});
  const coverage = statistics?.label_coverage;

  return (
    <>
      <Facts
        items={[
          ["Run", <code key="run">{manifest.run_id}</code>],
          [
            "Scenario",
            <>
              <code>{manifest.scenario ?? scenario?.name ?? "not recorded"}</code>
              {scenario?.description ? `: ${scenario.description}` : ""}
            </>,
          ],
          ["Seed", manifest.seed ?? "not recorded"],
          ["Created", formatIso(manifest.created_at)],
          ["Temporal contract version", manifest.identity?.contract_version ?? "not recorded"],
          [
            "Generator",
            <>
              <code>{manifest.generator?.version ?? "not recorded"}</code>
              {config?.n_patients !== undefined && `, ${formatCount(config.n_patients)} simulated patients`}
              {config?.study_days !== undefined && ` over ${config.study_days} days`}
            </>,
          ],
          [
            "Code commit",
            commit ? (
              <>
                <code title={commit}>{shortHash(commit, 12)}</code>{" "}
                {environment?.code_dirty === true ? (
                  <Tag tone="warn">working tree had uncommitted changes</Tag>
                ) : environment?.code_dirty === false ? (
                  "clean working tree"
                ) : (
                  "tree state not recorded"
                )}
              </>
            ) : (
              "not recorded"
            ),
          ],
          [
            "Environment",
            environment
              ? `AsOfLab ${environment.asoflab_version ?? "?"}, Python ${environment.python ?? "?"} on ${environment.platform ?? "?"}`
              : "not recorded",
          ],
        ]}
      />

      <h3>Dataset size</h3>
      {cohort.length === 0 ? (
        <Empty>Cohort counts are in statistics.json, which has not loaded for this run.</Empty>
      ) : (
        <>
          <Facts items={cohort.map(([name, count]) => [humanise(name), formatCount(count)] as const)} />
          {coverage && coverage.labelled !== undefined && coverage.predictions !== undefined && (
            <p className="muted">
              {formatCount(coverage.labelled)} of {formatCount(coverage.predictions)} predictions have a known label (
              {formatPercent(coverage.coverage)}). Unknown labels are excluded from metrics and are never counted as
              negatives.
            </p>
          )}
        </>
      )}

      <h3>Content hashes of the simulated tables</h3>
      <Table
        label="Content hashes of the simulated tables"
        caption="SHA-256 of each table's content, first 16 characters. Hover a hash for the full value."
        columns={[
          { key: "table", header: "Table", rowHeader: true, cell: ([name]) => name },
          { key: "hash", header: "Content hash", cell: ([, hash]) => <code title={hash}>{shortHash(hash, 16)}</code> },
        ]}
        rows={hashes}
        rowKey={([name]) => name}
        empty="The manifest of this run records no data hashes."
      />
      {manifest.note && <p className="muted">{manifest.note}</p>}
    </>
  );
}

/* ------------------------------------------------------------------- audit */

function AuditSummary({ audit }: { audit: Audit }) {
  const differential = audit.differential;
  const changed = Object.entries(audit.feature_differences_from_reference ?? {});
  const validation = audit.validation;
  if (!differential) return <Empty>This run's audit file has no differential audit section.</Empty>;

  const types = Object.entries(differential.by_type ?? {});
  const sample = `${formatCount(differential.predictions)} predictions`;
  const share = differential.predictions > 0 ? differential.predictions_with_findings / differential.predictions : null;
  const candidate = differential.candidate_policy ?? "candidate";
  const reference = differential.reference_policy ?? REFERENCE_POLICY;
  const naiveChanged = audit.feature_differences_from_reference?.[candidate];

  const eventTypes = Object.entries(differential.by_event_type ?? {});
  const typeColumns = types.filter(([name]) => eventTypes.some(([, counts]) => (counts[name] ?? 0) > 0)).map(([name]) => name);

  return (
    <>
      <p>
        Candidate <code>{candidate}</code> ({policyLabel(candidate)}) compared with reference <code>{reference}</code> (
        {policyLabel(reference)}). {differential.scope}
      </p>
      <div className="tiles">
        <div className="tile">
          <div className="label">Predictions with at least one timing or revision finding</div>
          <div className="value">{share === null ? "not available" : formatPercent(share)}</div>
          <div className="context">
            {formatCount(differential.predictions_with_findings)} of {sample}
          </div>
        </div>
        <div className="tile">
          <div className="label">Findings in total</div>
          <div className="value">{formatCount(differential.findings_total)}</div>
          <div className="context">one finding is one observation at one prediction</div>
        </div>
        {naiveChanged && (
          <div className="tile">
            <div className="label">Predictions with at least one changed feature value</div>
            <div className="value">{formatPercent(naiveChanged.share_of_predictions)}</div>
            <div className="context">
              {formatCount(naiveChanged.predictions_with_any_feature_changed)} of {sample}
            </div>
          </div>
        )}
      </div>

      <h3>Findings by type</h3>
      {differential.findings_total === 0 ? (
        <Empty>
          No findings: at every one of the {sample}, the candidate policy used exactly what the reference policy used.
        </Empty>
      ) : null}
      <Table
        label="Audit findings by type"
        caption={`Simulated data. ${formatCount(differential.findings_total)} findings in ${formatCount(differential.predictions_with_findings)} of ${sample}.`}
        columns={[
          {
            key: "severity",
            header: "Severity",
            cell: ([, entry]) => <span className={severityClass(entry.severity)}>{entry.severity}</span>,
          },
          { key: "type", header: "Finding", rowHeader: true, cell: ([name]) => <code>{name}</code> },
          { key: "count", header: "Count", numeric: true, cell: ([, entry]) => formatCount(entry.count) },
          { key: "meaning", header: "Meaning", className: "wrap", cell: ([, entry]) => entry.meaning },
          { key: "fix", header: "What to change", className: "wrap", cell: ([, entry]) => entry.remediation ?? "" },
        ]}
        rows={types}
        rowKey={([name]) => name}
        empty="The audit file lists no finding types."
      />

      {eventTypes.length > 0 && typeColumns.length > 0 && (
        <Table
          label="Audit findings by event type"
          caption={`Findings by event type; finding types with no findings are left out. Simulated data, ${sample}.`}
          columns={[
            { key: "type", header: "Event type", rowHeader: true, cell: ([name]) => <code>{name}</code> },
            ...typeColumns.map(
              (name): Column<[string, Record<string, number>]> => ({
                key: name,
                header: humanise(name),
                numeric: true,
                cell: ([, counts]) => formatCount(counts[name] ?? 0),
              }),
            ),
          ]}
          rows={eventTypes}
          rowKey={([name]) => name}
        />
      )}

      <h3>Predictions whose feature values differ from the reference</h3>
      <Table
        label="Predictions with changed features, by policy"
        caption={`Share of the ${sample} with at least one feature value different from the reference. Simulated data.`}
        columns={[
          { key: "policy", header: "Policy compared with the reference", wrapHeader: true, rowHeader: true, cell: ([slug]) => policyLabel(slug) },
          {
            key: "count",
            header: "Predictions with a changed feature", wrapHeader: true,
            numeric: true,
            cell: ([, entry]) => formatCount(entry.predictions_with_any_feature_changed),
          },
          { key: "share", header: "Share", numeric: true, cell: ([, entry]) => formatPercent(entry.share_of_predictions) },
        ]}
        rows={changed}
        rowKey={([slug]) => slug}
        empty="The audit file records no feature differences."
      />

      <h3>Contract validation</h3>
      {!validation ? (
        <Empty>The audit file has no contract validation section.</Empty>
      ) : (
        <>
          <p>
            Dataset passes the temporal contract: <YesNo value={validation.ok} concern="no" />.
            {Object.entries(validation.counts ?? {}).map(([name, count]) => ` ${humanise(name)}: ${formatCount(count)}.`)}
          </p>
          <Table
            label="Contract validation warnings"
            columns={[
              { key: "code", header: "Code", rowHeader: true, cell: (finding) => <code>{finding.code}</code> },
              { key: "count", header: "Count", numeric: true, cell: (finding) => formatCount(finding.count) },
              { key: "message", header: "Meaning", className: "wrap", cell: (finding) => finding.message ?? "" },
            ]}
            rows={validation.findings ?? []}
            rowKey={(finding) => finding.code}
            empty="No contract errors or warnings for this dataset."
          />
        </>
      )}
    </>
  );
}

/* ------------------------------------------------------------ experiment A */

function ExperimentA({ statistics, runId }: { statistics: Statistics; runId: string }) {
  const a = statistics.experiment_a;
  if (!a) return <Empty>This run's statistics file has no experiment A section.</Empty>;
  const block = a.prediction_weighted;
  const differences = block?.differences ?? {};
  const keys = Object.keys(differences).filter((key) => !DECOMPOSITION_KEYS.includes(key));
  const level = formatLevel(statistics.bootstrap?.level);
  const sample = sampleText(a.sample);
  const reference = a.model_trained_under ?? REFERENCE_POLICY;
  const metrics = [...HEADLINE_METRICS, "calibration_intercept", "calibration_slope"].filter((metric) =>
    Object.values(block?.conditions ?? {}).some((set) => set[metric] !== undefined),
  );

  return (
    <>
      {a.question && <p>{a.question}</p>}
      {a.interpretation && (
        <p>
          <strong>How to read it.</strong> {a.interpretation}
        </p>
      )}
      {(a.instability_flags?.length ?? 0) > 0 && (
        <p className="notice">Caution, the results file flags instability: {a.instability_flags?.join("; ")}.</p>
      )}
      <ConditionsTable
        label="Experiment A: metric by feature-time policy"
        firstColumn="Features built under"
        conditions={block?.conditions ?? {}}
        metrics={metrics}
        reference={reference}
        caption={`${sample} Evaluated on ${a.evaluated_on ?? "the test set"}. ${intervalNote(statistics.bootstrap)}`}
      />

      <h3>Paired differences against the reference</h3>
      <DifferenceForests
        keys={keys}
        sources={[differences]}
        titleSuffix="policy minus as-known"
        level={level}
        caption={sample}
      />
      <DifferencesTable
        label="Experiment A: paired differences against the reference"
        rows={keys.map((key) => ({ key, difference: differences[key] }))}
        caption={`${sample} Prediction-weighted. Each difference is formed within a bootstrap resample; ${level} intervals.`}
      />
      <p className="muted">
        The patient-weighted view, the split into a revision effect and an availability effect, and experiment B are
        in the <a href={formatHash("comparison", { run: runId })}>Comparison</a> view.
      </p>
    </>
  );
}
