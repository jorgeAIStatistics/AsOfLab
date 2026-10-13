"""A lactate drawn at 08:00, resulted at 11:00, and a prediction at 09:00.

Run from the repository root:

    uv run python examples/quickstart.py

It builds the archive in code, computes the same features under two time
policies with the batch backend, checks the answer against the reference
oracle, and prints why the result was or was not usable.
"""

from asoflab.audit.parity import compare_feature_tables
from asoflab.batch import compute_features
from asoflab.oracle import compute_features as oracle_features
from asoflab.oracle.explain import explain_cutoff
from asoflab.schema import EventVersion, FeatureConfig, PredictionRow, TimePolicy
from asoflab.schema.tables import events_to_table, feature_rows_to_table, spine_to_table
from asoflab.schema.validation import validate_inputs

events = [
    EventVersion(
        entity_id="patient-1",
        logical_event_id="lactate-1",
        version_id="v1",
        source_revision=1,
        operation="upsert",
        event_type="lab.lactate",
        event_time="2025-03-01T08:00:00Z",  # when the blood was drawn
        value=4.2,
        unit="mmol/L",
        source="lab-system",
        schema_version="1",
        source_available_time="2025-03-01T11:00:00Z",  # when the lab released the result
        source_availability_provenance="observed",
        pipeline_available_time="2025-03-01T11:05:00Z",  # when the model's pipeline received it
        pipeline_availability_provenance="observed",
    )
]
spine = [
    PredictionRow(
        prediction_id="at-09:00", entity_id="patient-1", prediction_time="2025-03-01T09:00:00Z"
    ),
    PredictionRow(
        prediction_id="at-12:00", entity_id="patient-1", prediction_time="2025-03-01T12:00:00Z"
    ),
]
config = FeatureConfig.from_file("examples/features.json")

events_table, spine_table = events_to_table(events), spine_to_table(spine)
validate_inputs(events_table, spine_table)

for slug in ("pipeline_as_known", "naive_event_time"):
    policy = TimePolicy.parse(slug)
    batch = compute_features(events_table, spine_table, config, policy)
    oracle = feature_rows_to_table(oracle_features(events, spine, config, policy), config)
    assert compare_feature_tables(oracle, batch, config).equivalent
    print(f"\n{slug} ({policy.strictness.value}):")
    for row in batch.to_pylist():
        values = ", ".join(f"{name}={row[name]}" for name in config.names)
        print(f"  {row['prediction_id']}: {values}")

print("\nWhy, at 09:00, as known to the pipeline:")
for status in explain_cutoff(
    events, spine[0].prediction_time, config, TimePolicy.parse("pipeline_as_known")
):
    print(
        f"  {status.version.logical_event_id} revision {status.version.source_revision}: {status.status}. {status.reason}"
    )
