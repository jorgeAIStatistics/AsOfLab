"""The batch backend must give bit-identical output on every run, whatever the thread count.

Found during development: with a plain floating-point mean, DuckDB's parallel
aggregation fed rows to the sum in a different order on each run, and a few
hundred of sixty thousand means differed in the last bits. Feature-table hashes
then changed between identical runs. The mean now sums in a fixed order.
"""

import pytest

from asoflab.batch import compute_features
from asoflab.schema.models import TimePolicy
from asoflab.schema.tables import content_hash
from asoflab.simulation.workload import WorkloadSpec, generate_workload, workload_features

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("slug", ["pipeline_as_known", "latest_revised", "naive_event_time"])
def test_batch_output_is_bit_identical_across_runs_and_thread_counts(slug: str) -> None:
    events, spine = generate_workload(WorkloadSpec(n_events=120_000, seed=5))
    config = workload_features()
    policy = TimePolicy.parse(slug)
    first = compute_features(events, spine, config, policy)
    for threads in (None, None, 1, 3):
        again = compute_features(events, spine, config, policy, threads=threads)
        assert again.equals(first), f"output changed with threads={threads}"
        assert content_hash(again, ["prediction_id"]) == content_hash(first, ["prediction_id"])
