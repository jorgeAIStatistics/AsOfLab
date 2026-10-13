import { statusMeta } from "../labels";

/**
 * The mark for one eligibility status. Every status has its own shape, so the
 * timeline never relies on colour alone; the legend and the details table show
 * the same shape next to the status in words.
 */
export function StatusGlyph({ status, x, y, r = 5 }: { status: string; x: number; y: number; r?: number }) {
  const { glyph, tone } = statusMeta(status);
  const filled = `glyph filled tone-${tone}`;
  const hollow = `glyph hollow tone-${tone}`;
  switch (glyph) {
    case "disc":
      return <circle className={filled} cx={x} cy={y} r={r} />;
    case "ring":
      return <circle className={hollow} cx={x} cy={y} r={r - 1} />;
    case "cross":
      return (
        <path
          className={`glyph stroke tone-${tone}`}
          d={`M${x - r + 1.5},${y - r + 1.5} L${x + r - 1.5},${y + r - 1.5} M${x - r + 1.5},${y + r - 1.5} L${x + r - 1.5},${y - r + 1.5}`}
        />
      );
    case "square":
      return <rect className={filled} x={x - r + 0.5} y={y - r + 0.5} width={2 * r - 1} height={2 * r - 1} rx={1} />;
    case "diamond":
      return <path className={hollow} d={`M${x},${y - r} L${x + r},${y} L${x},${y + r} L${x - r},${y} Z`} />;
    case "triangle":
      return <path className={hollow} d={`M${x - r + 1.5},${y - r + 0.5} L${x + r - 0.5},${y} L${x - r + 1.5},${y + r - 0.5} Z`} />;
    case "question":
      return (
        <g>
          <rect className={hollow} x={x - r + 0.5} y={y - r + 0.5} width={2 * r - 1} height={2 * r - 1} rx={1} />
          <text className={`glyph-text tone-${tone}`} x={x} y={y + 3} textAnchor="middle">
            ?
          </text>
        </g>
      );
    case "dot":
      return <circle className={filled} cx={x} cy={y} r={2.5} />;
  }
}

/** The glyph as a small inline icon, for legends and table cells. Decorative: the label is always beside it. */
export function StatusIcon({ status }: { status: string }) {
  return (
    <svg className="status-icon" width={14} height={14} viewBox="0 0 14 14" aria-hidden="true" focusable="false">
      <StatusGlyph status={status} x={7} y={7} />
    </svg>
  );
}

export function StatusLabel({ status }: { status: string }) {
  return (
    <span className="status-label">
      <StatusIcon status={status} />
      {statusMeta(status).label}
    </span>
  );
}
