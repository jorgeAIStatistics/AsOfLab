/** Page frame: header with the run selector and the four views, switched by the URL hash. */

import { useEffect, useRef } from "react";
import { endpoints, type RunListing } from "./api";
import { CommandBlock, Empty, ErrorNotice, Loading } from "./components/ui";
import { formatCount, plural } from "./format";
import { VIEWS, formatHash, useLoad, useRoute, type ViewId } from "./hooks";
import { ComparisonView } from "./views/Comparison";
import { EngineeringView } from "./views/Engineering";
import { OverviewView } from "./views/Overview";
import { PatientTimelineView } from "./views/PatientTimeline";

const TITLES: Record<ViewId, string> = {
  overview: "Run overview",
  timeline: "Patient timeline",
  comparison: "Comparison",
  engineering: "Engineering",
};

function runLabel(run: RunListing): string {
  const parts = [run.run_id];
  if (run.patients !== null) parts.push(`${formatCount(run.patients)} patients`);
  if (!run.has_patient_data) parts.push("summaries only");
  return parts.join(" · ");
}

export function App() {
  const { route, navigate } = useRoute();
  const runs = useLoad(endpoints.runs());
  const health = useLoad(endpoints.health());
  const engineering = useLoad(endpoints.engineering(), { cache: true });
  const main = useRef<HTMLElement | null>(null);

  const list = runs.data ?? [];
  const requested = route.params["run"];
  const run = requested === undefined ? list[0] : list.find((candidate) => candidate.run_id === requested);
  const defaultRun = requested === undefined ? list[0]?.run_id : undefined;

  // Put the default run in the URL, so a copied link names the run it shows.
  useEffect(() => {
    if (defaultRun !== undefined) navigate(route.view, { ...route.params, run: defaultRun }, "replace");
  }, [defaultRun, route.view, route.params, navigate]);

  // On a change of view, name it in the title and move focus to its heading.
  const shown = useRef(route.view);
  useEffect(() => {
    document.title = `${TITLES[route.view]} · AsOfLab`;
    if (shown.current === route.view) return;
    shown.current = route.view;
    main.current?.querySelector<HTMLElement>("h1")?.focus();
  }, [route.view]);

  const demoCommand = engineering.data?.commands?.["demo"] ?? "asoflab demo";

  let content;
  if (route.view === "engineering") {
    content = <EngineeringView run={run ?? null} />;
  } else if (runs.error) {
    content = (
      <>
        <h1 tabIndex={-1}>{TITLES[route.view]}</h1>
        <ErrorNotice error={runs.error} what="the list of runs" retry={runs.retry} />
      </>
    );
  } else if (runs.data === undefined) {
    content = (
      <>
        <h1 tabIndex={-1}>{TITLES[route.view]}</h1>
        <Loading what="the list of runs" />
      </>
    );
  } else if (list.length === 0) {
    content = (
      <>
        <h1 tabIndex={-1}>{TITLES[route.view]}</h1>
        <Empty>
          No runs were found in the directory this server reads. Generate the small demonstration run, then reload
          this page:
        </Empty>
        <CommandBlock command={demoCommand} label="the demonstration run" />
      </>
    );
  } else if (!run) {
    content = (
      <>
        <h1 tabIndex={-1}>{TITLES[route.view]}</h1>
        <div className="notice" role="alert">
          <p>
            <strong>Run not found.</strong> The link names run <code>{requested}</code>, which is not among the{" "}
            {plural(list.length, "run")} this server reads. Choose a run in the header.
          </p>
        </div>
      </>
    );
  } else if (route.view === "overview") {
    content = <OverviewView run={run} />;
  } else if (route.view === "timeline") {
    content = <PatientTimelineView run={run} route={route} navigate={navigate} />;
  } else {
    content = <ComparisonView run={run} route={route} navigate={navigate} />;
  }

  return (
    <>
      <header className="site-header">
        <div className="bar">
          <div className="brand">
            AsOfLab
            <span className="badge" title="Every number in this tool comes from a simulation.">
              Synthetic data
            </span>
          </div>
          <nav className="site-nav" aria-label="Views">
            <ul>
              {VIEWS.map((view) => (
                <li key={view}>
                  <a href={formatHash(view, { run: run?.run_id })} aria-current={route.view === view ? "page" : undefined}>
                    {TITLES[view]}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
          <div className="run-picker">
            <label htmlFor="run-select">Run</label>
            <select
              id="run-select"
              value={run?.run_id ?? ""}
              disabled={list.length === 0}
              onChange={(event) => navigate(route.view, { run: event.target.value })}
            >
              {!run && <option value="">{runs.data === undefined ? (runs.error ? "Runs unavailable" : "Loading runs…") : list.length === 0 ? "No runs" : "Choose a run"}</option>}
              {list.map((candidate) => (
                <option key={candidate.run_id} value={candidate.run_id}>
                  {runLabel(candidate)}
                </option>
              ))}
            </select>
          </div>
        </div>
      </header>
      <main ref={main}>{content}</main>
      <footer className="site-footer">
        <span role="status" aria-live="polite">
          {health.error
            ? "Local API: not reachable."
            : health.data
              ? `Local API: ${health.data.status}. AsOfLab ${health.data.asoflab_version}, temporal contract ${health.data.contract_version}.`
              : "Local API: checking…"}
        </span>{" "}
        All data shown are simulated. This is a research tool, not a clinical one.
      </footer>
    </>
  );
}
