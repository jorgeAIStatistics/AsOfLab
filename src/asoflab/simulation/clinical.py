"""A seeded simulation of hospital encounters with delayed, corrected and retracted observations.

THIS IS A COMPUTATIONAL SIMULATION. It is not a validated physiological model,
its event types are named after familiar measurements only for readability,
and nothing it produces is a clinical risk estimate.

Data-generating process (version `clinical-sim/1`), per encounter, on an hourly grid:

    severity     z[t] = patient effect + age effect + encounter effect + s[t]
                 s[t+1] = phi * s[t] + Normal(0, sigma)            (latent, evolving)
    outcome      P(event in hour t | no event before) = 1 - exp(-exp(a + b * z[t]))
    measurement  in each hour a stream is measured with probability
                 min(1, exp(gamma * z[t]) / interval)              (sicker: measured more often)
    value        intercept + slope * z[t] + noise, on a log scale for laboratory streams

The outcome is drawn from the hazard. No feature is a copy of the label.

Timing mistakes are possible only because of three separately switchable
mechanisms: laboratory results become available after a delay, some
observations are later corrected or withdrawn, and outcomes are ascertained
after a delay. With all three off, every time policy must give identical
features: that is the negative control.

Each patient has separate random streams for traits, encounters, the latent
path, measurement, delays, revisions and outcome ascertainment. Switching a
mechanism on therefore changes only what that mechanism controls: the same
seed gives the same patients, severity paths, measurement times, true values
and outcomes in every scenario that shares a measurement process.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field

from asoflab.schema.models import FeatureConfig, LabelSpec, UtcDatetime
from asoflab.schema.tables import (
    EVENT_SCHEMA,
    EVENT_SORT_KEYS,
    FOLLOW_UP_SCHEMA,
    OUTCOME_SCHEMA,
    SPINE_SCHEMA,
    TIMESTAMP,
    content_hash,
    write_parquet,
)
from asoflab.schema.time import to_us

CLINICAL_GENERATOR_VERSION = "clinical-sim/1"
OUTCOME_TYPE = "deterioration"
_US = 1_000_000
_MINUTE_US = 60 * _US
_HOUR_US = 60 * _MINUTE_US

PATIENT_SCHEMA = pa.schema(
    [("entity_id", pa.string()), ("site", pa.string()), ("age_band", pa.string())]
)
ENCOUNTER_SCHEMA = pa.schema(
    [
        ("entity_id", pa.string()),
        ("encounter_id", pa.string()),
        ("encounter_start", TIMESTAMP),
        ("encounter_end", TIMESTAMP),
    ]
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Site(_Frozen):
    name: str
    share: float
    measurement_interval_scale: float
    lab_delay_scale: float
    pipeline_batch_minutes: int
    pipeline_processing_minutes: int


class Stream(_Frozen):
    """One measured quantity. `interval_hours` is the mean gap at average severity."""

    event_type: str
    short_name: str
    unit: str
    is_lab: bool
    interval_hours: float
    severity_frequency: float
    intercept: float
    slope: float
    noise_sd: float
    log_scale: bool
    decimals: int
    delay_median_minutes: float = 0.0
    delay_sigma: float = 0.0
    correction_error_sd: float = 0.0


class ClinicalParameters(_Frozen):
    """Every number of the data-generating process. Fixed before any comparison was run."""

    horizon_hours: int = 12
    prediction_interval_hours: int = 4
    first_prediction_hour: int = 4

    patient_effect_sd: float = 0.6
    encounter_effect_sd: float = 0.3
    age_bands: tuple[str, ...] = ("under_50", "50_to_69", "70_plus")
    age_band_shares: tuple[float, ...] = (0.3, 0.4, 0.3)
    age_band_effects: tuple[float, ...] = (0.0, 0.2, 0.4)
    ar_coefficient: float = 0.95
    innovation_sd: float = 0.25
    # The intercept was set to give roughly 5% of predictions a positive label. It is the
    # only value chosen by looking at simulated output, and only at the outcome rate.
    hazard_intercept: float = -6.2
    hazard_slope: float = 1.0

    extra_encounter_rate: float = 0.6
    max_encounters: int = 4
    gap_days_mean: float = 20.0
    length_min_hours: int = 24
    length_gamma_shape: float = 2.0
    length_gamma_scale_hours: float = 24.0
    length_max_hours: int = 240

    lab_correction_rate: float = 0.08
    lab_retraction_rate: float = 0.015
    lab_event_time_error_rate: float = 0.01
    vital_correction_rate: float = 0.02
    correction_delay_median_hours: float = 6.0
    correction_delay_sigma: float = 0.6
    retraction_delay_median_hours: float = 8.0
    event_time_error_sd_minutes: float = 45.0

    outcome_delay_median_hours: float = 8.0
    outcome_delay_sigma: float = 0.8
    outcome_delay_max_hours: int = 72

    sites: tuple[Site, ...] = (
        Site(name="site_a", share=0.5, measurement_interval_scale=1.0, lab_delay_scale=1.0,
             pipeline_batch_minutes=5, pipeline_processing_minutes=1),
        Site(name="site_b", share=0.3, measurement_interval_scale=0.75, lab_delay_scale=1.5,
             pipeline_batch_minutes=15, pipeline_processing_minutes=2),
        Site(name="site_c", share=0.2, measurement_interval_scale=1.5, lab_delay_scale=2.5,
             pipeline_batch_minutes=60, pipeline_processing_minutes=10),
    )  # fmt: skip
    streams: tuple[Stream, ...] = (
        Stream(event_type="vital.heart_rate", short_name="hr", unit="bpm", is_lab=False,
               interval_hours=2.0, severity_frequency=0.2, intercept=80.0, slope=10.0,
               noise_sd=7.0, log_scale=False, decimals=0, correction_error_sd=0.25),
        Stream(event_type="vital.resp_rate", short_name="rr", unit="breaths/min", is_lab=False,
               interval_hours=4.0, severity_frequency=0.2, intercept=18.0, slope=2.5,
               noise_sd=2.5, log_scale=False, decimals=0, correction_error_sd=0.25),
        Stream(event_type="lab.lactate", short_name="lac", unit="mmol/L", is_lab=True,
               interval_hours=10.0, severity_frequency=0.4, intercept=0.4, slope=0.35,
               noise_sd=0.3, log_scale=True, decimals=1, delay_median_minutes=60.0,
               delay_sigma=0.5, correction_error_sd=0.5),
        Stream(event_type="lab.creatinine", short_name="cre", unit="mg/dL", is_lab=True,
               interval_hours=16.0, severity_frequency=0.1, intercept=0.0, slope=0.2,
               noise_sd=0.25, log_scale=True, decimals=2, delay_median_minutes=120.0,
               delay_sigma=0.5, correction_error_sd=0.5),
    )  # fmt: skip


class Scenario(_Frozen):
    """Which timing mechanisms are switched on. Each can be controlled independently."""

    name: str
    description: str
    delayed_labs: bool = False
    corrections: bool = False
    delayed_outcomes: bool = False
    multiple_encounters: bool = False
    site_variation: bool = False


SCENARIOS: dict[str, Scenario] = {
    scenario.name: scenario
    for scenario in (
        Scenario(
            name="negative_control",
            description="No delays, no revisions, one encounter per patient. Every policy must agree.",
        ),
        Scenario(
            name="delayed_labs",
            description="Laboratory results become available after a delay. Nothing else is on.",
            delayed_labs=True,
        ),
        Scenario(
            name="corrections",
            description="Some observations are later corrected or withdrawn. No delays.",
            corrections=True,
        ),
        Scenario(
            name="delayed_outcomes",
            description="Outcomes are ascertained after a delay. Features are unaffected.",
            delayed_outcomes=True,
        ),
        Scenario(
            name="multiple_encounters",
            description="Patients can have several encounters. No delays or revisions.",
            multiple_encounters=True,
        ),
        Scenario(
            name="site_variation",
            description="Delayed laboratory results, with measurement frequency, delays and "
            "pipeline cadence that differ by site.",
            delayed_labs=True,
            site_variation=True,
        ),
        Scenario(
            name="combined",
            description="Everything on: delays, revisions, delayed outcomes, several encounters, "
            "site differences.",
            delayed_labs=True,
            corrections=True,
            delayed_outcomes=True,
            multiple_encounters=True,
            site_variation=True,
        ),
    )
}


class SimulationConfig(_Frozen):
    scenario: Scenario
    seed: int
    n_patients: int = Field(gt=0)
    study_start: UtcDatetime = datetime.fromisoformat("2025-01-01T00:00:00+00:00")
    study_days: int = Field(default=180, gt=30)
    parameters: ClinicalParameters = ClinicalParameters()

    def label_spec(self) -> LabelSpec:
        lag_hours = self.parameters.outcome_delay_max_hours if self.scenario.delayed_outcomes else 0
        return LabelSpec(
            outcome_type=OUTCOME_TYPE,
            horizon_seconds=self.parameters.horizon_hours * 3600,
            max_ascertainment_lag_seconds=lag_hours * 3600,
        )

    def config_hash(self) -> str:
        document = {"generator_version": CLINICAL_GENERATOR_VERSION, **self.model_dump(mode="json")}
        text = json.dumps(document, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()


def clinical_features() -> FeatureConfig:
    """The prespecified feature set for the clinical demonstration."""
    return FeatureConfig(
        feature_set_version="clinical-1",
        features=[
            {"name": "hr_last_4h", "event_type": "vital.heart_rate", "aggregation": "last", "window": "4h"},
            {"name": "hr_mean_8h", "event_type": "vital.heart_rate", "aggregation": "mean", "window": "8h"},
            {"name": "rr_last_4h", "event_type": "vital.resp_rate", "aggregation": "last", "window": "4h"},
            {"name": "rr_mean_8h", "event_type": "vital.resp_rate", "aggregation": "mean", "window": "8h"},
            {"name": "lac_last_24h", "event_type": "lab.lactate", "aggregation": "last", "window": "24h"},
            {"name": "lac_mean_24h", "event_type": "lab.lactate", "aggregation": "mean", "window": "24h"},
            {"name": "lac_count_24h", "event_type": "lab.lactate", "aggregation": "count", "window": "24h"},
            {"name": "lac_since_24h", "event_type": "lab.lactate", "aggregation": "time_since_last", "window": "24h"},
            {"name": "cre_last_48h", "event_type": "lab.creatinine", "aggregation": "last", "window": "48h"},
            {"name": "cre_count_48h", "event_type": "lab.creatinine", "aggregation": "count", "window": "48h"},
        ],
    )  # fmt: skip


@dataclass
class SimulatedCohort:
    config: SimulationConfig
    patients: pa.Table
    encounters: pa.Table
    events: pa.Table
    spine: pa.Table
    outcomes: pa.Table
    follow_up: pa.Table

    TABLES = ("patients", "encounters", "events", "spine", "outcomes", "follow_up")

    def hashes(self) -> dict[str, str]:
        """Content hashes of every table. They identify the data, not its correctness."""
        keys = {
            "patients": ["entity_id"],
            "encounters": ["encounter_id"],
            "events": EVENT_SORT_KEYS,
            "spine": ["prediction_id"],
            "outcomes": ["outcome_event_id"],
            "follow_up": ["entity_id", "encounter_id"],
        }
        return {name: content_hash(getattr(self, name), keys[name]) for name in self.TABLES}

    def write(self, directory: str | Path) -> dict[str, Path]:
        directory = Path(directory)
        return {
            name: write_parquet(getattr(self, name), directory / f"{name}.parquet")
            for name in self.TABLES
        }

    @classmethod
    def read(cls, directory: str | Path, config: SimulationConfig) -> "SimulatedCohort":
        directory = Path(directory)
        tables = {name: pq.read_table(directory / f"{name}.parquet") for name in cls.TABLES}
        return cls(config=config, **tables)


class _Rows:
    """Column lists that are filled encounter by encounter and turned into tables at the end."""

    def __init__(self, names: list[str]) -> None:
        self.columns: dict[str, list[Any]] = {name: [] for name in names}

    def add(self, **values: Any) -> None:
        for name, value in values.items():
            self.columns[name].append(value)

    def extend(self, **values: list[Any]) -> None:
        for name, value in values.items():
            self.columns[name].extend(value)


def simulate(config: SimulationConfig) -> SimulatedCohort:
    """Generate one cohort. Deterministic for a given configuration on a given platform."""
    parameters = config.parameters
    scenario = config.scenario
    study_start_us = to_us(config.study_start)
    study_end_us = study_start_us + config.study_days * 24 * _HOUR_US
    horizon_us = parameters.horizon_hours * _HOUR_US
    site_shares = np.array([site.share for site in parameters.sites])
    baseline_site = parameters.sites[0]
    stationary_sd = parameters.innovation_sd / np.sqrt(1.0 - parameters.ar_coefficient**2)

    patients = _Rows(PATIENT_SCHEMA.names)
    encounters = _Rows(ENCOUNTER_SCHEMA.names)
    events = _Rows(EVENT_SCHEMA.names)
    spine = _Rows(SPINE_SCHEMA.names)
    outcomes = _Rows(OUTCOME_SCHEMA.names)
    follow_up = _Rows(FOLLOW_UP_SCHEMA.names)

    for patient_index, patient_seed in enumerate(
        np.random.SeedSequence(config.seed).spawn(config.n_patients)
    ):
        traits, visits, latent, measuring, delays, revising, ascertaining = (
            np.random.default_rng(child) for child in patient_seed.spawn(7)
        )
        entity_id = f"P{patient_index:06d}"
        site = parameters.sites[int(traits.choice(len(parameters.sites), p=site_shares))]
        age_index = int(traits.choice(len(parameters.age_bands), p=parameters.age_band_shares))
        patient_effect = traits.normal(0.0, parameters.patient_effect_sd)
        patients.add(entity_id=entity_id, site=site.name, age_band=parameters.age_bands[age_index])
        # Without site variation every site behaves like the first one; the label is kept.
        behaviour = site if scenario.site_variation else baseline_site

        extra = min(
            int(visits.poisson(parameters.extra_encounter_rate)), parameters.max_encounters - 1
        )
        n_encounters = 1 + (extra if scenario.multiple_encounters else 0)
        latest_first_start = config.study_days * 24 * 60 - 14 * 24 * 60
        start_us = study_start_us + int(visits.integers(0, latest_first_start)) * _MINUTE_US

        for encounter_number in range(1, parameters.max_encounters + 1):
            length_hours = int(
                min(
                    parameters.length_min_hours
                    + round(
                        visits.gamma(
                            parameters.length_gamma_shape, parameters.length_gamma_scale_hours
                        )
                    ),
                    parameters.length_max_hours,
                )
            )
            gap_minutes = int(visits.exponential(parameters.gap_days_mean * 24 * 60)) + 60
            end_us = start_us + length_hours * _HOUR_US
            if encounter_number > n_encounters or end_us + horizon_us > study_end_us:
                break
            encounter_id = f"{entity_id}-E{encounter_number}"
            encounters.add(
                entity_id=entity_id,
                encounter_id=encounter_id,
                encounter_start=start_us,
                encounter_end=end_us,
            )
            follow_up.add(
                entity_id=entity_id,
                encounter_id=encounter_id,
                follow_up_start=start_us,
                follow_up_end=end_us + horizon_us,
            )

            # Latent severity for every hour of the encounter and of the follow-up after it.
            n_hours = length_hours + parameters.horizon_hours
            encounter_effect = latent.normal(0.0, parameters.encounter_effect_sd)
            wander = np.empty(n_hours)
            wander[0] = latent.normal(0.0, stationary_sd)
            shocks = latent.normal(0.0, parameters.innovation_sd, size=n_hours)
            for hour in range(1, n_hours):
                wander[hour] = parameters.ar_coefficient * wander[hour - 1] + shocks[hour]
            severity = (
                patient_effect + parameters.age_band_effects[age_index] + encounter_effect + wander
            )

            # Outcome: the first hour in which the hazard fires, at a random second within it.
            hazard = 1.0 - np.exp(
                -np.exp(parameters.hazard_intercept + parameters.hazard_slope * severity)
            )
            fired = np.flatnonzero(latent.random(n_hours) < hazard)
            second_in_hour = int(latent.integers(1, 3600))
            ascertainment_delay_hours = min(
                ascertaining.lognormal(
                    np.log(parameters.outcome_delay_median_hours), parameters.outcome_delay_sigma
                ),
                float(parameters.outcome_delay_max_hours),
            )
            outcome_us = None
            if len(fired):
                outcome_us = start_us + int(fired[0]) * _HOUR_US + second_in_hour * _US
                delay_us = (
                    round(ascertainment_delay_hours * 3600) * _US
                    if scenario.delayed_outcomes
                    else 0
                )
                outcomes.add(
                    outcome_event_id=f"{encounter_id}-outcome",
                    entity_id=entity_id,
                    encounter_id=encounter_id,
                    outcome_type=OUTCOME_TYPE,
                    event_time=outcome_us,
                    ascertainment_time=outcome_us + delay_us,
                )

            # Predictions: every few hours while the encounter lasts and the patient is still at risk.
            for hour in range(
                parameters.first_prediction_hour,
                length_hours + 1,
                parameters.prediction_interval_hours,
            ):
                prediction_us = start_us + hour * _HOUR_US
                if outcome_us is not None and prediction_us >= outcome_us:
                    break
                spine.add(
                    prediction_id=f"{encounter_id}-T{hour:03d}",
                    entity_id=entity_id,
                    encounter_id=encounter_id,
                    prediction_time=prediction_us,
                )

            for stream in parameters.streams:
                _measure(
                    events, stream, scenario, behaviour, parameters, entity_id, encounter_id,
                    start_us, length_hours, severity, measuring, delays, revising,
                )  # fmt: skip
            start_us = end_us + gap_minutes * _MINUTE_US

    return SimulatedCohort(
        config=config,
        patients=pa.table(patients.columns, schema=PATIENT_SCHEMA),
        encounters=_timed_table(encounters, ENCOUNTER_SCHEMA),
        events=_timed_table(events, EVENT_SCHEMA),
        spine=_timed_table(spine, SPINE_SCHEMA),
        outcomes=_timed_table(outcomes, OUTCOME_SCHEMA),
        follow_up=_timed_table(follow_up, FOLLOW_UP_SCHEMA),
    )


def _measure(
    events: _Rows,
    stream: Stream,
    scenario: Scenario,
    site: Site,
    parameters: ClinicalParameters,
    entity_id: str,
    encounter_id: str,
    start_us: int,
    length_hours: int,
    severity: np.ndarray,
    measuring: np.random.Generator,
    delays: np.random.Generator,
    revising: np.random.Generator,
) -> None:
    """Append every version of every measurement of one stream in one encounter."""
    level = severity[:length_hours]
    interval = stream.interval_hours * site.measurement_interval_scale
    chance = np.minimum(1.0, np.exp(stream.severity_frequency * level) / interval)
    # Every random array is drawn for every hour, used or not, so that streams stay aligned
    # between scenarios.
    measured = measuring.random(length_hours) < chance
    minute = measuring.integers(0, 60, size=length_hours)
    noise = measuring.normal(0.0, stream.noise_sd, size=length_hours)
    delay_draw = delays.normal(0.0, 1.0, size=length_hours)
    kind_draw = revising.random(length_hours)
    error_draw = revising.normal(0.0, 1.0, size=length_hours)
    revision_delay_draw = revising.normal(0.0, 1.0, size=length_hours)
    stranger_draw = revising.normal(0.0, 1.0, size=length_hours)
    time_error_draw = np.abs(
        revising.normal(0.0, parameters.event_time_error_sd_minutes, size=length_hours)
    )

    for sequence, hour in enumerate(np.flatnonzero(measured)):
        true_event_us = start_us + int(hour) * _HOUR_US + int(minute[hour]) * _MINUTE_US
        true_value = _value(stream, level[hour], noise[hour])

        source_us = true_event_us
        if stream.is_lab and scenario.delayed_labs:
            median_seconds = stream.delay_median_minutes * 60 * site.lab_delay_scale
            source_us += round(median_seconds * np.exp(stream.delay_sigma * delay_draw[hour])) * _US
        pipeline_us = (
            _pipeline_arrival(source_us, site)
            if stream.is_lab and scenario.delayed_labs
            else source_us
        )

        first_event_us, first_value = true_event_us, true_value
        # (operation, event time, value, median hours until the revision is released)
        second: tuple[str, int | None, float | None, float] | None = None
        if scenario.corrections:
            draw = kind_draw[hour]
            retraction_rate = parameters.lab_retraction_rate if stream.is_lab else 0.0
            correction_rate = (
                parameters.lab_correction_rate
                if stream.is_lab
                else parameters.vital_correction_rate
            )
            time_error_rate = parameters.lab_event_time_error_rate if stream.is_lab else 0.0
            if draw < retraction_rate:
                # A result filed against the wrong patient: unrelated to this patient's severity.
                first_value = _value(stream, stranger_draw[hour], noise[hour])
                second = ("retract", None, None, parameters.retraction_delay_median_hours)
            elif draw < retraction_rate + correction_rate:
                first_value = _corrupt(stream, true_value, error_draw[hour])
                second = (
                    "upsert",
                    true_event_us,
                    true_value,
                    parameters.correction_delay_median_hours,
                )
            elif draw < retraction_rate + correction_rate + time_error_rate:
                # Collection time first recorded too early, later corrected.
                first_event_us = true_event_us - (5 + round(time_error_draw[hour])) * _MINUTE_US
                second = (
                    "upsert",
                    true_event_us,
                    true_value,
                    parameters.correction_delay_median_hours,
                )

        logical_event_id = f"{encounter_id}-{stream.short_name}-{sequence:04d}"
        _add_version(events, stream, entity_id, encounter_id, logical_event_id, 1, "upsert",
                     first_event_us, first_value, source_us, pipeline_us)  # fmt: skip
        if second is not None:
            operation, event_us, value, median_hours = second
            lag_seconds = (
                median_hours
                * 3600
                * np.exp(parameters.correction_delay_sigma * revision_delay_draw[hour])
            )
            second_source_us = source_us + max(60, round(lag_seconds)) * _US
            second_pipeline_us = (
                _pipeline_arrival(second_source_us, site)
                if stream.is_lab and scenario.delayed_labs
                else second_source_us
            )
            _add_version(events, stream, entity_id, encounter_id, logical_event_id, 2, operation,
                         event_us, value, second_source_us, second_pipeline_us)  # fmt: skip


def _value(stream: Stream, severity: float, noise: float) -> float:
    raw = stream.intercept + stream.slope * severity + noise
    if stream.log_scale:
        raw = np.exp(raw)
    floor = 10.0**-stream.decimals
    return float(max(round(float(raw), stream.decimals), floor))


def _corrupt(stream: Stream, value: float, draw: float) -> float:
    """A wrong first report: the true value times a log-normal error."""
    floor = 10.0**-stream.decimals
    return float(
        max(round(value * float(np.exp(stream.correction_error_sd * draw)), stream.decimals), floor)
    )


def _pipeline_arrival(source_us: int, site: Site) -> int:
    """The pipeline loads in batches: the next batch boundary at or after release, plus processing."""
    batch_us = site.pipeline_batch_minutes * _MINUTE_US
    boundary = -(-source_us // batch_us) * batch_us
    return int(boundary + site.pipeline_processing_minutes * _MINUTE_US)


def _add_version(
    events: _Rows,
    stream: Stream,
    entity_id: str,
    encounter_id: str,
    logical_event_id: str,
    revision: int,
    operation: str,
    event_us: int | None,
    value: float | None,
    source_us: int,
    pipeline_us: int,
) -> None:
    events.add(
        entity_id=entity_id,
        encounter_id=encounter_id,
        logical_event_id=logical_event_id,
        version_id=f"v{revision}",
        source_revision=revision,
        operation=operation,
        event_type=stream.event_type,
        event_time=event_us,
        value=value,
        unit=stream.unit,
        source="simulated_lab_system" if stream.is_lab else "simulated_monitor",
        schema_version="1",
        source_available_time=source_us,
        # The simulation knows the true availability times, so they are 'observed'.
        source_availability_provenance="observed",
        pipeline_available_time=pipeline_us,
        pipeline_availability_provenance="observed",
        ingestion_time=None,
    )


def _timed_table(rows: _Rows, schema: pa.Schema) -> pa.Table:
    arrays = []
    for field in schema:
        values = rows.columns[field.name]
        if pa.types.is_timestamp(field.type):
            arrays.append(pa.array(values, pa.int64()).cast(field.type))
        else:
            arrays.append(pa.array(values, field.type))
    return pa.Table.from_arrays(arrays, schema=schema)
