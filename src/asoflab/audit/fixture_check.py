"""Run the handwritten fixtures through the engines and compare with the hand-calculated answers."""

import math
from dataclasses import dataclass

import pyarrow as pa

from asoflab.contract import FLOAT_ABS_TOL, FLOAT_REL_TOL
from asoflab.engines import run_engine
from asoflab.fixtures import load_cases
from asoflab.schema.models import TimePolicy
from asoflab.schema.tables import events_to_table, spine_to_table


@dataclass(frozen=True)
class FixtureResult:
    fixture: str
    policy: str
    engine: str
    passed: bool
    detail: str = ""


def differences(table: pa.Table, expected: dict[str, dict[str, float | int | None]]) -> list[str]:
    rows = {row["prediction_id"]: row for row in table.to_pylist()}
    problems = []
    if sorted(rows) != sorted(expected):
        problems.append(f"prediction ids {sorted(rows)}, expected {sorted(expected)}")
    for prediction_id, features in expected.items():
        row = rows.get(prediction_id)
        if row is None:
            continue
        for name, want in features.items():
            got = row[name]
            if got is None or want is None:
                same = got is None and want is None
            else:
                same = math.isclose(got, want, rel_tol=FLOAT_REL_TOL, abs_tol=FLOAT_ABS_TOL)
            if not same:
                problems.append(f"{prediction_id}.{name}: got {got!r}, expected {want!r}")
    return problems


def check_fixtures(engines: list[str]) -> list[FixtureResult]:
    results = []
    for case in load_cases():
        if case.expected_error is not None:
            continue
        events, spine = events_to_table(case.events), spine_to_table(case.spine)
        for slug, expected in case.expected.items():
            for engine in engines:
                table = run_engine(engine, events, spine, case.config, TimePolicy.parse(slug))
                problems = differences(table, expected)
                results.append(
                    FixtureResult(case.name, slug, engine, not problems, "; ".join(problems))
                )
    return results
