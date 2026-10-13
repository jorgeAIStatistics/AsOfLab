/** Shared building blocks: async states, panels, tables, estimates, copyable commands. */

import { useEffect, useId, useLayoutEffect, useRef, useState, type HTMLAttributes, type ReactNode } from "react";
import { endpoints, type ApiError, type Sample } from "../api";
import { describeEstimate, formatCount, type IntervalLike } from "../format";
import { useLoad, type Load } from "../hooks";

/* ------------------------------------------------------------ async states */

export function Loading({ what }: { what: string }) {
  return (
    <p className="status" role="status" aria-live="polite">
      Loading {what}…
    </p>
  );
}

export function ErrorNotice({ error, what, retry }: { error: ApiError; what: string; retry?: () => void }) {
  return (
    <div className="notice error" role="alert">
      <p>
        <strong>Could not load {what}.</strong> {error.message}
      </p>
      <details>
        <summary>Technical details</summary>
        <pre>{error.technical}</pre>
      </details>
      {retry && (
        <button type="button" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

/** Render `children` once the document is there; otherwise the pending or failed state. */
export function Async<T>({
  load,
  what,
  children,
}: {
  load: Load<T>;
  what: string;
  children: (data: T) => ReactNode;
}) {
  if (load.error) return <ErrorNotice error={load.error} what={what} retry={load.retry} />;
  if (load.data === undefined) return <Loading what={what} />;
  return <>{children(load.data)}</>;
}

/** A results file that has not been produced on this machine yet, with the command that produces it. */
export function NotGenerated({ what, command, file }: { what: string; command: string | undefined; file: string }) {
  return (
    <div className="notice quiet">
      <p>
        <strong>Not generated yet.</strong> {what} are read from <code>{file}</code>, and nothing has been written there
        on this machine. {command ? "Run:" : "The API did not name a command that writes it."}
      </p>
      {command && <CommandBlock command={command} />}
    </div>
  );
}

/** Shown when a run has summaries but no patient-level tables, or a patient endpoint answers 404. */
export function NoPatientData({ what, error }: { what: string; error?: ApiError | undefined }) {
  const engineering = useLoad(endpoints.engineering(), { cache: true });
  const command = engineering.data?.commands?.["demo"] ?? "asoflab demo";
  return (
    <div className="notice">
      <p>
        <strong>Patient-level data for this run are not on this machine.</strong> The run's summaries (overview,
        metric comparisons, engine agreement) are still available. {what} needs the detailed tables, which are
        generated locally and not stored with the repository. Generate them with:
      </p>
      <CommandBlock command={command} />
      {error && (
        <>
          <p>{error.message}</p>
          <details>
            <summary>Technical details</summary>
            <pre>{error.technical}</pre>
          </details>
        </>
      )}
    </div>
  );
}

/* ----------------------------------------------------------------- layout */

export function Panel({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  const id = useId();
  return (
    <section className="panel" aria-labelledby={id}>
      <div className="panel-head">
        <h2 id={id}>{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

/** A horizontally scrollable region. It becomes a keyboard stop only while its content overflows. */
export function ScrollRegion({
  label,
  children,
  maxHeight,
}: {
  label: string;
  children: ReactNode;
  maxHeight?: number | undefined;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [overflowing, setOverflowing] = useState(false);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const check = () =>
      setOverflowing(element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1);
    check();
    const observer = new ResizeObserver(check);
    observer.observe(element);
    if (element.firstElementChild) observer.observe(element.firstElementChild);
    return () => observer.disconnect();
  }, []);
  return (
    <div
      ref={ref}
      className="scroll"
      style={maxHeight ? { maxHeight, overflowY: "auto" } : undefined}
      {...(overflowing ? { tabIndex: 0, role: "region", "aria-label": `${label} (scrollable)` } : {})}
    >
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ tables */

export interface Column<Row> {
  key: string;
  header: ReactNode;
  cell: (row: Row, index: number) => ReactNode;
  /** Right-aligned with tabular figures. */
  numeric?: boolean;
  /** Rendered as <th scope="row">. */
  rowHeader?: boolean;
  className?: string;
  /** An extra class for single cells, for example to mark a changed value. */
  cellClass?: (row: Row) => string | undefined;
  /** Let a long header break over lines instead of widening its column. */
  wrapHeader?: boolean;
}

interface TableProps<Row> {
  /** Names the table for assistive technology and for its scroll region. */
  label: string;
  columns: readonly Column<Row>[];
  rows: readonly Row[];
  rowKey: (row: Row, index: number) => string;
  caption?: ReactNode;
  rowClass?: (row: Row, index: number) => string | undefined;
  rowProps?: (row: Row, index: number) => HTMLAttributes<HTMLTableRowElement>;
  empty?: ReactNode;
  maxHeight?: number;
  dense?: boolean;
}

export function Table<Row>({
  label,
  columns,
  rows,
  rowKey,
  caption,
  rowClass,
  rowProps,
  empty,
  maxHeight,
  dense,
}: TableProps<Row>) {
  const captionId = useId();
  if (rows.length === 0 && empty !== undefined) return <Empty>{empty}</Empty>;
  const classes = (...names: (string | false | undefined)[]) => names.filter(Boolean).join(" ") || undefined;
  const cellClass = (column: Column<Row>, row: Row) =>
    classes(column.numeric && "num", column.className, column.cellClass?.(row));
  return (
    <>
      {/* The caption is a paragraph above the scroll box, so it wraps to the page and is never scrolled out of view. */}
      {caption && (
        <p id={captionId} className="table-caption">
          {caption}
        </p>
      )}
      <ScrollRegion label={label} maxHeight={maxHeight}>
        <table className={dense ? "dense" : undefined} aria-label={label} aria-describedby={caption ? captionId : undefined}>
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={classes(column.numeric && "num", column.className, column.wrapHeader && "head-wrap")}
                >
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={rowKey(row, index)} className={rowClass?.(row, index)} {...rowProps?.(row, index)}>
                {columns.map((column) =>
                  column.rowHeader ? (
                    <th key={column.key} scope="row" className={cellClass(column, row)}>
                      {column.cell(row, index)}
                    </th>
                  ) : (
                    <td key={column.key} className={cellClass(column, row)}>
                      {column.cell(row, index)}
                    </td>
                  ),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </ScrollRegion>
    </>
  );
}

/** Term and description pairs for provenance-style facts. */
export function Facts({ items }: { items: readonly (readonly [string, ReactNode])[] }) {
  return (
    <dl className="facts">
      {items.map(([term, description]) => (
        <div key={term}>
          <dt>{term}</dt>
          <dd>{description}</dd>
        </div>
      ))}
    </dl>
  );
}

/* --------------------------------------------------------------- estimates */

/** An estimate with its interval, or the reason there is none. Never a silent zero. */
export function Est({
  value,
  digits = 3,
  signed = false,
}: {
  value: IntervalLike | null | undefined;
  digits?: number;
  signed?: boolean;
}) {
  const estimate = describeEstimate(value, digits, signed);
  if (estimate.kind === "interval") return <>{estimate.text}</>;
  return <span className="not-estimable">{estimate.text}</span>;
}

export function sampleText(sample: Sample | undefined): string {
  if (!sample) return "Simulated data. Sample size not recorded in the results file.";
  return (
    `Simulated data. ${formatCount(sample.predictions)} predictions from ${formatCount(sample.patients)} patients, ` +
    `${formatCount(sample.events)} with the event.`
  );
}

export function Tag({ tone, children }: { tone?: "warn" | "high" | "ok" | undefined; children: ReactNode }) {
  return <span className={tone ? `tag ${tone}` : "tag"}>{children}</span>;
}

/** A yes/no fact as words. `concern` marks the answer that deserves attention. */
export function YesNo({ value, concern }: { value: boolean | null | undefined; concern?: "yes" | "no" }) {
  if (value === null || value === undefined) return <span className="not-estimable">not recorded</span>;
  const answer = value ? "yes" : "no";
  return concern === answer ? <Tag tone="warn">{answer}</Tag> : <>{answer}</>;
}

/* ---------------------------------------------------------------- commands */

/** A command in a code block with a copy button. */
export function CommandBlock({ command, label }: { command: string; label?: string }) {
  const [state, setState] = useState<"idle" | "copied" | "selected">("idle");
  const code = useRef<HTMLElement | null>(null);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const copy = async () => {
    let next: "copied" | "selected" = "copied";
    try {
      await navigator.clipboard.writeText(command);
    } catch {
      // No clipboard permission: select the text so one keystroke copies it.
      const selection = window.getSelection();
      if (selection && code.current) {
        const range = document.createRange();
        range.selectNodeContents(code.current);
        selection.removeAllRanges();
        selection.addRange(range);
      }
      next = "selected";
    }
    setState(next);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState("idle"), 3000);
  };

  return (
    <div className="command">
      <pre>
        <code ref={code}>{command}</code>
      </pre>
      <button type="button" onClick={copy} aria-label={`Copy command${label ? ` for ${label}` : ""}`}>
        Copy
      </button>
      <span className="command-status" role="status" aria-live="polite">
        {state === "copied" ? "Copied" : state === "selected" ? "Selected: press Ctrl or Cmd + C" : ""}
      </span>
    </div>
  );
}
