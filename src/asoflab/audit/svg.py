"""Small inline-SVG charts for the self-contained HTML report.

No plotting library and no JavaScript: a report must open from a file, offline,
years later. Colours come from CSS custom properties defined by the report, so
the same marks work in light and dark mode.

Conventions: 2 px lines, markers of radius 4.5 with a 2 px surface-coloured
ring, hairline solid gridlines, one axis per chart, text in text colours and
never in a series colour, a legend whenever there are two or more series, and a
native tooltip (`<title>`) on every mark. Every chart is accompanied in the
report by a table holding the same numbers.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

FONT = "font-family: inherit; font-size: 12px"
SERIES = ("var(--series-1)", "var(--series-2)", "var(--series-3)")


@dataclass(frozen=True)
class ForestRow:
    label: str
    point: float | None
    lower: float | None
    upper: float | None
    note: str = ""  # shown instead of marks when there is no estimate


@dataclass(frozen=True)
class Series:
    name: str
    points: Sequence[tuple[float, float]]
    tooltips: Sequence[str] = ()


def nice_ticks(low: float, high: float, target: int = 5) -> list[float]:
    """Round tick values (1, 2 or 5 times a power of ten) covering [low, high]."""
    if not math.isfinite(low) or not math.isfinite(high):
        return [0.0, 1.0]
    if high <= low:
        pad = abs(low) * 0.1 or 1.0
        low, high = low - pad, high + pad
    raw = (high - low) / max(target, 1)
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= raw)
    first = math.floor(low / step) * step
    ticks = []
    value = first
    while value <= high + step * 1e-9:
        ticks.append(round(value, 12))
        value += step
    if ticks[-1] < high - step * 1e-9:
        ticks.append(round(ticks[-1] + step, 12))
    return ticks


def format_tick(value: float, step: float) -> str:
    decimals = max(0, -math.floor(math.log10(step))) if step > 0 else 0
    text = f"{value:.{min(decimals, 6)}f}"
    return "0" if float(text) == 0 else text


def _scale(low: float, high: float, start: float, end: float):  # type: ignore[no-untyped-def]
    span = (high - low) or 1.0
    return lambda value: start + (value - low) / span * (end - start)


def _text(x: float, y: float, content: str, *, anchor: str = "start", colour: str = "var(--text-2)", weight: str = "400") -> str:  # fmt: skip
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" fill="{colour}" '
        f'style="{FONT}; font-weight: {weight}">{escape(content)}</text>'
    )


def forest_plot(rows: Sequence[ForestRow], *, title: str, axis_label: str, width: int = 460) -> str:
    """Point estimates with interval whiskers on one shared axis, one row per comparison."""
    row_height, top, bottom, left, right = 34, 30, 44, 112, 34
    height = top + row_height * len(rows) + bottom
    values = [v for row in rows for v in (row.point, row.lower, row.upper) if v is not None]
    low, high = (min([*values, 0.0]), max([*values, 0.0])) if values else (-1.0, 1.0)
    pad = (high - low) * 0.08 or 0.001
    ticks = nice_ticks(low - pad, high + pad, 4)
    x = _scale(ticks[0], ticks[-1], left, width - right)
    step = ticks[1] - ticks[0]
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}" '
        f'style="max-width: {width}px; width: 100%; height: auto">',
        _text(0, 14, title, colour="var(--text)", weight="600"),
    ]
    axis_y = top + row_height * len(rows)
    for tick in ticks:
        parts.append(
            f'<line x1="{x(tick):.1f}" y1="{top}" x2="{x(tick):.1f}" y2="{axis_y}" stroke="var(--grid)" stroke-width="1"/>'
        )
        parts.append(_text(x(tick), axis_y + 16, format_tick(tick, step), anchor="middle"))
    # The zero line is the reference every row is read against.
    parts.append(
        f'<line x1="{x(0):.1f}" y1="{top - 4}" x2="{x(0):.1f}" y2="{axis_y}" stroke="var(--text-muted)" stroke-width="1"/>'
    )
    parts.append(
        _text(
            (left + width - right) / 2,
            axis_y + 34,
            axis_label,
            anchor="middle",
            colour="var(--text-muted)",
        )
    )
    for index, row in enumerate(rows):
        y = top + row_height * index + row_height / 2
        parts.append(_text(left - 10, y + 4, row.label, anchor="end", colour="var(--text)"))
        if row.point is None:
            parts.append(
                _text(left + 6, y + 4, row.note or "not estimable", colour="var(--text-muted)")
            )
            continue
        tooltip = f"{row.label}: {row.point:+.4f}"
        if row.lower is not None and row.upper is not None:
            tooltip += f" (95% interval {row.lower:+.4f} to {row.upper:+.4f})"
            parts.append(
                f'<line x1="{x(row.lower):.1f}" y1="{y:.1f}" x2="{x(row.upper):.1f}" y2="{y:.1f}" '
                f'stroke="var(--series-1)" stroke-width="2" stroke-linecap="round"><title>{escape(tooltip)}</title></line>'
            )
        parts.append(
            f'<circle cx="{x(row.point):.1f}" cy="{y:.1f}" r="5.5" fill="var(--series-1)" '
            f'stroke="var(--surface)" stroke-width="2"><title>{escape(tooltip)}</title></circle>'
        )
    parts.append("</svg>")
    return "".join(parts)


def line_chart(
    series: Sequence[Series],
    *,
    title: str,
    x_label: str,
    y_label: str,
    diagonal: bool = False,
    log_x: bool = False,
    width: int = 460,
    height: int = 320,
    zero_based: bool = True,
) -> str:
    """Lines with markers on one pair of axes. A legend is drawn when there are two or more series."""
    left, right, top, bottom = 56, 16, 56 if len(series) > 1 else 30, 46
    points = [point for one in series for point in one.points]
    if not points:
        return f"<p class='empty'>{escape(title)}: no data to plot.</p>"

    def tx(value: float) -> float:
        return math.log10(value) if log_x else value

    xs, ys = [tx(p[0]) for p in points], [p[1] for p in points]
    x_low, x_high = (0.0 if zero_based and not log_x else min(xs)), max(xs)
    y_low, y_high = (0.0 if zero_based else min(ys)), max(ys)
    if diagonal:
        x_high = y_high = max(x_high, y_high)
    x_ticks = nice_ticks(x_low, x_high, 5) if not log_x else sorted({tx(p[0]) for p in points})
    y_ticks = nice_ticks(y_low, y_high, 5)
    sx = _scale(x_ticks[0], x_ticks[-1], left, width - right)
    sy = _scale(y_ticks[0], y_ticks[-1], height - bottom, top)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}" '
        f'style="max-width: {width}px; width: 100%; height: auto">',
        _text(0, 14, title, colour="var(--text)", weight="600"),
    ]
    if len(series) > 1:
        cursor = 0.0
        for index, one in enumerate(series):
            colour = SERIES[index % len(SERIES)]
            parts.append(
                f'<line x1="{cursor:.1f}" y1="34" x2="{cursor + 18:.1f}" y2="34" stroke="{colour}" stroke-width="2"/>'
                f'<circle cx="{cursor + 9:.1f}" cy="34" r="4" fill="{colour}"/>'
            )
            parts.append(_text(cursor + 24, 38, one.name, colour="var(--text)"))
            cursor += 34 + 6.6 * len(one.name)
    y_step = y_ticks[1] - y_ticks[0]
    for tick in y_ticks:
        parts.append(
            f'<line x1="{left}" y1="{sy(tick):.1f}" x2="{width - right}" y2="{sy(tick):.1f}" stroke="var(--grid)" stroke-width="1"/>'
        )
        parts.append(_text(left - 8, sy(tick) + 4, format_tick(tick, y_step), anchor="end"))
    x_step = (x_ticks[1] - x_ticks[0]) if len(x_ticks) > 1 else 1.0
    for tick in x_ticks:
        label = f"{10**tick:,.0f}" if log_x else format_tick(tick, x_step)
        parts.append(_text(sx(tick), height - bottom + 16, label, anchor="middle"))
    parts.append(
        f'<line x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}" stroke="var(--text-muted)" stroke-width="1"/>'
    )
    parts.append(
        _text(
            (left + width - right) / 2,
            height - 8,
            x_label,
            anchor="middle",
            colour="var(--text-muted)",
        )
    )
    parts.append(
        f'<text transform="translate(13 {(top + height - bottom) / 2:.1f}) rotate(-90)" text-anchor="middle" '
        f'fill="var(--text-muted)" style="{FONT}">{escape(y_label)}</text>'
    )
    if diagonal:
        parts.append(
            f'<line x1="{sx(x_ticks[0]):.1f}" y1="{sy(x_ticks[0]):.1f}" x2="{sx(x_ticks[-1]):.1f}" y2="{sy(x_ticks[-1]):.1f}" '
            f'stroke="var(--text-muted)" stroke-width="1"><title>Perfect calibration</title></line>'
        )
    for index, one in enumerate(series):
        colour = SERIES[index % len(SERIES)]
        path = " ".join(
            f"{'M' if position == 0 else 'L'}{sx(tx(px)):.1f},{sy(py):.1f}"
            for position, (px, py) in enumerate(one.points)
        )
        parts.append(
            f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        for position, (px, py) in enumerate(one.points):
            tooltip = (
                one.tooltips[position]
                if position < len(one.tooltips)
                else f"{one.name}: {px:g}, {py:g}"
            )
            parts.append(
                f'<circle cx="{sx(tx(px)):.1f}" cy="{sy(py):.1f}" r="4.5" fill="{colour}" stroke="var(--surface)" '
                f'stroke-width="2"><title>{escape(tooltip)}</title></circle>'
            )
    parts.append("</svg>")
    return "".join(parts)


def bar_chart(
    categories: Sequence[str],
    groups: Sequence[tuple[str, Sequence[float | None]]],
    *,
    title: str,
    y_label: str,
    tooltips: Sequence[Sequence[str]] = (),
    width: int = 460,
    height: int = 320,
) -> str:
    """Grouped columns from a single zero baseline. `groups` is [(series name, value per category)]."""
    left, right, top, bottom = 56, 16, 56 if len(groups) > 1 else 30, 46
    values = [value for _, series in groups for value in series if value is not None]
    if not values:
        return f"<p class='empty'>{escape(title)}: no data to plot.</p>"
    y_ticks = nice_ticks(0.0, max(values), 5)
    sy = _scale(y_ticks[0], y_ticks[-1], height - bottom, top)
    band = (width - left - right) / max(len(categories), 1)
    bar = min(24.0, (band - 12) / max(len(groups), 1) - 2)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}" '
        f'style="max-width: {width}px; width: 100%; height: auto">',
        _text(0, 14, title, colour="var(--text)", weight="600"),
    ]
    if len(groups) > 1:
        cursor = 0.0
        for index, (name, _) in enumerate(groups):
            parts.append(
                f'<rect x="{cursor:.1f}" y="28" width="12" height="12" rx="2" fill="{SERIES[index % len(SERIES)]}"/>'
            )
            parts.append(_text(cursor + 18, 38, name, colour="var(--text)"))
            cursor += 28 + 6.6 * len(name)
    y_step = y_ticks[1] - y_ticks[0]
    for tick in y_ticks:
        parts.append(
            f'<line x1="{left}" y1="{sy(tick):.1f}" x2="{width - right}" y2="{sy(tick):.1f}" stroke="var(--grid)" stroke-width="1"/>'
        )
        parts.append(_text(left - 8, sy(tick) + 4, format_tick(tick, y_step), anchor="end"))
    baseline = sy(0.0)
    for position, category in enumerate(categories):
        centre = left + band * (position + 0.5)
        parts.append(_text(centre, height - bottom + 16, category, anchor="middle"))
        total = len(groups) * (bar + 2) - 2
        for index, (name, series) in enumerate(groups):
            value = series[position]
            if value is None:
                continue
            x = centre - total / 2 + index * (bar + 2)
            y = sy(value)
            radius = min(4.0, bar / 2, max(baseline - y, 0.0))
            tooltip = (
                tooltips[index][position]
                if index < len(tooltips) and position < len(tooltips[index])
                else f"{name}, {category}: {value:g}"
            )
            # Rounded at the data end, square on the baseline.
            parts.append(
                f'<path d="M{x:.1f},{baseline:.1f} V{y + radius:.1f} Q{x:.1f},{y:.1f} {x + radius:.1f},{y:.1f} '
                f'H{x + bar - radius:.1f} Q{x + bar:.1f},{y:.1f} {x + bar:.1f},{y + radius:.1f} V{baseline:.1f} Z" '
                f'fill="{SERIES[index % len(SERIES)]}"><title>{escape(tooltip)}</title></path>'
            )
    parts.append(
        f'<line x1="{left}" y1="{baseline:.1f}" x2="{width - right}" y2="{baseline:.1f}" stroke="var(--text-muted)" stroke-width="1"/>'
    )
    parts.append(
        f'<text transform="translate(13 {(top + height - bottom) / 2:.1f}) rotate(-90)" text-anchor="middle" '
        f'fill="var(--text-muted)" style="{FONT}">{escape(y_label)}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)
