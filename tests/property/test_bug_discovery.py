"""Can the random generator find each seeded bug with no hints?

The fixtures prove that known mistakes are caught by cases written for them.
This module asks the harder question: starting from nothing, does the archive
generator produce an input on which each wrong implementation disagrees with
the oracle? If it cannot find a seeded bug, it would not find the real one.
"""

import pytest
from hypothesis import find, settings
from hypothesis.errors import NoSuchExample

from asoflab.oracle import compute_features as oracle
from asoflab.schema.models import TimePolicy
from seeded_bugs import CONTROL, SEEDED_BUGS
from strategies import Archive, archives
from support import same_value

POLICY = TimePolicy.parse("pipeline_as_known")
SEARCH = settings(max_examples=4000, database=None, deadline=None)


def disagrees_with_oracle(compute, archive: Archive) -> bool:  # type: ignore[no-untyped-def]
    truth = {
        row.prediction_id: row
        for row in oracle(archive.events, archive.spine, archive.config, POLICY)
    }
    other = {
        row.prediction_id: row
        for row in compute(archive.events, archive.spine, archive.config, POLICY)
    }
    if truth.keys() != other.keys():
        return True
    return any(
        not same_value(other[prediction_id].values[name], value)
        for prediction_id, row in truth.items()
        for name, value in row.values.items()
    )


def minimal_counterexample(name: str) -> Archive:
    return find(
        archives(),
        lambda archive: disagrees_with_oracle(SEEDED_BUGS[name], archive),
        settings=SEARCH,
    )


@pytest.mark.parametrize("name", sorted(SEEDED_BUGS))
def test_generator_discovers_seeded_bug(name: str) -> None:
    try:
        example = minimal_counterexample(name)
    except NoSuchExample:
        pytest.fail(f"the generator never produced an archive that exposes {name!r}")
    assert disagrees_with_oracle(SEEDED_BUGS[name], example)


def test_generator_finds_nothing_wrong_with_the_control() -> None:
    with pytest.raises(NoSuchExample):
        find(
            archives(),
            lambda archive: disagrees_with_oracle(CONTROL, archive),
            settings=settings(max_examples=1500, database=None, deadline=None),
        )
