"""Command-line interface. Run `asoflab --help`."""

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from asoflab import CONTRACT_VERSION, __version__
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


if __name__ == "__main__":
    app()
