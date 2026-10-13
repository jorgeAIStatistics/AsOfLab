"""Self-contained HTML report for one run. Every number is read from the run's saved files.

The page has no scripts and no external resources: it opens from disk, offline.
Charts are inline SVG and each one sits next to a table of the same values.
"""

import json
from collections.abc import Sequence
from html import escape
from pathlib import Path
from typing import Any

from asoflab.audit.svg import ForestRow, Series, forest_plot, line_chart

REPORT_NAME = "report.html"

CSS = """
:root {
  color-scheme: light dark;
  --surface: #fcfcfb; --panel: #f4f4f1; --border: #dddcd6; --grid: #e7e6e1;
  --text: #0b0b0b; --text-2: #52514e; --text-muted: #6f6e68;
  --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a;
  --accent: #1f62b5; --warning-bg: #fff3d1; --warning-ink: #5c4000; --warning-edge: #e0a100;
  --critical-bg: #fde8e6; --critical-ink: #8a1f1f;
}
@media (prefers-color-scheme: dark) {
  :root {
    --surface: #1a1a19; --panel: #232322; --border: #3a3a37; --grid: #2f2f2d;
    --text: #ffffff; --text-2: #c3c2b7; --text-muted: #9b9a90;
    --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;
    --accent: #7db3f2; --warning-bg: #3a2f10; --warning-ink: #ffd978; --warning-edge: #c98500;
    --critical-bg: #3b1a1a; --critical-ink: #ffb3ad;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface); color: var(--text);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 1040px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 24px; line-height: 1.25; margin: 8px 0 4px; text-wrap: balance; }
h2 { font-size: 18px; margin: 40px 0 8px; padding-top: 16px; border-top: 1px solid var(--border); }
h3 { font-size: 15px; margin: 24px 0 6px; }
p { margin: 8px 0; max-width: 74ch; }
.lede { color: var(--text-2); }
.muted, figcaption, .empty { color: var(--text-muted); font-size: 13px; }
code { font: 13px ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--panel);
  padding: 1px 5px; border-radius: 4px; overflow-wrap: anywhere; }
pre { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 12px;
  overflow-x: auto; font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
.badge { display: inline-block; font-size: 12px; font-weight: 600; letter-spacing: 0.04em;
  text-transform: uppercase; padding: 3px 9px; border-radius: 4px; border: 1px solid var(--warning-edge);
  background: var(--warning-bg); color: var(--warning-ink); }
.tag { display: inline-block; font-size: 12px; padding: 1px 7px; border-radius: 4px;
  border: 1px solid var(--border); color: var(--text-2); white-space: nowrap; }
.tag.warn { border-color: var(--warning-edge); background: var(--warning-bg); color: var(--warning-ink); }
.tag.high { background: var(--critical-bg); color: var(--critical-ink); border-color: transparent; }
.note { border-left: 3px solid var(--warning-edge); background: var(--warning-bg); color: var(--warning-ink);
  padding: 10px 14px; border-radius: 0 6px 6px 0; margin: 12px 0; max-width: 78ch; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; margin: 16px 0; }
.tile { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; }
.tile .label { color: var(--text-2); font-size: 13px; }
.tile .value { font-size: 26px; font-weight: 600; line-height: 1.2; margin: 4px 0; }
.tile .context { color: var(--text-muted); font-size: 13px; }
.scroll { overflow-x: auto; margin: 10px 0; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--text-2); font-weight: 600; white-space: nowrap; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tr.reference td { background: var(--panel); }
.charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin: 16px 0; }
figure { margin: 0; }
ul { padding-left: 20px; max-width: 78ch; }
li { margin: 4px 0; }
details { margin: 10px 0; }
summary { cursor: pointer; color: var(--accent); }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
@media print { h2 { break-after: avoid; } .tile { break-inside: avoid; } }
"""

POLICY_LABELS = {
    "pipeline_as_known": "As known to the pipeline",
    "source_as_known": "As known in the source system",
    "latest_revised": "Latest revised",
    "naive_event_time": "Naive (event time only)",
}
METRIC_LABELS = {
    "auroc": "AUROC",
    "average_precision": "Average precision",
    "brier": "Brier score",
    "calibration_intercept": "Calibration intercept",
    "calibration_slope": "Calibration slope",
}


def number(value: float | None, digits: int = 3, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    if round(value, digits) == 0:
        return f"{0:.{digits}f}"  # never print a signed zero
    return f"{value:+.{digits}f}" if signed else f"{value:.{digits}f}"


def estimate(interval: dict[str, Any], digits: int = 3, signed: bool = False) -> str:
    """'0.712 (0.688 to 0.735)', or the reason there is no estimate."""
    if interval.get("point") is None:
        return f"not estimable: {interval.get('reason') or 'no reason recorded'}"
    text = number(interval["point"], digits, signed)
    if interval.get("lower") is None:
        return f"{text} (no interval: {interval.get('reason') or 'not estimable'})"
    return f"{text} ({number(interval['lower'], digits, signed)} to {number(interval['upper'], digits, signed)})"


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]], numeric: Sequence[int] = (), highlight: int | None = None) -> str:  # fmt: skip
    head = "".join(
        f'<th class="num">{escape(h)}</th>' if index in numeric else f"<th>{escape(h)}</th>"
        for index, h in enumerate(headers)
    )
    body = []
    for row_index, row in enumerate(rows):
        cells = "".join(
            f'<td class="num">{cell}</td>' if index in numeric else f"<td>{cell}</td>"
            for index, cell in enumerate(row)
        )
        css = ' class="reference"' if row_index == highlight else ""
        body.append(f"<tr{css}>{cells}</tr>")
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def tile(label: str, value: str, context: str) -> str:
    return (
        f'<div class="tile"><div class="label">{escape(label)}</div>'
        f'<div class="value">{escape(value)}</div><div class="context">{escape(context)}</div></div>'
    )


def sample_text(sample: dict[str, Any]) -> str:
    return (
        f"Simulated data. {sample['predictions']:,} predictions from {sample['patients']:,} patients, "
        f"{sample['events']:,} with the event."
    )


def write_run_report(directory: str | Path) -> Path:
    directory = Path(directory)
    target = directory / REPORT_NAME
    target.write_text(render_run_report(directory))
    return target


def render_run_report(directory: str | Path) -> str:
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    statistics = json.loads((directory / "statistics.json").read_text())
    audit = json.loads((directory / "audit.json").read_text())
    sections = [
        _header(manifest, statistics),
        _headline(statistics, audit),
        _question(statistics),
        _cohort(statistics),
        _audit(audit),
        _experiment_a(statistics),
        _experiment_b(statistics),
        _calibration(statistics),
        _chronological(statistics),
        _subgroups(statistics),
        _label_maturity(statistics),
        _engineering(audit),
        _limits(statistics),
        _provenance(manifest),
    ]
    title = f"AsOfLab run report: {manifest['run_id']}"
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{CSS}</style></head><body><main>"
        + "".join(sections)
        + "</main></body></html>"
    )


def _header(manifest: dict[str, Any], statistics: dict[str, Any]) -> str:
    scenario = statistics["scenario"]
    return (
        '<span class="badge">Synthetic data</span>'
        f"<h1>What could the pipeline know, and did it matter? Scenario: {escape(scenario['name'])}</h1>"
        f'<p class="lede">{escape(scenario["description"])}</p>'
        f'<p class="muted">Run <code>{escape(manifest["run_id"])}</code> · seed {statistics["seed"]} · '
        f"created {escape(manifest['created_at'])} · temporal contract "
        f"{escape(manifest['identity']['contract_version'])}</p>"
        '<p class="note">Everything on this page comes from a computer simulation. It is not a '
        "validated physiological model, and no number here is evidence about a real patient, hospital, "
        "or model.</p>"
    )


def _headline(statistics: dict[str, Any], audit: dict[str, Any]) -> str:
    differential = audit["differential"]
    share = differential["predictions_with_findings"] / max(differential["predictions"], 1)
    gap = statistics["experiment_a"]["prediction_weighted"]["differences"][
        "naive_event_time_minus_pipeline_as_known"
    ]["auroc"]
    interval = (
        f"95% interval {number(gap['lower'], 4, True)} to {number(gap['upper'], 4, True)}"
        if gap.get("lower") is not None
        else (gap.get("reason") or "no interval")
    )
    maturity = statistics["label_maturity"]
    hasty = maturity["evaluation_against_impatient_labels"]
    tiles = [
        tile(
            "Predictions where the naive policy read something the pipeline could not have",
            f"{share:.1%}",
            f"{differential['predictions_with_findings']:,} of {differential['predictions']:,} predictions",
        ),
        tile(
            "Change in AUROC when the same model is scored on naive features",
            number(gap["point"], 4, True),
            interval + ". Naive minus as-known.",
        ),
        tile(
            "Events an impatient pipeline would have labelled negative",
            f"{maturity['premature_negatives_if_labelled_at_the_horizon']:,}",
            f"of {statistics['label_coverage']['by_status']['positive']:,} positive labels; test-set prevalence "
            f"{hasty['prevalence_with_labels_recorded_at_horizon']:.3f} against {hasty['prevalence_with_mature_labels']:.3f}",
        ),
    ]
    return '<h2>Key results</h2><div class="tiles">' + "".join(tiles) + "</div>"


def _question(statistics: dict[str, Any]) -> str:
    target = statistics["target"]
    rows = [
        ["Target", escape(target["definition"])],
        ["Predictions", escape(target["prediction_schedule"])],
        ["Eligible population", escape(target["eligible_population"])],
        ["Unit of analysis", escape(target["unit_of_analysis"])],
        ["Estimand", escape(target["estimand"])],
        ["Average precision", escape(target["average_precision_convention"])],
        ["Weighting", escape(statistics["weighting"])],
    ]
    return "<h2>What was estimated</h2>" + table(["Item", "Definition"], rows)


def _cohort(statistics: dict[str, Any]) -> str:
    cohort, coverage = statistics["cohort"], statistics["label_coverage"]
    counts = statistics["splits"]["patient_holdout"]["counts"]
    rows = [
        [escape(key.replace("_", " ").capitalize()), f"{value:,}"] for key, value in cohort.items()
    ]
    status = [
        [escape(key.replace("_", " ")), f"{value:,}"]
        for key, value in coverage["by_status"].items()
    ]
    splits = [
        [escape(part), f"{entry['patients']:,}", f"{entry['predictions']:,}"]
        for part, entry in counts.items()
    ]
    return (
        "<h2>Cohort, labels and splits</h2>"
        + table(["Quantity", "Count"], rows, numeric=[1])
        + f"<h3>Label coverage</h3><p>{coverage['labelled']:,} of {coverage['predictions']:,} predictions have a known "
        f"label ({coverage['coverage']:.1%}). Unknown labels are excluded from metrics and are never treated as negatives.</p>"
        + table(["Label status", "Predictions"], status, numeric=[1])
        + "<h3>Patient-level split</h3><p>Patients, not rows, were assigned to partitions, so no patient "
        "contributes to more than one.</p>"
        + table(["Partition", "Patients", "Predictions"], splits, numeric=[1, 2])
    )


def _audit(audit: dict[str, Any]) -> str:
    differential = audit["differential"]
    rows = [
        [
            f'<span class="tag {"high" if entry["severity"] == "high" else "warn"}">{escape(entry["severity"])}</span>',
            f"<code>{escape(name)}</code>",
            f"{entry['count']:,}",
            escape(entry["meaning"]),
            escape(entry["remediation"]),
        ]
        for name, entry in differential["by_type"].items()
    ]
    changed = audit["feature_differences_from_reference"]
    change_rows = [
        [
            escape(POLICY_LABELS[slug]),
            f"{entry['predictions_with_any_feature_changed']:,}",
            f"{entry['share_of_predictions']:.1%}",
        ]
        for slug, entry in changed.items()
    ]
    examples = [
        [
            f"<code>{escape(example['prediction_id'])}</code>",
            f"<code>{escape(example['logical_event_id'])}</code>",
            f"<code>{escape(example['type'])}</code>",
            escape(_eligibility(example["expected"], "eligible")),
            escape(_eligibility(example["actual"], "used")),
        ]
        for example in differential["examples"][:8]
    ]
    warnings = [
        [
            f"<code>{escape(finding['code'])}</code>",
            f"{finding['count']:,}",
            escape(finding["message"]),
        ]
        for finding in audit["validation"]["findings"]
    ]
    html = (
        "<h2>Audit: where the naive policy departs from what was known</h2>"
        f"<p>{escape(differential['scope'])}</p>"
        f"<p>Reference: <code>{escape(differential['reference_policy'])}</code>. Candidate: "
        f"<code>{escape(differential['candidate_policy'])}</code>. {differential['findings_total']:,} findings across "
        f"{differential['predictions_with_findings']:,} of {differential['predictions']:,} predictions.</p>"
        + table(["Severity", "Finding", "Count", "Meaning", "What to change"], rows, numeric=[2])
        + "<h3>How many predictions have different features</h3>"
        + table(
            ["Policy compared with the reference", "Predictions with a changed feature", "Share"],
            change_rows,
            numeric=[1, 2],
        )
    )
    if examples:
        html += (
            f"<h3>Examples ({len(examples)} of {differential['findings_total']:,})</h3>"
            + table(
                [
                    "Prediction",
                    "Observation",
                    "Finding",
                    "Expected (as known)",
                    "Actual (candidate)",
                ],
                examples,
            )
        )
    html += "<h3>Contract validation</h3>" + (
        table(["Code", "Count", "Meaning"], warnings, numeric=[1])
        if warnings
        else "<p>No contract errors or warnings for this dataset.</p>"
    )
    return html


def _eligibility(side: dict[str, Any], key: str) -> str:
    state = "yes" if side.get(key) else "no"
    detail = []
    if side.get("source_revision") is not None:
        detail.append(f"revision {side['source_revision']}")
    if side.get("value") is not None:
        detail.append(f"value {side['value']:g}")
    if side.get("first_available"):
        detail.append(f"first available {side['first_available'][:16].replace('T', ' ')}Z")
    return f"{key}: {state}" + (f" ({', '.join(detail)})" if detail else "")


def _metric_rows(
    conditions: dict[str, Any], labels: dict[str, str], metrics: Sequence[str]
) -> list[list[str]]:
    digits = {"brier": 4}
    return [
        [
            escape(labels.get(name, name)),
            *(escape(estimate(entry[metric], digits.get(metric, 3))) for metric in metrics),
        ]
        for name, entry in conditions.items()
    ]


def _difference_rows(differences: dict[str, Any], names: dict[str, str]) -> list[list[str]]:
    return [
        [
            escape(names.get(name, name.replace("_", " "))),
            *(
                escape(estimate(entry[metric], 5 if metric == "brier" else 4, signed=True))
                for metric in ("auroc", "average_precision", "brier")
            ),
        ]
        for name, entry in differences.items()
    ]


def _experiment_a(statistics: dict[str, Any]) -> str:
    a = statistics["experiment_a"]
    block = a["prediction_weighted"]
    metrics = ["auroc", "average_precision", "brier"]
    caption = sample_text(a["sample"])
    charts = []
    comparisons = [
        ("source_as_known_minus_pipeline_as_known", "Source system"),
        ("latest_revised_minus_pipeline_as_known", "Latest revised"),
        ("naive_event_time_minus_pipeline_as_known", "Naive"),
    ]
    for metric in metrics:
        rows = [
            ForestRow(
                label,
                block["differences"][key][metric]["point"],
                block["differences"][key][metric]["lower"],
                block["differences"][key][metric]["upper"],
                block["differences"][key][metric].get("reason") or "",
            )
            for key, label in comparisons
        ]
        charts.append(
            "<figure>"
            + forest_plot(
                rows,
                title=f"{METRIC_LABELS[metric]}: policy minus as-known",
                axis_label="difference",
                width=420,
            )
            + f"<figcaption>{escape(caption)} Dot: estimate. Bar: 95% patient-bootstrap interval. "
            "Zero means no change.</figcaption></figure>"
        )
    names = {
        "source_as_known_minus_pipeline_as_known": "Source system minus pipeline",
        "latest_revised_minus_pipeline_as_known": "Latest revised minus as-known",
        "naive_event_time_minus_pipeline_as_known": "Naive minus as-known",
        "revision_effect": "Revision effect (latest revised minus as-known)",
        "availability_effect": "Availability effect (naive minus latest revised)",
    }
    flags = a["instability_flags"]
    return (
        "<h2>Experiment A: one frozen model, four ways of building its inputs</h2>"
        f"<p>{escape(a['question'])}</p><p><strong>How to read it.</strong> {escape(a['interpretation'])}</p>"
        f'<p class="muted">{escape(caption)}'
        + (f" Caution: {escape('; '.join(flags))}." if flags else "")
        + "</p>"
        + table(
            ["Features built under", *(METRIC_LABELS[m] for m in metrics)],
            _metric_rows(block["conditions"], POLICY_LABELS, metrics),
            numeric=[1, 2, 3],
            highlight=0,
        )
        + "<p class='muted'>Values are estimates with 95% patient-bootstrap intervals. The shaded row is the reference.</p>"
        + f'<div class="charts">{"".join(charts)}</div>'
        + "<h3>Paired differences</h3>"
        + table(
            ["Comparison", "AUROC", "Average precision", "Brier score"],
            _difference_rows(block["differences"], names),
            numeric=[1, 2, 3],
        )
        + f"<p class='muted'>{escape(a['decomposition_note'])}</p>"
        + "<h3>Patient-weighted view</h3><p>The same comparison with every patient given equal total weight, "
        "so patients with many predictions do not dominate.</p>"
        + table(
            ["Comparison", "AUROC", "Average precision", "Brier score"],
            _difference_rows(a["patient_weighted"]["differences"], names),
            numeric=[1, 2, 3],
        )
    )


def _experiment_b(statistics: dict[str, Any]) -> str:
    b = statistics["experiment_b"]
    block = b["prediction_weighted"]
    cells = {
        "naive_trained__naive_evaluated": "Trained naive, evaluated naive (what a naive pipeline reports)",
        "naive_trained__as_known_evaluated": "Trained naive, evaluated as known (what it would deliver)",
        "as_known_trained__as_known_evaluated": "Trained as known, evaluated as known",
        "as_known_trained__naive_evaluated": "Trained as known, evaluated naive",
    }
    contrasts = {
        "optimism_of_the_naive_pipeline": "Reported minus delivered, naive pipeline",
        "as_known_training_minus_naive_training_at_deployment": "As-known training minus naive training, both delivered",
        "reported_naive_minus_reported_as_known": "Naive pipeline's report minus as-known pipeline's report",
    }
    metrics = ["auroc", "average_precision", "brier"]
    return (
        "<h2>Experiment B: two training pipelines</h2>"
        f"<p>{escape(b['question'])}</p><p>{escape(b['design'])}</p>"
        + table(
            ["Model and evaluation", *(METRIC_LABELS[m] for m in metrics)],
            _metric_rows(block["conditions"], cells, metrics),
            numeric=[1, 2, 3],
        )
        + "<h3>Contrasts</h3>"
        + table(
            ["Contrast", "AUROC", "Average precision", "Brier score"],
            _difference_rows(block["differences"], contrasts),
            numeric=[1, 2, 3],
        )
        + f"<p><strong>What this can attribute.</strong> {escape(b['can_attribute'])}</p>"
        f"<p><strong>What it cannot.</strong> {escape(b['cannot_attribute'])}</p>"
    )


def _calibration(statistics: dict[str, Any]) -> str:
    a = statistics["experiment_a"]
    curves = a["calibration_curves"]
    series = [
        Series(
            POLICY_LABELS[slug],
            [(point["mean_predicted"], point["observed_rate"]) for point in curves[slug]],
            [
                f"{POLICY_LABELS[slug]}: predicted {point['mean_predicted']:.3f}, observed {point['observed_rate']:.3f} "
                f"({point['events']} events in {point['predictions']} predictions)"
                for point in curves[slug]
            ],
        )
        for slug in ("pipeline_as_known", "naive_event_time")
    ]
    rows = _metric_rows(
        a["prediction_weighted"]["conditions"],
        POLICY_LABELS,
        ["calibration_intercept", "calibration_slope"],
    )
    bins = [
        [
            str(index + 1),
            number(point["mean_predicted"], 3),
            number(point["observed_rate"], 3),
            f"{point['events']:,}",
            f"{point['predictions']:,}",
        ]
        for index, point in enumerate(curves["pipeline_as_known"])
    ]
    return (
        "<h2>Calibration</h2><p>Observed event rate against mean predicted risk in ten equal-count groups. "
        "Points on the diagonal are perfectly calibrated. An intercept of 0 and a slope of 1 indicate no "
        "systematic over- or under-prediction.</p>"
        '<div class="charts"><figure>'
        + line_chart(
            series,
            title="Calibration of the frozen model",
            x_label="mean predicted risk",
            y_label="observed event rate",
            diagonal=True,
        )
        + f"<figcaption>{escape(sample_text(a['sample']))}</figcaption></figure></div>"
        + table(
            ["Features built under", "Calibration intercept", "Calibration slope"],
            rows,
            numeric=[1, 2],
            highlight=0,
        )
        + "<details><summary>Calibration groups, as-known features</summary>"
        + table(
            ["Group", "Mean predicted", "Observed rate", "Events", "Predictions"],
            bins,
            numeric=[0, 1, 2, 3, 4],
        )
        + "</details>"
    )


def _chronological(statistics: dict[str, Any]) -> str:
    c = statistics["chronological"]
    counts = [
        [escape(part.replace("_", " ")), f"{entry['patients']:,}", f"{entry['predictions']:,}"]
        for part, entry in c["split"]["counts"].items()
    ]
    html = (
        "<h2>Chronological evaluation</h2>"
        f"<p>{escape(c['question'])}</p><p>{escape(c['differs_from_patient_holdout'])}</p>"
        f"<p>Cutoff: <code>{escape(c['split']['cutoff'])}</code>.</p><ul>"
        + "".join(f"<li>{escape(rule)}</li>" for rule in c["split"]["rules"])
        + "</ul>"
        + table(["Assignment", "Patients", "Predictions"], counts, numeric=[1, 2])
    )
    if not c.get("estimable"):
        return (
            html
            + f"<p class='note'>Not estimable: {escape(c.get('reason', 'no reason recorded'))}.</p>"
        )
    metrics = ["auroc", "average_precision", "brier"]
    return (
        html
        + f"<p class='muted'>{escape(sample_text(c['sample']))}</p>"
        + table(
            ["Features built under", *(METRIC_LABELS[m] for m in metrics)],
            _metric_rows(c["prediction_weighted"]["conditions"], POLICY_LABELS, metrics),
            numeric=[1, 2, 3],
            highlight=0,
        )
    )


def _subgroups(statistics: dict[str, Any]) -> str:
    s = statistics["subgroups"]
    rows = []
    for attribute, levels in s["groups"].items():
        for level, entry in levels.items():
            sample = entry["sample"]
            flag = (
                f'<span class="tag warn">unstable: {escape("; ".join(entry["instability_flags"]))}</span>'
                if entry["unstable"]
                else '<span class="tag">no flag</span>'
            )
            rows.append(
                [
                    escape(attribute.replace("_", " ")),
                    escape(level.replace("_", " ")),
                    f"{sample['patients']:,}",
                    f"{sample['predictions']:,}",
                    f"{sample['events']:,}",
                    escape(estimate(entry["metrics"]["auroc"])),
                    escape(estimate(entry["metrics"]["brier"], 4)),
                    flag,
                ]
            )
    return (
        "<h2>Subgroups</h2>"
        f"<p>{escape(s['purpose'])} Prespecified groups: {escape(', '.join(s['prespecified']))}. "
        f"Features as known to the pipeline. Intervals use {s['bootstrap_replicates']:,} patient resamples.</p>"
        + table(
            [
                "Attribute",
                "Group",
                "Patients",
                "Predictions",
                "Events",
                "AUROC",
                "Brier score",
                "Stability",
            ],
            rows,
            numeric=[2, 3, 4, 5, 6],
        )
    )


def _label_maturity(statistics: dict[str, Any]) -> str:
    m = statistics["label_maturity"]
    coverage = m["coverage_at_data_cut"]
    status = [
        [escape(key.replace("_", " ")), f"{value:,}"]
        for key, value in coverage["by_status"].items()
    ]
    hasty = m["evaluation_against_impatient_labels"]
    block = hasty["prediction_weighted"]
    labels = {
        "mature_labels": "Mature labels of record",
        "labels_recorded_at_horizon": "Labels recorded when the horizon ended",
    }
    metrics = ["auroc", "average_precision", "brier"]
    return (
        "<h2>Label timing</h2>"
        f"<p>{escape(m['purpose'])}</p>"
        f"<h3>Labels available at a data cut ({escape(m['data_cut'][:10])})</h3>"
        f"<p>Horizon {m['horizon_hours']:g} h, maximum ascertainment lag {m['max_ascertainment_lag_hours']:g} h. "
        f"{coverage['unknown']:,} of {coverage['predictions']:,} predictions had no usable label yet. "
        f"Unknown labels converted to negatives: {m['unknown_labels_set_to_zero']}.</p>"
        + table(["Label status at the data cut", "Predictions"], status, numeric=[1])
        + "<h3>Labelling without waiting for ascertainment</h3>"
        f"<p>{escape(m['premature_negatives_meaning'])} In this cohort: "
        f"<strong>{m['premature_negatives_if_labelled_at_the_horizon']:,}</strong> predictions.</p>"
        f"<p>{escape(hasty['design'])} Test-set positives: {hasty['positives_with_mature_labels']:,} with mature labels, "
        f"{hasty['positives_with_labels_recorded_at_horizon']:,} with labels recorded at the horizon.</p>"
        + table(
            ["Labels used", *(METRIC_LABELS[x] for x in metrics)],
            _metric_rows(block["conditions"], labels, metrics),
            numeric=[1, 2, 3],
            highlight=0,
        )
        + table(
            ["Contrast", "AUROC", "Average precision", "Brier score"],
            _difference_rows(
                block["differences"],
                {"recorded_at_horizon_minus_mature": "Recorded at horizon minus mature"},
            ),
            numeric=[1, 2, 3],
        )
    )


def _engineering(audit: dict[str, Any]) -> str:
    rows = []
    for slug, entry in audit["parity"]["replay_vs_batch"].items():
        rows.append(
            [
                escape(POLICY_LABELS[slug]),
                "replay against batch, all predictions",
                f"{entry['rows_left']:,}",
                "agree" if entry["equivalent"] else f"DISAGREE in {entry['n_mismatches']} cells",
            ]
        )
    for slug, entry in audit["parity"]["oracle_vs_batch_on_a_patient_sample"].items():
        rows.append(
            [
                escape(POLICY_LABELS[slug]),
                f"oracle against batch, first {entry['patients']} patients",
                f"{entry['rows_left']:,}",
                "agree" if entry["equivalent"] else f"DISAGREE in {entry['n_mismatches']} cells",
            ]
        )
    return (
        "<h2>Engine agreement on this run's data</h2><p>Features were computed by the batch backend. The same "
        "data were also run through the incremental replay, and a sample of patients through the reference "
        "oracle. Identifiers, counts and last values must match exactly; means and elapsed times within "
        "a relative tolerance of 1e-9.</p>"
        + (
            table(["Policy", "Comparison", "Predictions compared", "Result"], rows, numeric=[2])
            if rows
            else "<p class='empty'>No parity checks were run for this run.</p>"
        )
    )


def _limits(statistics: dict[str, Any]) -> str:
    bootstrap = statistics["bootstrap"]
    methods = [
        f"Uncertainty: {bootstrap['replicates_requested']:,} bootstrap resamples of patients (seed {bootstrap['seed']}), "
        f"{bootstrap['interval_method']} intervals at the {bootstrap['level']:.0%} level. A patient drawn more than once "
        "contributes all of its predictions that many times. The same resamples are used for every condition, "
        "and each difference is formed within a resample.",
        f"A statistic is reported without an interval when fewer than {bootstrap['min_completed_fraction']:.0%} of the "
        "resamples could be evaluated. The number completed is recorded for every estimate in statistics.json.",
        "Models: logistic regression with median imputation, missing-value indicators and standardisation, all "
        "fitted on training patients; the regularisation strength was chosen on validation patients. Test "
        "patients were used once.",
    ]
    return (
        "<h2>Methods and limits</h2><h3>Methods</h3><ul>"
        + "".join(f"<li>{escape(item)}</li>" for item in methods)
        + "</ul><h3>Limits</h3><ul>"
        + "".join(f"<li>{escape(item)}</li>" for item in statistics["limits"])
        + "</ul>"
    )


def _provenance(manifest: dict[str, Any]) -> str:
    environment = manifest["environment"]
    policies = [
        [
            f"<code>{escape(entry['policy'])}</code>",
            escape(entry["strictness"]),
            "yes" if entry["faithful_replay"] else "no",
            f"<code>{escape(entry['feature_table_hash'][:16])}</code>",
        ]
        for entry in manifest["time_policies"]
    ]
    hashes = [
        [escape(name), f"<code>{escape(value[:24])}</code>"]
        for name, value in manifest["data_hashes"].items()
    ]
    scenario, seed = manifest["scenario"], manifest["seed"]
    patients = manifest["generator"]["config"]["n_patients"]
    dirty = " with uncommitted changes" if environment.get("code_dirty") else ""
    return (
        "<h2>Provenance and reproduction</h2>"
        f"<p>Code revision <code>{escape(str(environment.get('code_commit'))[:12])}</code>{dirty}. "
        f"Python {escape(environment['python'])} on {escape(environment['platform'])}. Generator "
        f"<code>{escape(manifest['generator']['version'])}</code>.</p>"
        f"<pre>uv run asoflab experiment run --scenario {escape(scenario)} --seed {seed} --patients {patients} --out runs</pre>"
        + table(["Time policy", "Kind", "Faithful replay", "Feature table hash"], policies)
        + table(["Table", "Content hash (SHA-256, first 24 characters)"], hashes)
        + f"<p class='muted'>{escape(manifest['note'])}</p>"
    )
