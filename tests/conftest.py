"""Shared pytest configuration."""

import os

from hypothesis import HealthCheck, settings

# Each example runs up to three engines, one of which writes SQLite files, so
# the per-example deadline is disabled and the example count is explicit.
settings.register_profile(
    "default",
    max_examples=int(os.environ.get("ASOFLAB_HYPOTHESIS_EXAMPLES", "120")),
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
settings.register_profile("thorough", parent=settings.get_profile("default"), max_examples=1000)
settings.load_profile(os.environ.get("ASOFLAB_HYPOTHESIS_PROFILE", "default"))
