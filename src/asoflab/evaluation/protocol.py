"""The prespecified analysis: which scenarios, which seeds, how large, how many resamples.

Everything here was fixed before the full set of runs was executed. Changing a
value changes the run identifiers, so results from a different protocol cannot
be mistaken for these.
"""

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from asoflab.evaluation.bootstrap import BootstrapSettings
from asoflab.evaluation.experiment import AnalysisSettings, run_experiment
from asoflab.simulation.clinical import SCENARIOS, SimulationConfig

# Seeds were chosen before any run: the year followed by 1 to 5.
PRESPECIFIED_SEEDS = (20261, 20262, 20263, 20264, 20265)
PRESPECIFIED_SCENARIOS = tuple(SCENARIOS)
FULL_PATIENTS = 3000
FULL_SETTINGS = AnalysisSettings()

# The small demonstration: one scenario, one seed, fewer patients and resamples.
DEMO_SCENARIO = "combined"
DEMO_SEED = PRESPECIFIED_SEEDS[0]
DEMO_PATIENTS = 600
DEMO_SETTINGS = AnalysisSettings(
    bootstrap=BootstrapSettings(replicates=500), subgroup_replicates=200
)


def simulation_config(scenario: str, seed: int, n_patients: int) -> SimulationConfig:
    if scenario not in SCENARIOS:
        raise KeyError(f"Unknown scenario {scenario!r}. Choose from {sorted(SCENARIOS)}.")
    return SimulationConfig(scenario=SCENARIOS[scenario], seed=seed, n_patients=n_patients)


def _one(job: tuple[str, int, int, AnalysisSettings, str]) -> tuple[str, str]:
    from asoflab.audit.report import write_run_report

    scenario, seed, n_patients, settings, out_root = job
    try:
        directory = run_experiment(
            simulation_config(scenario, seed, n_patients), settings, out_root
        )
    except FileExistsError as error:
        return f"{scenario} seed {seed}", f"skipped: {error}"
    write_run_report(directory)
    return f"{scenario} seed {seed}", f"wrote {directory.name}"


def run_matrix(
    out_root: str | Path,
    *,
    scenarios: tuple[str, ...] = PRESPECIFIED_SCENARIOS,
    seeds: tuple[int, ...] = PRESPECIFIED_SEEDS,
    n_patients: int = FULL_PATIENTS,
    settings: AnalysisSettings = FULL_SETTINGS,
    workers: int = 1,
) -> list[tuple[str, str]]:
    """Run every scenario with every seed. Existing runs are left untouched and reported as skipped."""
    jobs = [
        (scenario, seed, n_patients, settings, str(out_root))
        for scenario in scenarios
        for seed in seeds
    ]
    if workers <= 1:
        return [_one(job) for job in jobs]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_one, jobs))
