"""The handwritten fixture corpus: one tiny case per failure mode, with answers worked by hand.

Each JSON file in `cases/` is readable on its own. Two shorthands keep them short:

- A time written as "HH:MM" means that time, UTC, on the fixture's `day`.
- `"available": t` sets both `source_available_time` and `pipeline_available_time` to `t`.

`defaults` are merged into every event. Nothing else is filled in.
"""

import json
import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

from asoflab.schema.models import EventVersion, FeatureConfig, PredictionRow

_CLOCK_TIME = re.compile(r"^\d{2}:\d{2}$")
_TIME_FIELDS = (
    "event_time",
    "source_available_time",
    "pipeline_available_time",
    "ingestion_time",
    "prediction_time",
)

Expected = dict[str, dict[str, dict[str, float | int | None]]]


@dataclass(frozen=True)
class FixtureCase:
    name: str
    title: str
    invariant: str
    narrative: str
    events: list[EventVersion]
    spine: list[PredictionRow]
    config: FeatureConfig
    # policy slug -> prediction_id -> feature name -> hand-calculated value
    expected: Expected = field(default_factory=dict)
    expected_error: dict[str, Any] | None = None
    expected_warnings: dict[str, int] = field(default_factory=dict)


def _expand(record: dict[str, Any], day: str) -> dict[str, Any]:
    expanded = dict(record)
    if "available" in expanded:
        moment = expanded.pop("available")
        expanded["source_available_time"] = moment
        expanded["pipeline_available_time"] = moment
    for name in _TIME_FIELDS:
        value = expanded.get(name)
        if isinstance(value, str) and _CLOCK_TIME.match(value):
            expanded[name] = f"{day}T{value}:00+00:00"
    return expanded


def parse_case(document: dict[str, Any]) -> FixtureCase:
    day = document["day"]
    defaults = document.get("defaults", {})
    events = [EventVersion(**_expand({**defaults, **event}, day)) for event in document["events"]]
    spine = [PredictionRow(**_expand(row, day)) for row in document["spine"]]
    return FixtureCase(
        name=document["name"],
        title=document["title"],
        invariant=document["invariant"],
        narrative=document["narrative"],
        events=events,
        spine=spine,
        config=FeatureConfig(features=document["features"]),
        expected=document.get("expected", {}),
        expected_error=document.get("expected_error"),
        expected_warnings=document.get("expected_warnings", {}),
    )


def load_cases() -> list[FixtureCase]:
    """Every fixture, in file-name order."""
    directory = resources.files(__package__).joinpath("cases")
    files = sorted(
        (entry for entry in directory.iterdir() if entry.name.endswith(".json")),
        key=lambda entry: entry.name,
    )
    return [parse_case(json.loads(entry.read_text())) for entry in files]


def load_case(name: str) -> FixtureCase:
    for case in load_cases():
        if case.name == name:
            return case
    raise KeyError(f"No fixture named {name!r}.")
