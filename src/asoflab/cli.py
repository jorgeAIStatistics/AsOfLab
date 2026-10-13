"""Command-line interface. Run `asoflab --help`."""

import contextlib
import json
from pathlib import Path
from typing import Annotated, Any

import typer

from asoflab import CONTRACT_VERSION, __version__
from asoflab.api.listen import PortUnavailable, local_url, open_listener
from asoflab.engines import run_engine
from asoflab.errors import ContractViolation
from asoflab.schema.models import FeatureConfig, TimePolicy
from asoflab.schema.tables import (
    EVENT_SCHEMA,
    SPINE_SCHEMA,
    read_parquet,
    table_to_events,
    write_parquet,
)
from asoflab.textui import format_table, format_time

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="AsOfLab: what could this prediction pipeline actually know at time T?",
)
fixtures_app = typer.Typer(no_args_is_help=True, help="The handwritten fixture corpus.")
app.add_typer(fixtures_app, name="fixtures")
experiment_app = typer.Typer(
    no_args_is_help=True, help="The statistical demonstration on simulated cohorts."
)
app.add_typer(experiment_app, name="experiment")
replay_app = typer.Typer(
    no_args_is_help=True, help="Incremental replay: a local, single-machine stream simulator."
)
app.add_typer(replay_app, name="replay")

ALL_POLICIES = ["pipeline_as_known", "source_as_known", "latest_revised", "naive_event_time"]

EventsOption = Annotated[Path, typer.Option("--events", help="Parquet file of event versions.")]
SpineOption = Annotated[Path, typer.Option("--spine", help="Parquet file of prediction rows.")]
ConfigOption = Annotated[Path, typer.Option("--config", help="Feature set as JSON.")]
PolicyOption = Annotated[
    str,
    typer.Option(
        "--policy",
        help="pipeline_as_known, source_as_known, latest_revised, or naive_event_time.",
    ),
]


def _fail(error: ContractViolation) -> None:
    typer.secho(f"Contract violation {error.code}", fg=typer.colors.RED, bold=True, err=True)
    typer.echo(error.message, err=True)
    for example in error.examples[:5]:
        typer.echo(f"  example: {json.dumps(example, default=str)}", err=True)
    if error.remediation:
        typer.echo(f"What to do: {error.remediation}", err=True)
    raise typer.Exit(code=2)


@app.command()
def version() -> None:
    """Print the package and temporal-contract versions."""
    typer.echo(f"asoflab {__version__} (temporal contract {CONTRACT_VERSION})")


@app.command()
def validate(
    events: EventsOption,
    spine: SpineOption,
    policy: PolicyOption = "pipeline_as_known",
    as_json: Annotated[bool, typer.Option("--json", help="Print the full report as JSON.")] = False,
) -> None:
    """Check event and spine files against the temporal contract."""
    from asoflab.schema.validation import validate_events, validate_spine

    try:
        report = validate_events(
            read_parquet(events, EVENT_SCHEMA, "events"), TimePolicy.parse(policy)
        ).merge(validate_spine(read_parquet(spine, SPINE_SCHEMA, "spine")))
    except ContractViolation as error:
        _fail(error)
    if as_json:
        typer.echo(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        typer.echo(format_table(["count", "value"], sorted(report.counts.items())))
        for finding in report.findings:
            colour = typer.colors.RED if finding.severity == "error" else typer.colors.YELLOW
            typer.secho(f"{finding.severity.upper()} {finding.code} x{finding.count}", fg=colour)
            typer.echo(f"  {finding.message}")
            if finding.remediation:
                typer.echo(f"  What to do: {finding.remediation}")
        typer.echo("OK: no contract errors." if report.ok else "FAILED: contract errors found.")
    if not report.ok:
        raise typer.Exit(code=2)


@app.command()
def features(
    events: EventsOption,
    spine: SpineOption,
    config: ConfigOption,
    out: Annotated[Path, typer.Option("--out", help="Where to write the feature Parquet file.")],
    policy: PolicyOption = "pipeline_as_known",
    engine: Annotated[str, typer.Option("--engine", help="oracle, batch, or replay.")] = "batch",
) -> None:
    """Compute features for every prediction under one time policy."""
    from asoflab.schema.validation import validate_inputs

    if out.exists():
        typer.secho(
            f"{out} already exists. Runs are never overwritten.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)
    try:
        chosen = TimePolicy.parse(policy)
        feature_config = FeatureConfig.from_file(config)
        validate_inputs(
            read_parquet(events, EVENT_SCHEMA, "events"),
            read_parquet(spine, SPINE_SCHEMA, "spine"),
            chosen,
        )
        table = run_engine(engine, events, spine, feature_config, chosen)
    except ContractViolation as error:
        _fail(error)
    write_parquet(table, out)
    typer.echo(
        f"Wrote {table.num_rows} rows to {out} "
        f"(engine={engine}, policy={chosen.slug()}, {chosen.strictness.value})."
    )
    if not chosen.faithful_replay:
        typer.secho(
            "This policy is not a faithful historical replay of a deployed pipeline.",
            fg=typer.colors.YELLOW,
        )


@app.command()
def parity(
    events: EventsOption,
    spine: SpineOption,
    config: ConfigOption,
    policy: Annotated[
        list[str] | None, typer.Option("--policy", help="Repeatable. Defaults to all four.")
    ] = None,
    engines: Annotated[
        str, typer.Option("--engines", help="Comma-separated.")
    ] = "oracle,batch,replay",
) -> None:
    """Run several engines on the same input and require identical answers."""
    from asoflab.audit.parity import compare_feature_tables

    names = [name.strip() for name in engines.split(",") if name.strip()]
    failed = False
    try:
        feature_config = FeatureConfig.from_file(config)
        for slug in policy or ALL_POLICIES:
            chosen = TimePolicy.parse(slug)
            tables = {
                name: run_engine(name, events, spine, feature_config, chosen) for name in names
            }
            reference = names[0]
            for other in names[1:]:
                report = compare_feature_tables(
                    tables[reference],
                    tables[other],
                    feature_config,
                    left_name=reference,
                    right_name=other,
                )
                failed = failed or not report.equivalent
                colour = typer.colors.GREEN if report.equivalent else typer.colors.RED
                typer.secho(f"[{chosen.slug()}] {report.describe()}", fg=colour)
    except ContractViolation as error:
        _fail(error)
    if failed:
        raise typer.Exit(code=1)


@app.command()
def sql(config: ConfigOption, policy: PolicyOption = "pipeline_as_known") -> None:
    """Print the SQL the batch backend runs for a feature set and policy."""
    from asoflab.batch import build_query

    try:
        typer.echo(build_query(FeatureConfig.from_file(config), TimePolicy.parse(policy)))
    except ContractViolation as error:
        _fail(error)


@app.command()
def explain(
    events: EventsOption,
    config: ConfigOption,
    entity: Annotated[str, typer.Option("--entity", help="Entity (patient) identifier.")],
    cutoff: Annotated[str, typer.Option("--cutoff", help="Prediction time, ISO-8601 with offset.")],
    policy: PolicyOption = "pipeline_as_known",
) -> None:
    """Show why each version of one entity's events is or is not usable at a cutoff."""
    from asoflab.oracle.explain import explain_cutoff
    from asoflab.schema.time import parse_utc

    try:
        chosen = TimePolicy.parse(policy)
        moment = parse_utc(cutoff)
        versions = [
            version
            for version in table_to_events(read_parquet(events, EVENT_SCHEMA, "events"))
            if version.entity_id == entity
        ]
        statuses = explain_cutoff(versions, moment, FeatureConfig.from_file(config), chosen)
    except ContractViolation as error:
        _fail(error)
    typer.echo(f"Entity {entity} at {moment.isoformat()} under {chosen.slug()}")
    typer.echo(_status_table(statuses))


def _status_table(statuses: list[Any]) -> str:
    rows = [
        [
            status.version.logical_event_id,
            status.version.source_revision,
            status.version.operation.value,
            format_time(status.version.event_time),
            format_time(status.known_time),
            "" if status.version.value is None else f"{status.version.value:g}",
            status.status,
        ]
        for status in statuses
    ]
    return format_table(["event", "rev", "op", "event time", "known", "value", "status"], rows)


@fixtures_app.command("list")
def fixtures_list() -> None:
    """List the fixtures and the invariant each one pins down."""
    from asoflab.fixtures import load_cases

    typer.echo(format_table(["name", "invariant"], [[c.name, c.invariant] for c in load_cases()]))


@fixtures_app.command("show")
def fixtures_show(name: str) -> None:
    """Walk through one fixture: its story, every version, and the answer under each policy."""
    from asoflab.fixtures import load_case
    from asoflab.oracle import compute_features as oracle_features
    from asoflab.oracle.explain import explain_cutoff

    try:
        case = load_case(name)
    except KeyError as error:
        raise typer.BadParameter(str(error)) from error
    typer.secho(case.title, bold=True)
    typer.echo(case.narrative + "\n")
    if case.expected_error:
        typer.echo(f"Expected outcome: refused with {case.expected_error['code']}.")
        return
    for slug in case.expected:
        chosen = TimePolicy.parse(slug)
        typer.secho(f"Policy {slug} ({chosen.strictness.value})", bold=True)
        rows = oracle_features(case.events, case.spine, case.config, chosen)
        typer.echo(
            format_table(
                ["prediction", "cutoff", *case.config.names],
                [
                    [
                        row.prediction_id,
                        format_time(row.prediction_time),
                        *(
                            "-" if row.values[n] is None else f"{row.values[n]:g}"
                            for n in case.config.names
                        ),
                    ]
                    for row in rows
                ],
            )
        )
        first = case.spine[0]
        typer.echo(f"Why, for prediction {first.prediction_id}:")
        mine = [v for v in case.events if v.entity_id == first.entity_id]
        typer.echo(_status_table(explain_cutoff(mine, first.prediction_time, case.config, chosen)))
        typer.echo("")


@fixtures_app.command("check")
def fixtures_check(
    engines: Annotated[
        str, typer.Option("--engines", help="Comma-separated.")
    ] = "oracle,batch,replay",
) -> None:
    """Run every fixture through every engine and compare with the hand-calculated answers."""
    from asoflab.audit.fixture_check import check_fixtures

    names = [name.strip() for name in engines.split(",") if name.strip()]
    results = check_fixtures(names)
    failures = [result for result in results if not result.passed]
    typer.echo(
        format_table(
            ["fixture", "policy", "engine", "result"],
            [
                [r.fixture, r.policy, r.engine, "ok" if r.passed else "FAIL: " + r.detail]
                for r in results
            ],
        )
    )
    typer.echo(f"{len(results) - len(failures)} of {len(results)} checks passed.")
    if failures:
        raise typer.Exit(code=1)


@app.command()
def simulate(
    out: Annotated[Path, typer.Option("--out", help="Directory for the simulated tables.")],
    scenario: Annotated[
        str, typer.Option("--scenario", help="Scenario name. See 'experiment scenarios'.")
    ] = "combined",
    seed: Annotated[int, typer.Option("--seed")] = 20261,
    patients: Annotated[int, typer.Option("--patients")] = 600,
) -> None:
    """Generate one synthetic cohort (events, spine, outcomes, follow-up) as Parquet files."""
    from asoflab.evaluation.protocol import simulation_config
    from asoflab.simulation.clinical import simulate as run_simulation

    if out.exists() and any(out.iterdir()):
        typer.secho(
            f"{out} is not empty. Runs are never overwritten.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)
    cohort = run_simulation(simulation_config(scenario, seed, patients))
    cohort.write(out)
    typer.secho("SYNTHETIC DATA", fg=typer.colors.YELLOW, bold=True)
    typer.echo(
        format_table(
            ["table", "rows", "content hash"],
            [
                [name, getattr(cohort, name).num_rows, digest[:16]]
                for name, digest in cohort.hashes().items()
            ],
        )
    )


@experiment_app.command("scenarios")
def experiment_scenarios() -> None:
    """List the prespecified scenarios and seeds."""
    from asoflab.evaluation.protocol import FULL_PATIENTS, PRESPECIFIED_SEEDS
    from asoflab.simulation.clinical import SCENARIOS

    typer.echo(
        format_table(
            ["scenario", "what is switched on"],
            [[s.name, s.description] for s in SCENARIOS.values()],
        )
    )
    typer.echo(
        f"\nPrespecified seeds: {', '.join(map(str, PRESPECIFIED_SEEDS))}. Full runs use {FULL_PATIENTS} patients."
    )


@experiment_app.command("run")
def experiment_run(
    out: Annotated[
        Path, typer.Option("--out", help="Root directory. The run gets its own subdirectory.")
    ],
    scenario: Annotated[str, typer.Option("--scenario")] = "combined",
    seed: Annotated[int, typer.Option("--seed")] = 20261,
    patients: Annotated[int, typer.Option("--patients")] = 3000,
    replicates: Annotated[
        int, typer.Option("--replicates", help="Bootstrap resamples of patients.")
    ] = 2000,
) -> None:
    """Simulate one cohort, compute features under every policy, evaluate, audit, and write a report."""
    from asoflab.audit.report import write_run_report
    from asoflab.evaluation.bootstrap import BootstrapSettings
    from asoflab.evaluation.experiment import AnalysisSettings, run_experiment
    from asoflab.evaluation.protocol import simulation_config

    settings = AnalysisSettings(bootstrap=BootstrapSettings(replicates=replicates))
    try:
        directory = run_experiment(simulation_config(scenario, seed, patients), settings, out)
    except FileExistsError as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from error
    report = write_run_report(directory)
    typer.secho("SYNTHETIC DATA", fg=typer.colors.YELLOW, bold=True)
    typer.echo(f"Run written to {directory}")
    typer.echo(f"Report: {report}")


@experiment_app.command("matrix")
def experiment_matrix(
    out: Annotated[Path, typer.Option("--out", help="Root directory for all runs.")],
    patients: Annotated[int, typer.Option("--patients")] = 3000,
    workers: Annotated[int, typer.Option("--workers", help="Runs to execute at once.")] = 1,
) -> None:
    """Run every prespecified scenario with every prespecified seed, then summarise across seeds."""
    from asoflab.audit.manifest import write_json
    from asoflab.evaluation.protocol import run_matrix
    from asoflab.evaluation.summary import summarise

    for name, outcome in run_matrix(out, n_patients=patients, workers=workers):
        typer.echo(f"{name}: {outcome}")
    target = write_json(out / "summary.json", summarise(out))
    typer.echo(f"Cross-seed summary: {target}")


@experiment_app.command("summarise")
def experiment_summarise(
    root: Annotated[Path, typer.Argument(help="Directory holding the runs.")],
) -> None:
    """Rebuild the cross-seed summary from the runs found under a directory."""
    from asoflab.audit.manifest import write_json
    from asoflab.evaluation.summary import summarise

    typer.echo(f"Wrote {write_json(root / 'summary.json', summarise(root))}")


@app.command()
def report(
    run: Annotated[Path, typer.Argument(help="A run directory containing manifest.json.")],
) -> None:
    """Rebuild the self-contained HTML report of a run from its saved outputs."""
    from asoflab.audit.report import write_run_report

    typer.echo(f"Wrote {write_run_report(run)}")


@app.command()
def demo(
    out: Annotated[Path, typer.Option("--out", help="Root directory for the demo run.")] = Path(
        "runs/demo"
    ),
    reference: Annotated[
        Path, typer.Option("--reference", help="Committed reference results to compare with.")
    ] = Path("results/demo"),
    strict: Annotated[
        bool,
        typer.Option(
            "--strict", help="Exit with an error if the output differs from the reference."
        ),
    ] = False,
) -> None:
    """The small end-to-end demonstration: fixtures, one simulated cohort, statistics, audit, report."""
    from asoflab.audit.fixture_check import check_fixtures
    from asoflab.audit.report import write_run_report
    from asoflab.evaluation.experiment import run_experiment
    from asoflab.evaluation.protocol import (
        DEMO_PATIENTS,
        DEMO_SCENARIO,
        DEMO_SEED,
        DEMO_SETTINGS,
        simulation_config,
    )

    typer.secho("1. Handwritten fixtures through all three engines", bold=True)
    results = check_fixtures(["oracle", "batch", "replay"])
    failed = [result for result in results if not result.passed]
    typer.echo(
        f"   {len(results) - len(failed)} of {len(results)} checks match the hand-calculated answers."
    )
    if failed:
        for result in failed[:5]:
            typer.echo(f"   FAIL {result.fixture} {result.policy} {result.engine}: {result.detail}")
        raise typer.Exit(code=1)

    typer.secho(
        f"2. Simulated cohort: scenario {DEMO_SCENARIO}, seed {DEMO_SEED}, {DEMO_PATIENTS} patients",
        bold=True,
    )
    typer.secho(
        "   SYNTHETIC DATA. Not a physiological model and not a clinical risk tool.",
        fg=typer.colors.YELLOW,
    )
    config = simulation_config(DEMO_SCENARIO, DEMO_SEED, DEMO_PATIENTS)
    try:
        directory = run_experiment(config, DEMO_SETTINGS, out)
        write_run_report(directory)
    except FileExistsError:
        existing = sorted(path.parent for path in out.glob("*/manifest.json"))
        directory = existing[-1]
        typer.echo(f"   A completed demo run already exists and was left untouched: {directory}")
    statistics = json.loads((directory / "statistics.json").read_text())
    audit = json.loads((directory / "audit.json").read_text())
    manifest = json.loads((directory / "manifest.json").read_text())

    typer.secho("3. Results", bold=True)
    differential = audit["differential"]
    gap = statistics["experiment_a"]["prediction_weighted"]["differences"][
        "naive_event_time_minus_pipeline_as_known"
    ]["auroc"]
    sample = statistics["experiment_a"]["sample"]
    typer.echo(
        f"   Naive policy read something unavailable in {differential['predictions_with_findings']:,} of "
        f"{differential['predictions']:,} predictions."
    )
    typer.echo(
        f"   Same model, naive minus as-known AUROC: {gap['point']:+.4f} "
        f"(95% patient-bootstrap interval {gap['lower']:+.4f} to {gap['upper']:+.4f}; "
        f"{sample['patients']} test patients, {sample['events']} events)."
    )
    parity = [item["equivalent"] for group in audit["parity"].values() for item in group.values()]
    typer.echo(
        f"   Engine agreement on this cohort: {sum(parity)} of {len(parity)} comparisons agree."
    )

    typer.secho("4. Reproducibility", bold=True)
    reference_manifest = sorted(reference.glob("*/manifest.json"))
    if not reference_manifest:
        typer.echo(f"   No reference results under {reference}; nothing to compare with.")
    else:
        expected = json.loads(reference_manifest[-1].read_text())
        same_data = expected["data_hashes"] == manifest["data_hashes"]
        same_features = expected["time_policies"] == manifest["time_policies"]
        typer.echo(
            f"   Simulated data identical to the committed reference: {'yes' if same_data else 'NO'}"
        )
        typer.echo(
            f"   Feature tables identical to the committed reference: {'yes' if same_features else 'NO'}"
        )
        if not (same_data and same_features):
            typer.echo(
                "   A difference can come from another platform or library version. See docs/reproducibility.md."
            )
            if strict:
                raise typer.Exit(code=3)
    typer.echo(f"\nReport: {directory / 'report.html'}")
    typer.echo(f"Inspect it interactively with: asoflab serve --runs {out}")


@app.command()
def serve(
    runs: Annotated[Path, typer.Option("--runs", help="Directory holding run directories.")] = Path(
        "runs/demo"
    ),
    results: Annotated[
        Path, typer.Option("--results", help="Saved validation and benchmark results.")
    ] = Path("results"),
    web: Annotated[Path, typer.Option("--web", help="Built UI (web/dist).")] = Path("web/dist"),
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option(
            "--port",
            min=0,
            max=65535,
            help="Port to listen on. 0 lets the operating system pick a free one.",
        ),
    ] = 8000,
) -> None:
    """Serve the inspection UI and its read-only API on this machine."""
    try:
        import uvicorn

        from asoflab.api.app import create_app
    except ImportError as error:
        typer.secho(
            "The UI server needs the 'serve' extra: uv sync --extra serve",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2) from error
    if not runs.exists():
        typer.secho(
            f"{runs} does not exist. Run 'asoflab demo' first.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)
    application = create_app(runs, results, web)
    # The port is opened before the address is printed, so the printed address is one
    # this process holds. uvicorn is handed the open socket and binds nothing itself.
    try:
        listener = open_listener(host, port)
    except PortUnavailable as unavailable:
        headline, *advice = _explain_unavailable_port(unavailable)
        typer.secho(headline, fg=typer.colors.RED, err=True)
        for line in advice:
            typer.echo(line, err=True)
        raise typer.Exit(code=2) from unavailable
    bound_port = listener.getsockname()[1]
    typer.echo(f"Serving runs from {runs} at {local_url(host, bound_port)}")
    server = uvicorn.Server(
        uvicorn.Config(application, host=host, port=bound_port, log_level="warning")
    )
    # Ctrl+C ends the server cleanly and is not an error, as with uvicorn.run.
    with contextlib.suppress(KeyboardInterrupt):
        server.run(sockets=[listener])
    if not server.started:
        # The status uvicorn.run itself exits with when the server could not start.
        raise typer.Exit(code=3)


def _explain_unavailable_port(unavailable: PortUnavailable) -> list[str]:
    if not unavailable.in_use:
        return [
            f"Cannot listen on {unavailable.host}, port {unavailable.port}: "
            f"{unavailable.reason}. Nothing was started."
        ]
    lines = [
        f"Port {unavailable.port} on {unavailable.host} is already in use by another program, "
        "so nothing was started."
    ]
    if unavailable.free_port is not None:
        lines.append(
            f"Port {unavailable.free_port} is free: run the same command with "
            f"--port {unavailable.free_port}."
        )
    else:
        lines.append(
            "Run the same command with another --port, or with --port 0 to let the "
            "operating system pick a free one."
        )
    return lines


@replay_app.command("prepare")
def replay_prepare(
    events: EventsOption,
    spine: SpineOption,
    config: ConfigOption,
    out: Annotated[Path, typer.Option("--out", help="Directory for the prepared log.")],
    policy: PolicyOption = "pipeline_as_known",
    partitions: Annotated[int, typer.Option("--partitions", help="Number of key groups.")] = 1,
) -> None:
    """Sort and compact an archive into per-partition replay logs."""
    from asoflab.replay import prepare_log

    try:
        prepared = prepare_log(
            events,
            spine,
            FeatureConfig.from_file(config),
            TimePolicy.parse(policy),
            out,
            n_partitions=partitions,
        )
    except ContractViolation as error:
        _fail(error)
    except FileExistsError as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from error
    rows = sum(int(partition["rows"]) for partition in prepared.partitions)
    typer.echo(f"Prepared {rows} log rows in {prepared.n_partitions} partition(s) at {out}.")


@replay_app.command("run")
def replay_run(
    prepared: Annotated[
        Path, typer.Option("--prepared", help="Directory written by 'replay prepare'.")
    ],
    config: ConfigOption,
    stores: Annotated[
        Path, typer.Option("--stores", help="Directory for checkpoints and outputs.")
    ],
    workers: Annotated[int, typer.Option("--workers", help="Parallel worker processes.")] = 1,
    chunk_size: Annotated[int, typer.Option("--chunk-size", help="Log rows per commit.")] = 50_000,
) -> None:
    """Replay a prepared log. Running it again after an interruption resumes from the last commit."""
    from asoflab.replay import PreparedLog, run_replay

    result = run_replay(
        PreparedLog.load(prepared),
        stores,
        FeatureConfig.from_file(config),
        workers=workers,
        chunk_size=chunk_size,
    )
    typer.echo(json.dumps(result.to_dict(), indent=2))


@replay_app.command("export")
def replay_export(
    prepared: Annotated[
        Path, typer.Option("--prepared", help="Directory written by 'replay prepare'.")
    ],
    config: ConfigOption,
    stores: Annotated[Path, typer.Option("--stores", help="Directory used by 'replay run'.")],
    out: Annotated[Path, typer.Option("--out", help="Where to write the feature Parquet file.")],
) -> None:
    """Write the committed outputs of a finished replay to one Parquet file."""
    from asoflab.replay import PreparedLog, collect_outputs, run_replay

    if out.exists():
        typer.secho(
            f"{out} already exists. Runs are never overwritten.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)
    feature_config = FeatureConfig.from_file(config)
    # A finished run returns immediately, so this only confirms completion and resolves the run id.
    result = run_replay(PreparedLog.load(prepared), stores, feature_config)
    table = collect_outputs(result, feature_config)
    write_parquet(table, out)
    typer.echo(f"Wrote {table.num_rows} rows to {out} (run {result.run_id}).")


if __name__ == "__main__":
    app()
