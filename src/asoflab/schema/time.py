"""UTC normalisation. Section 2 of the temporal contract."""

import re
from datetime import UTC, datetime, timedelta

from asoflab.errors import ContractViolation

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_FRACTION = re.compile(r"[.,](\d+)")
_DURATION = re.compile(r"^\s*(\d+)\s*([smhd])\s*$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_utc(value: datetime | str) -> datetime:
    """Return the same instant as a timezone-aware UTC datetime.

    Naive values are refused rather than assumed to be UTC, and precision
    finer than a microsecond is refused rather than silently truncated.
    """
    if isinstance(value, datetime):
        if getattr(value, "nanosecond", 0):
            raise ContractViolation(
                "E_TS_PRECISION",
                f"Timestamp {value!r} has precision finer than a microsecond.",
                remediation="Round or truncate deliberately before loading.",
            )
        parsed = value
    elif isinstance(value, str):
        parsed = _parse_iso(value)
    else:
        raise ContractViolation("E_TS_INVALID", f"Cannot interpret {value!r} as a timestamp.")
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ContractViolation(
            "E_TS_NAIVE",
            f"Timestamp {value!r} has no UTC offset, so the instant is ambiguous.",
            remediation="Supply an explicit offset such as 'Z' or '+00:00'.",
        )
    return parsed.astimezone(UTC)


def _parse_iso(text: str) -> datetime:
    cleaned = text.strip()
    fraction = _FRACTION.search(cleaned)
    if fraction and len(fraction.group(1)) > 6:
        digits = fraction.group(1)
        if digits[6:].strip("0"):
            raise ContractViolation(
                "E_TS_PRECISION",
                f"Timestamp {text!r} has precision finer than a microsecond.",
                remediation="Round or truncate deliberately before loading.",
            )
        cleaned = cleaned[: fraction.start(1)] + digits[:6] + cleaned[fraction.end(1) :]
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError as error:
        raise ContractViolation(
            "E_TS_INVALID", f"Timestamp {text!r} is not valid ISO-8601: {error}."
        ) from error


def to_us(moment: datetime) -> int:
    """Microseconds since the Unix epoch, as an exact integer."""
    delta = moment - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def from_us(microseconds: int) -> datetime:
    return _EPOCH + timedelta(microseconds=microseconds)


def parse_duration_seconds(value: int | str) -> int:
    """Parse '90s', '30m', '24h', or '2d' into whole seconds.

    A day is exactly 24 hours: durations are elapsed time, never calendar time.
    """
    if isinstance(value, bool):
        raise ContractViolation("E_FEATURE_CONFIG", f"Invalid duration {value!r}.")
    if isinstance(value, int):
        seconds = value
    else:
        match = _DURATION.match(value)
        if not match:
            raise ContractViolation(
                "E_FEATURE_CONFIG",
                f"Cannot read duration {value!r}.",
                remediation="Use whole seconds, or a number followed by s, m, h, or d.",
            )
        seconds = int(match.group(1)) * _UNIT_SECONDS[match.group(2)]
    if seconds <= 0:
        raise ContractViolation("E_FEATURE_CONFIG", f"A duration must be positive, got {value!r}.")
    return seconds
