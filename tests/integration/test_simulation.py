"""The clinical simulation: reproducible, independently switchable, and free of target copies."""

import inspect

import pyarrow.compute as pc
import pytest

from asoflab import batch, oracle
from asoflab.errors import ContractViolation
from asoflab.evaluation.labels import assign_labels
from asoflab.replay import replay_features
from asoflab.schema.models import TimePolicy
from asoflab.schema.tables import events_to_table, table_to_events
from asoflab.schema.validation import validate_events, validate_inputs
from asoflab.simulation.clinical import SCENARIOS, SimulationConfig, clinical_features, simulate

N = 150
SEED = 20261


def cohort(name: str, seed: int = SEED, n_patients: int = N):  # type: ignore[no-untyped-def]
    return simulate(SimulationConfig(scenario=SCENARIOS[name], seed=seed, n_patients=n_patients))


def test_the_same_configuration_gives_the_same_data() -> None:
    first, again, other = cohort("combined"), cohort("combined"), cohort("combined", seed=SEED + 1)
    assert first.hashes() == again.hashes()
    assert first.hashes()["events"] != other.hashes()["events"]
    assert first.config.config_hash() == again.config.config_hash() != other.config.config_hash()


def test_adding_patients_does_not_change_existing_patients() -> None:
    small, large = cohort("combined", n_patients=40), cohort("combined", n_patients=80)
    shared = pc.is_in(large.events.column("entity_id"), small.patients.column("entity_id"))
    assert large.events.filter(shared).equals(small.events)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_scenario_satisfies_the_contract(name: str) -> None:
    data = cohort(name)
    report = validate_inputs(data.events, data.spine, TimePolicy.parse("pipeline_as_known"))
    assert report.ok
    assert not report.warnings, [finding.code for finding in report.warnings]
    labels = assign_labels(data.spine, data.outcomes, data.follow_up, data.config.label_spec())
    assert labels.column("label").null_count == 0, (
        "the main cohort has complete fixed-horizon follow-up"
    )
    assert 0 < pc.sum(labels.column("label")).as_py() < labels.num_rows, "both classes are present"


def test_switches_change_only_what_they_control() -> None:
    """Same seed, different mechanisms: the patients, measurements and outcomes are shared."""
    control, delayed, corrected, late_outcomes = (
        cohort(name)
        for name in ("negative_control", "delayed_labs", "corrections", "delayed_outcomes")
    )
    for other in (delayed, corrected, late_outcomes):
        assert other.spine.equals(control.spine)
        assert other.outcomes.column("event_time").equals(control.outcomes.column("event_time"))
        assert other.encounters.equals(control.encounters)

    # Delayed laboratory results: identical measurements, later availability, laboratory streams only.
    same = ["logical_event_id", "event_type", "event_time", "value", "source_revision"]
    assert delayed.events.select(same).equals(control.events.select(same))
    is_lab = pc.starts_with(delayed.events.column("event_type"), "lab.")
    lag = pc.subtract(
        delayed.events.column("pipeline_available_time").cast("int64"),
        delayed.events.column("event_time").cast("int64"),
    )
    assert pc.min(lag.filter(is_lab)).as_py() > 0
    assert pc.max(lag.filter(pc.invert(is_lab))).as_py() == 0
    assert control.events.column("pipeline_available_time").equals(
        control.events.column("event_time")
    )

    # Corrections: revision 2 rows appear; every first version is still available immediately.
    assert pc.max(control.events.column("source_revision")).as_py() == 1
    assert pc.max(corrected.events.column("source_revision")).as_py() == 2
    assert "retract" in corrected.events.column("operation").to_pylist()

    # Delayed outcomes: the events are untouched, only outcome ascertainment moves.
    assert late_outcomes.events.equals(control.events)
    outcome_lag = pc.subtract(
        late_outcomes.outcomes.column("ascertainment_time").cast("int64"),
        late_outcomes.outcomes.column("event_time").cast("int64"),
    )
    assert pc.min(outcome_lag).as_py() > 0
    assert pc.max(outcome_lag).as_py() <= 72 * 3600 * 1_000_000, (
        "the declared maximum lag holds by construction"
    )


def test_multiple_encounters_and_site_variation_do_what_they_say() -> None:
    assert cohort("negative_control").encounters.num_rows == N
    assert cohort("multiple_encounters").encounters.num_rows > N
    varied, uniform = cohort("site_variation"), cohort("delayed_labs")
    assert varied.patients.equals(uniform.patients), "site labels are assigned either way"
    assert not varied.events.select(["event_time"]).equals(uniform.events.select(["event_time"]))


def test_outcomes_cannot_enter_feature_computation() -> None:
    """Three structural guards against an accidental join."""
    # 1. No engine accepts outcome or follow-up tables.
    for function in (oracle.compute_features, batch.compute_features, replay_features):
        parameters = set(inspect.signature(function).parameters)
        assert not {
            name for name in parameters if "outcome" in name or "follow" in name or "label" in name
        }

    # 2. Features are a function of events and spine alone: changing when outcomes are ascertained
    #    (which changes the labels available at any moment) leaves every feature bit-identical.
    control, late_outcomes = cohort("negative_control"), cohort("delayed_outcomes")
    assert not control.outcomes.equals(late_outcomes.outcomes)
    config, policy = clinical_features(), TimePolicy.parse("pipeline_as_known")
    assert batch.compute_features(control.events, control.spine, config, policy).equals(
        batch.compute_features(late_outcomes.events, late_outcomes.spine, config, policy)
    )

    # 3. An outcome smuggled into the event stream under the reserved prefix is refused.
    smuggled = table_to_events(control.events.slice(0, 1))[0].model_dump()
    smuggled["event_type"] = "outcome.deterioration"
    with pytest.raises(ContractViolation) as caught:
        events_to_table([type(table_to_events(control.events.slice(0, 1))[0])(**smuggled)])
    assert caught.value.code == "E_RESERVED_TYPE"
    as_table = control.events.slice(0, 1).set_column(
        control.events.schema.get_field_index("event_type"),
        "event_type",
        [["outcome.deterioration"]],
    )
    assert "E_RESERVED_TYPE" in [finding.code for finding in validate_events(as_table).errors]


def test_no_feature_is_a_copy_of_the_label() -> None:
    """The outcome is drawn from a hazard; no single feature should come close to separating it."""
    import numpy as np

    from asoflab.evaluation.metrics import auroc

    data = cohort("combined", n_patients=600)
    config = clinical_features()
    features = batch.compute_features(
        data.events, data.spine, config, TimePolicy.parse("naive_event_time")
    )
    labels = assign_labels(
        data.spine, data.outcomes, data.follow_up, data.config.label_spec()
    ).sort_by("prediction_id")
    y = np.array(labels.column("label").to_pylist())
    for name in config.names:
        values = features.column(name).to_numpy(zero_copy_only=False).astype(float)
        present = np.isfinite(values)
        separation = auroc(y[present], values[present])
        assert 0.25 < separation < 0.8, (
            f"{name} separates the label suspiciously well: {separation:.3f}"
        )
