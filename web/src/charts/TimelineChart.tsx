import { useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { FeatureDefinition, TimelineVersion } from "../api";
import { DAY, HOUR, MINUTE, formatClock, formatDay, formatUtc, formatValue, formatWindow, plural } from "../format";
import { useElementWidth } from "../hooks";
import { statusMeta } from "../labels";
import { clamp, snapTime, timeTicks, timeToX, xToTime, type TimeRange } from "../scale";
import { StatusGlyph } from "./StatusGlyph";
import { textWidth } from "./common";
import { describeDelay, layoutTimeline, type PlacedVersion } from "./timelineLayout";

export interface PredictionMark {
  id: string;
  time: number;
  findings: number;
}

export interface EncounterSpan {
  id: string;
  start: number;
  end: number;
}

export interface OutcomeMark {
  id: string;
  type: string;
  time: number;
  ascertained: number | null;
}

interface TimelineChartProps {
  versions: readonly TimelineVersion[];
  typeOrder: readonly string[];
  /** Features that read each event type, for the lookback windows. Null when the run's manifest is unavailable. */
  featuresByType: ReadonlyMap<string, readonly FeatureDefinition[]> | null;
  view: TimeRange;
  /** Where the cutoff line is now. It moves at once; the statuses follow when the API answers. */
  cutoff: number;
  /** The cutoff the statuses were computed for. */
  appliedCutoff: number | null;
  stale: boolean;
  /** True when the policy has no availability clock, so known times are the pipeline's. */
  pipelineClockShown: boolean;
  predictions: readonly PredictionMark[];
  encounters: readonly EncounterSpan[];
  outcomes: readonly OutcomeMark[];
  selected: string | null;
  onSelect: (logicalEventId: string | null) => void;
  onCutoff: (ms: number) => void;
  onJump: (ms: number) => void;
}

const SIDE = 14;
const HANDLE = 22;
const TICK_BASELINE = 36;
const ENCOUNTER_Y = 44;
const GROUPS_TOP = 58;

function tooltip(item: PlacedVersion, pipelineClockShown: boolean): string {
  const { version } = item;
  const own = item.eventBorrowed ? null : item.eventMs;
  const lines = [
    `${version.logical_event_id}, revision ${version.source_revision} (${version.operation}), ${version.event_type}`,
    own === null ? "Event time: none (a retraction withdraws the observation)" : `Event time: ${formatUtc(own)}`,
    item.knownMs === null
      ? "Known: availability time unknown"
      : `Known${pipelineClockShown ? " to the pipeline" : ""}: ${formatUtc(item.knownMs)}` +
        (own !== null ? ` (${describeDelay(own, item.knownMs)})` : ""),
  ];
  if (version.value !== null) lines.push(`Value: ${formatValue(version.value)}${version.unit ? ` ${version.unit}` : ""}`);
  lines.push(`Status: ${statusMeta(version.status).label}. ${version.reason}`);
  if (version.used_by.length > 0) lines.push(`Used by: ${version.used_by.join(", ")}`);
  if (item.lateForPipeline) lines.push("The pipeline received this version after the cutoff.");
  return lines.join("\n");
}

/**
 * One patient's observations around a prediction cutoff.
 *
 * Each row is one version of one observation: a tick at its event time, a
 * connector for the delay, and a status marker at the time it became known.
 * The cutoff line can be dragged, or moved with the keyboard when focused.
 */
export function TimelineChart({
  versions,
  typeOrder,
  featuresByType,
  view,
  cutoff,
  appliedCutoff,
  stale,
  pipelineClockShown,
  predictions,
  encounters,
  outcomes,
  selected,
  onSelect,
  onCutoff,
  onJump,
}: TimelineChartProps) {
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const svg = useRef<SVGSVGElement | null>(null);
  const slider = useRef<SVGGElement | null>(null);
  const [dragging, setDragging] = useState(false);
  const held = useRef(false);

  const x0 = SIDE;
  const x1 = Math.max(width - SIDE, SIDE + 1);
  const toX = (ms: number) => timeToX(ms, view, x0, x1);
  const inLane = (x: number) => x >= x0 - 0.5 && x <= x1 + 0.5;

  const layout = useMemo(
    () => layoutTimeline({ versions, typeOrder, view: { start: view.start, end: view.end }, x0, x1, top: GROUPS_TOP, appliedCutoff }),
    [versions, typeOrder, view.start, view.end, x0, x1, appliedCutoff],
  );
  const height = layout.bottom + 2;
  const { ticks } = timeTicks(view, Math.max(2, Math.floor((x1 - x0) / 56)));
  const midnightInView = ticks.some((tick) => tick % DAY === 0);

  /* ---------------------------------------------------- moving the cutoff */

  const timeAt = (clientX: number) => {
    const box = svg.current?.getBoundingClientRect();
    if (!box) return cutoff;
    return clamp(snapTime(xToTime(clientX - box.left, view, x0, x1), MINUTE), view.start, view.end);
  };
  const stopDragging = () => {
    held.current = false;
    setDragging(false);
  };
  const pointer = {
    onPointerDown: (event: PointerEvent<SVGElement>) => {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      event.preventDefault();
      try {
        // Keep receiving moves when the pointer leaves the handle. Not every pointer can be captured.
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        // Without capture the drag still works while the pointer stays over the handle.
      }
      slider.current?.focus({ preventScroll: true });
      held.current = true;
      setDragging(true);
      onCutoff(timeAt(event.clientX));
    },
    onPointerMove: (event: PointerEvent<SVGElement>) => {
      if (!held.current) return;
      // A release that happened elsewhere (no capture) ends the drag at the next move.
      if (event.buttons === 0) stopDragging();
      else onCutoff(timeAt(event.clientX));
    },
    onPointerUp: stopDragging,
    onPointerCancel: stopDragging,
    onLostPointerCapture: stopDragging,
  };
  const onKeyDown = (event: KeyboardEvent<SVGGElement>) => {
    const step = event.shiftKey ? MINUTE : 15 * MINUTE;
    const moves: Record<string, number> = {
      ArrowLeft: cutoff - step,
      ArrowDown: cutoff - step,
      ArrowRight: cutoff + step,
      ArrowUp: cutoff + step,
      PageDown: cutoff - HOUR,
      PageUp: cutoff + HOUR,
      Home: view.start,
      End: view.end,
    };
    const next = moves[event.key];
    if (next === undefined) return;
    event.preventDefault();
    onCutoff(next);
  };

  /* ------------------------------------------------------------- drawing */

  const cutoffX = clamp(toX(cutoff), x0, x1);
  const cutoffText = `Cutoff ${formatDay(cutoff)} ${formatClock(cutoff)}`;
  const pillWidth = textWidth(cutoffText) + 30;
  const pillX = clamp(cutoffX - pillWidth / 2, 0, Math.max(width - pillWidth, 0));

  const inView = predictions.filter((prediction) => prediction.time >= view.start && prediction.time <= view.end);
  const spacing = inView.reduce((gap, prediction, index) => {
    const previous = inView[index - 1];
    return previous ? Math.min(gap, toX(prediction.time) - toX(previous.time)) : gap;
  }, 24);
  const jumpWidth = clamp(spacing - 2, 10, 24);

  return (
    <div ref={ref} className={`timeline${stale ? " stale" : ""}${dragging ? " dragging" : ""}`}>
      <div className="jump-strip" role="group" aria-label="Prediction times in the visible range">
        {width > 0 &&
          inView.map((prediction) => {
            const current = prediction.time === cutoff;
            const text =
              `prediction ${prediction.id}, ${formatUtc(prediction.time)}` +
              (prediction.findings > 0 ? `, ${plural(prediction.findings, "audit finding")}` : ", no audit findings");
            return (
              <button
                key={prediction.id}
                type="button"
                className={`jump${current ? " current" : ""}`}
                style={{ left: toX(prediction.time), width: jumpWidth }}
                aria-pressed={current}
                aria-label={`Move the cutoff to ${text}`}
                title={`Move the cutoff to ${text}`}
                onClick={() => onJump(prediction.time)}
              >
                <svg
                  className={`prediction-icon${prediction.findings > 0 ? " flagged" : ""}`}
                  width={10}
                  height={9}
                  viewBox="0 0 10 9"
                  aria-hidden="true"
                  focusable="false"
                >
                  <path d="M1,1 H9 L5,8 Z" />
                </svg>
              </button>
            );
          })}
      </div>
      {width > 0 && (
        <svg
          ref={svg}
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="group"
          aria-label="Timeline of this patient's observations around the prediction cutoff. The details table below lists the same versions."
        >
          {ticks.map((tick, index) => {
            const midnight = tick % DAY === 0;
            const label = midnight
              ? formatDay(tick)
              : !midnightInView && index === 0
                ? `${formatDay(tick)} ${formatClock(tick)}`
                : formatClock(tick);
            const x = toX(tick);
            const underCutoff = Math.abs(x - cutoffX) < textWidth(label) / 2 + 4;
            return (
              <g key={tick} aria-hidden="true">
                <line x1={x} y1={ENCOUNTER_Y - 4} x2={x} y2={height} stroke={midnight ? "var(--border)" : "var(--grid)"} strokeWidth={1} />
                <text
                  visibility={underCutoff ? "hidden" : undefined}
                  className={midnight ? "chart-label strong" : "chart-tick"}
                  x={clamp(x, x0 + textWidth(label) / 2 - 10, x1 - textWidth(label) / 2 + 10)}
                  y={TICK_BASELINE}
                  textAnchor="middle"
                >
                  {label}
                </text>
              </g>
            );
          })}

          <rect className="scrub" x={x0} y={HANDLE} width={x1 - x0} height={GROUPS_TOP - HANDLE - 4} {...pointer}>
            <title>Click or drag here to move the cutoff</title>
          </rect>

          {encounters
            .filter((encounter) => encounter.end >= view.start && encounter.start <= view.end)
            .map((encounter) => {
              const left = clamp(toX(encounter.start), x0, x1);
              const right = clamp(toX(encounter.end), x0, x1);
              return (
                <rect key={encounter.id} className="encounter" x={left} y={ENCOUNTER_Y} width={Math.max(right - left, 2)} height={5} rx={2}>
                  <title>{`Encounter ${encounter.id}: ${formatUtc(encounter.start)} to ${formatUtc(encounter.end)}`}</title>
                </rect>
              );
            })}
          {outcomes
            .filter((outcome) => outcome.time >= view.start && outcome.time <= view.end)
            .map((outcome) => {
              const x = toX(outcome.time);
              const y = ENCOUNTER_Y + 2.5;
              return (
                <path key={outcome.id} className="outcome" d={`M${x},${y - 6} L${x + 6},${y} L${x},${y + 6} L${x - 6},${y} Z`}>
                  <title>
                    {`Simulated outcome event (${outcome.type}) at ${formatUtc(outcome.time)}` +
                      (outcome.ascertained === null ? "" : `, ascertained ${formatUtc(outcome.ascertained)}`) +
                      ". Outcomes are never an input to features."}
                  </title>
                </path>
              );
            })}

          {layout.groups.map((group) => {
            const readers = featuresByType?.get(group.eventType) ?? [];
            const windows = [...new Set(readers.map((feature) => feature.window_seconds))].sort((a, b) => b - a);
            const shown = group.blocks.length;
            const summary =
              featuresByType !== null && readers.length === 0
                ? "no feature reads this type"
                : shown === group.total
                  ? plural(group.total, "observation")
                  : `${shown} of ${plural(group.total, "observation")} in this range`;
            let labelEdge = -Infinity;
            return (
              <g key={group.eventType}>
                <text className="chart-label strong halo" x={x0} y={group.top + 14}>
                  {group.eventType}
                  {group.unit ? ` (${group.unit})` : ""}
                  {width >= 480 && (
                    <tspan className="chart-axis" dx={10}>
                      {summary}
                    </tspan>
                  )}
                </text>
                <rect className="lane" x={x0} y={group.lanesTop} width={x1 - x0} height={group.lanesHeight} rx={3} />
                {windows.map((seconds) => {
                  const start = toX(cutoff - seconds * 1000);
                  const left = clamp(start, x0, x1);
                  const right = clamp(toX(cutoff), x0, x1);
                  const clipped = start < x0;
                  // An arrow marks a window that starts before the visible range.
                  const text = `${clipped ? "\u2190 " : ""}${formatWindow(seconds)}`;
                  const drawLabel = left >= labelEdge + 4 && left + textWidth(text, 11) < right;
                  if (drawLabel) labelEdge = left + textWidth(text, 11);
                  const names = readers.filter((feature) => feature.window_seconds === seconds).map((feature) => feature.name);
                  return (
                    <g key={seconds}>
                      <rect className="window" x={left} y={group.lanesTop} width={Math.max(right - left, 0)} height={group.lanesHeight}>
                        <title>{`Lookback window of ${names.join(", ")}: from ${formatWindow(seconds)} before the cutoff (exclusive) to the cutoff (inclusive)`}</title>
                      </rect>
                      {!clipped && <line className="window-edge" x1={left} y1={group.lanesTop - 3} x2={left} y2={group.lanesTop + group.lanesHeight} />}
                      {drawLabel && (
                        <text className="window-label" x={left + 3} y={group.lanesTop - 4}>
                          {text}
                        </text>
                      )}
                    </g>
                  );
                })}
                {shown === 0 && (
                  <text className="chart-axis" x={(x0 + x1) / 2} y={group.lanesTop + group.lanesHeight / 2 + 4} textAnchor="middle">
                    {group.total === 0
                      ? "no observations of this type"
                      : `none in this range (${group.earlier} earlier, ${group.later} later)`}
                  </text>
                )}
                <g className="marks">
                  {group.blocks.map((block) => {
                    const firstRow = block.versions[0];
                    const lastRow = block.versions[block.versions.length - 1];
                    const spine = firstRow?.xEvent ?? null;
                    // Part of an observation can lie outside the visible range: lines stop at the edge of the lane.
                    const blockLeft = clamp(block.x0, x0, x1);
                    const blockWidth = Math.max(clamp(block.x1, x0, x1) - blockLeft, 12);
                    return (
                      <g key={block.id} className={`block${block.id === selected ? " selected" : ""}`} onClick={() => onSelect(block.id === selected ? null : block.id)}>
                        <rect className="block-outline" x={blockLeft} y={block.top + 1} width={blockWidth} height={block.height - 2} rx={3} />
                        {spine !== null && inLane(spine) && firstRow && lastRow && block.versions.length > 1 && (
                          <line className="lineage" x1={spine} y1={firstRow.y} x2={spine} y2={lastRow.y} />
                        )}
                        {block.versions.map((item) => {
                          const markerX = item.xKnown ?? item.xEvent;
                          if (markerX === null) return null;
                          const labelWidth = item.label ? textWidth(item.label, 11) : 0;
                          const labelOnLeft = markerX + 9 + labelWidth > width - 2;
                          return (
                            <g key={item.key}>
                              <title>{tooltip(item, pipelineClockShown)}</title>
                              <rect className="row-hit" x={blockLeft} y={item.y - 9} width={blockWidth} height={18} />
                              {item.xEvent !== null && item.xKnown !== null && Math.abs(item.xKnown - item.xEvent) > 0.5 && (
                                <line
                                  className={`delay${item.eventBorrowed ? " borrowed" : ""}${item.lateForPipeline ? " late" : ""}`}
                                  x1={clamp(item.xEvent, x0, x1)}
                                  y1={item.y}
                                  x2={clamp(item.xKnown, x0, x1)}
                                  y2={item.y}
                                />
                              )}
                              {item.xEvent !== null && !item.eventBorrowed && inLane(item.xEvent) && (
                                <line className="event-tick" x1={item.xEvent} y1={item.y - 6} x2={item.xEvent} y2={item.y + 6} />
                              )}
                              {inLane(markerX) && (
                                <>
                                  {item.lateForPipeline && <circle className="late-ring" cx={markerX} cy={item.y} r={8} />}
                                  <StatusGlyph status={item.version.status} x={markerX} y={item.y} />
                                  {item.label && (
                                    <text
                                      className="version-label halo"
                                      x={labelOnLeft ? markerX - 10 : markerX + 9}
                                      y={item.y + 4}
                                      textAnchor={labelOnLeft ? "end" : "start"}
                                    >
                                      {item.label}
                                    </text>
                                  )}
                                </>
                              )}
                            </g>
                          );
                        })}
                      </g>
                    );
                  })}
                </g>
              </g>
            );
          })}

          <g
            ref={slider}
            className="cutoff"
            role="slider"
            tabIndex={0}
            aria-label="Prediction cutoff"
            aria-orientation="horizontal"
            aria-valuemin={view.start}
            aria-valuemax={view.end}
            aria-valuenow={cutoff}
            aria-valuetext={formatUtc(cutoff)}
            onKeyDown={onKeyDown}
            {...pointer}
          >
            <rect className="cutoff-hit" x={cutoffX - 12} y={HANDLE} width={24} height={Math.max(height - HANDLE, 0)} />
            <line className="cutoff-line" x1={cutoffX} y1={HANDLE - 2} x2={cutoffX} y2={height} />
            <rect className="cutoff-handle" x={pillX} y={1} width={pillWidth} height={HANDLE - 2} rx={4} />
            <path className="cutoff-grip" d={`M${pillX + 8},6 V${HANDLE - 6} M${pillX + 12},6 V${HANDLE - 6}`} />
            <text className="cutoff-text" x={pillX + 18 + (pillWidth - 24) / 2} y={15} textAnchor="middle">
              {cutoffText}
            </text>
          </g>
        </svg>
      )}
    </div>
  );
}
