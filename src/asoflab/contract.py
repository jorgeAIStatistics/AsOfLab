"""Names and constants of the temporal contract (docs/temporal-contract.md).

Everything here is shared vocabulary. The rules that *select* data live
separately in each engine so the engines can check one another.
"""

from enum import StrEnum

CONTRACT_VERSION = "1.0.0"

# An event type starting with this prefix is refused in the event stream.
RESERVED_EVENT_TYPE_PREFIX = "outcome."

# Tolerances for comparing floating-point aggregates between engines.
FLOAT_REL_TOL = 1e-9
FLOAT_ABS_TOL = 1e-12

MICROSECONDS_PER_SECOND = 1_000_000
MICROSECONDS_PER_HOUR = 3_600 * MICROSECONDS_PER_SECOND


class Operation(StrEnum):
    UPSERT = "upsert"
    RETRACT = "retract"


class Provenance(StrEnum):
    OBSERVED = "observed"
    ASSUMED = "assumed"
    UNKNOWN = "unknown"


class Clock(StrEnum):
    """Which availability timestamp decides when a version was known."""

    SOURCE = "source"
    PIPELINE = "pipeline"


class PolicyName(StrEnum):
    SOURCE_AS_KNOWN = "source_as_known"
    PIPELINE_AS_KNOWN = "pipeline_as_known"
    LATEST_REVISED = "latest_revised"
    NAIVE_EVENT_TIME = "naive_event_time"


class UnknownAvailability(StrEnum):
    QUARANTINE = "quarantine"
    IMPUTE_EVENT_TIME_PLUS_LAG = "impute_event_time_plus_lag"


class Aggregation(StrEnum):
    LAST = "last"
    COUNT = "count"
    MEAN = "mean"
    TIME_SINCE_LAST = "time_since_last"


class Strictness(StrEnum):
    """How a run may be described. Only STRICT is a faithful as-known replay."""

    STRICT = "strict"
    SENSITIVITY = "sensitivity"
    RETROSPECTIVE = "retrospective"
    DIAGNOSTIC = "diagnostic"
