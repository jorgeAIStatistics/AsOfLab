"""Plain-text formatting for the command line."""

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any


def format_time(moment: datetime | None) -> str:
    """Compact UTC rendering; microseconds are shown only when present."""
    if moment is None:
        return "-"
    text = moment.strftime("%Y-%m-%d %H:%M")
    if moment.second or moment.microsecond:
        text += moment.strftime(":%S")
    if moment.microsecond:
        text += f".{moment.microsecond:06d}"
    return text + "Z"


def format_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    cells = [[str(value) for value in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in cells:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    lines = ["  ".join(header.ljust(width) for header, width in zip(headers, widths, strict=True))]
    lines.append("  ".join("-" * width for width in widths))
    for row in cells:
        lines.append(
            "  ".join(value.ljust(width) for value, width in zip(row, widths, strict=True)).rstrip()
        )
    return "\n".join(lines)
