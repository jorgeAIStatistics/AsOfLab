"""Record and configuration models. Sections 3, 4 and 6 of the temporal contract.

These models are the row-level form of the contract. They validate one record
at a time; rules that involve several records (duplicates, revision order) are
checked by `asoflab.schema.validation`.
"""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from asoflab.contract import (
    RESERVED_EVENT_TYPE_PREFIX,
    Aggregation,
    Clock,
    Operation,
    PolicyName,
    Provenance,
    Strictness,
    UnknownAvailability,
)
from asoflab.errors import ContractViolation
from asoflab.schema.time import parse_duration_seconds, parse_utc

NonEmpty = Annotated[str, StringConstraints(min_length=1)]
UtcDatetime = Annotated[datetime, BeforeValidator(parse_utc)]

_FEATURE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_RESERVED_FEATURE_NAMES = {"prediction_id", "entity_id", "encounter_id", "prediction_time"}


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EventVersion(_Record):
    """One version of one observation. Rows are append-only."""

    entity_id: NonEmpty
    encounter_id: str | None = None
    logical_event_id: NonEmpty
    version_id: NonEmpty
    source_revision: int = Field(ge=1)
    operation: Operation
    event_type: NonEmpty
    event_time: UtcDatetime | None = None
    value: float | None = None
    unit: str | None = None
    source: NonEmpty
    schema_version: NonEmpty
    source_available_time: UtcDatetime | None = None
    source_availability_provenance: Provenance
    pipeline_available_time: UtcDatetime | None = None
    pipeline_availability_provenance: Provenance
    ingestion_time: UtcDatetime | None = None

    @model_validator(mode="after")
    def _check_contract(self) -> Self:
        where = {"logical_event_id": self.logical_event_id, "version_id": self.version_id}
        if self.event_type.startswith(RESERVED_EVENT_TYPE_PREFIX):
            raise ContractViolation(
                "E_RESERVED_TYPE",
                f"Event type {self.event_type!r} uses the reserved outcome prefix.",
                examples=[where],
                remediation="Keep outcomes in the outcome tables, not in the event stream.",
            )
        if self.value is not None and not math.isfinite(self.value):
            raise ContractViolation(
                "E_UPSERT_INCOMPLETE", "A value must be a finite number.", examples=[where]
            )
        if self.operation is Operation.UPSERT and (self.event_time is None or self.value is None):
            raise ContractViolation(
                "E_UPSERT_INCOMPLETE",
                "An upsert needs both an event_time and a value.",
                examples=[where],
            )
        for clock, moment, provenance in (
            ("source", self.source_available_time, self.source_availability_provenance),
            ("pipeline", self.pipeline_available_time, self.pipeline_availability_provenance),
        ):
            if (moment is None) != (provenance is Provenance.UNKNOWN):
                raise ContractViolation(
                    "E_PROVENANCE_MISMATCH",
                    f"{clock}_availability_provenance must be 'unknown' exactly when "
                    f"{clock}_available_time is missing.",
                    examples=[where],
                    remediation="Do not fill in a time you do not have; mark it unknown.",
                )
        return self


class PredictionRow(_Record):
    """One prediction the pipeline issued, or would have issued, at a cutoff."""

    prediction_id: NonEmpty
    entity_id: NonEmpty
    encounter_id: str | None = None
    prediction_time: UtcDatetime


class OutcomeEvent(_Record):
    """An outcome. Never an input to feature computation."""

    outcome_event_id: NonEmpty
    entity_id: NonEmpty
    encounter_id: str | None = None
    outcome_type: NonEmpty
    event_time: UtcDatetime
    ascertainment_time: UtcDatetime


class FollowUp(_Record):
    """The interval in which outcome status was observed for an encounter."""

    entity_id: NonEmpty
    encounter_id: str | None = None
    follow_up_start: UtcDatetime
    follow_up_end: UtcDatetime


class TimePolicy(_Record):
    """Which clock decides 'known' and which revision supplies the content."""

    name: PolicyName
    gate_clock: Clock = Clock.PIPELINE
    unknown_availability: UnknownAvailability = UnknownAvailability.QUARANTINE
    impute_lag_seconds: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check_combination(self) -> Self:
        if self.gate_clock is not Clock.PIPELINE and self.name is not PolicyName.LATEST_REVISED:
            raise ContractViolation(
                "E_POLICY", "gate_clock only applies to the latest_revised policy."
            )
        imputing = self.unknown_availability is not UnknownAvailability.QUARANTINE
        if self.name is PolicyName.NAIVE_EVENT_TIME and imputing:
            raise ContractViolation(
                "E_POLICY", "naive_event_time ignores availability; there is nothing to impute."
            )
        if not imputing and self.impute_lag_seconds:
            raise ContractViolation(
                "E_POLICY", "impute_lag_seconds requires unknown_availability to be an impute mode."
            )
        return self

    @property
    def clock(self) -> Clock | None:
        """The availability clock this policy consults, or None if it uses none."""
        if self.name is PolicyName.SOURCE_AS_KNOWN:
            return Clock.SOURCE
        if self.name is PolicyName.PIPELINE_AS_KNOWN:
            return Clock.PIPELINE
        if self.name is PolicyName.LATEST_REVISED:
            return self.gate_clock
        return None

    @property
    def imputes(self) -> bool:
        return self.unknown_availability is UnknownAvailability.IMPUTE_EVENT_TIME_PLUS_LAG

    @property
    def strictness(self) -> Strictness:
        if self.name is PolicyName.NAIVE_EVENT_TIME:
            return Strictness.DIAGNOSTIC
        if self.name is PolicyName.LATEST_REVISED:
            return Strictness.RETROSPECTIVE
        return Strictness.SENSITIVITY if self.imputes else Strictness.STRICT

    @property
    def faithful_replay(self) -> bool:
        """True only for an as-known policy with no imputed availability."""
        return self.strictness is Strictness.STRICT

    def slug(self) -> str:
        """A stable name for files and run identifiers."""
        parts = [self.name.value]
        if self.name is PolicyName.LATEST_REVISED and self.gate_clock is Clock.SOURCE:
            parts.append("source")
        if self.imputes:
            parts.append(f"impute{self.impute_lag_seconds}s")
        return "-".join(parts)

    @classmethod
    def parse(cls, text: str) -> "TimePolicy":
        """Build a policy from a slug such as 'pipeline_as_known'."""
        name, *options = text.split("-")
        fields: dict[str, Any] = {"name": name}
        for option in options:
            if option == "source":
                fields["gate_clock"] = Clock.SOURCE
            elif option.startswith("impute") and option.endswith("s"):
                fields["unknown_availability"] = UnknownAvailability.IMPUTE_EVENT_TIME_PLUS_LAG
                fields["impute_lag_seconds"] = int(option[len("impute") : -1])
            else:
                raise ContractViolation("E_POLICY", f"Unknown policy option {option!r}.")
        try:
            return cls(**fields)
        except ValueError as error:
            raise ContractViolation("E_POLICY", f"Unknown policy {text!r}.") from error


class FeatureSpec(_Record):
    """One feature: an aggregation of one event type over a lookback window."""

    name: str
    event_type: NonEmpty
    aggregation: Aggregation
    window_seconds: int = Field(gt=0)
    scope: str = "entity"

    @model_validator(mode="before")
    @classmethod
    def _accept_window_text(cls, data: Any) -> Any:
        if isinstance(data, dict) and "window" in data:
            if "window_seconds" in data:
                raise ContractViolation(
                    "E_FEATURE_CONFIG", "Give either 'window' or 'window_seconds', not both."
                )
            data = dict(data)
            data["window_seconds"] = parse_duration_seconds(data.pop("window"))
        return data

    @field_validator("name")
    @classmethod
    def _check_name(cls, name: str) -> str:
        if not _FEATURE_NAME.match(name) or name in _RESERVED_FEATURE_NAMES:
            raise ContractViolation(
                "E_FEATURE_CONFIG",
                f"Feature name {name!r} is not allowed.",
                remediation="Use lowercase letters, digits and underscores, starting with a "
                "letter, and avoid the output column names.",
            )
        return name

    @field_validator("scope")
    @classmethod
    def _check_scope(cls, scope: str) -> str:
        if scope != "entity":
            raise ContractViolation(
                "E_UNSUPPORTED_SCOPE",
                f"Feature scope {scope!r} is not supported.",
                remediation="Version 1 computes each feature from one entity's own events. "
                "Cross-entity features need a different execution model.",
            )
        return scope


class FeatureConfig(_Record):
    """A versioned set of features."""

    feature_set_version: NonEmpty = "1"
    features: tuple[FeatureSpec, ...]

    @model_validator(mode="after")
    def _check_features(self) -> Self:
        if not self.features:
            raise ContractViolation("E_FEATURE_CONFIG", "A feature set needs at least one feature.")
        names = [feature.name for feature in self.features]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ContractViolation("E_FEATURE_CONFIG", f"Repeated feature names: {repeated}.")
        return self

    @property
    def names(self) -> list[str]:
        return [feature.name for feature in self.features]

    def event_types(self) -> list[str]:
        return sorted({feature.event_type for feature in self.features})

    def max_window_seconds(self) -> dict[str, int]:
        """Longest lookback per event type: how long a value can stay relevant."""
        longest: dict[str, int] = {}
        for feature in self.features:
            longest[feature.event_type] = max(
                longest.get(feature.event_type, 0), feature.window_seconds
            )
        return longest

    def canonical(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def config_hash(self) -> str:
        """SHA-256 of the canonical JSON: the feature definition version."""
        text = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()

    @classmethod
    def from_file(cls, path: str | Path) -> "FeatureConfig":
        return cls.model_validate(json.loads(Path(path).read_text()))


class LabelSpec(_Record):
    """Definition of the fixed-horizon binary target. Section 8 of the contract."""

    outcome_type: NonEmpty
    horizon_seconds: int = Field(gt=0)
    max_ascertainment_lag_seconds: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class FeatureRow:
    """Features for one prediction. `values` maps feature name to its value."""

    prediction_id: str
    entity_id: str
    encounter_id: str | None
    prediction_time: datetime
    values: dict[str, float | int | None]
