"""Section 2 of the contract: UTC normalisation and what is refused."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from asoflab.errors import ContractViolation
from asoflab.schema.time import from_us, parse_duration_seconds, parse_utc, to_us


def test_equivalent_instants_are_equal() -> None:
    forms = [
        "2025-03-01T08:00:00Z",
        "2025-03-01T08:00:00+00:00",
        "2025-03-01T03:00:00-05:00",
        "2025-03-01T17:00:00+09:00",
        "2025-03-01 08:00:00.000000+00:00",
        datetime(2025, 3, 1, 3, 0, tzinfo=timezone(timedelta(hours=-5))),
    ]
    parsed = {parse_utc(form) for form in forms}
    assert parsed == {datetime(2025, 3, 1, 8, 0, tzinfo=UTC)}
    assert all(parse_utc(form).utcoffset() == timedelta(0) for form in forms)


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("2025-03-01T08:00:00", "E_TS_NAIVE"),
        ("2025-03-01", "E_TS_NAIVE"),
        (datetime(2025, 3, 1, 8, 0), "E_TS_NAIVE"),
        ("2025-03-01T08:00:00.1234567Z", "E_TS_PRECISION"),
        ("2025-03-01T25:00:00Z", "E_TS_INVALID"),
        ("2025-02-30T08:00:00Z", "E_TS_INVALID"),
        ("yesterday", "E_TS_INVALID"),
        (1740816000, "E_TS_INVALID"),
    ],
)
def test_ambiguous_or_invalid_timestamps_fail_clearly(value: object, code: str) -> None:
    with pytest.raises(ContractViolation) as caught:
        parse_utc(value)  # type: ignore[arg-type]
    assert caught.value.code == code
    assert repr(value)[:10] in str(caught.value) or str(value)[:10] in str(caught.value)


def test_trailing_zero_sub_microsecond_digits_are_not_a_precision_loss() -> None:
    assert parse_utc("2025-03-01T08:00:00.1234560000Z").microsecond == 123456


def test_microsecond_round_trip_is_exact() -> None:
    moment = datetime(2025, 3, 1, 8, 29, 59, 999999, tzinfo=UTC)
    assert from_us(to_us(moment)) == moment
    assert to_us(moment) + 1 == to_us(datetime(2025, 3, 1, 8, 30, tzinfo=UTC))


@pytest.mark.parametrize(
    ("text", "seconds"), [("90s", 90), ("30m", 1800), ("24h", 86400), ("1d", 86400), (7200, 7200)]
)
def test_durations_are_elapsed_time(text: object, seconds: int) -> None:
    assert parse_duration_seconds(text) == seconds  # type: ignore[arg-type]


@pytest.mark.parametrize("text", ["0h", "-1h", "1 week", "1.5h", "", True, 0])
def test_bad_durations_are_refused(text: object) -> None:
    with pytest.raises(ContractViolation) as caught:
        parse_duration_seconds(text)  # type: ignore[arg-type]
    assert caught.value.code == "E_FEATURE_CONFIG"
