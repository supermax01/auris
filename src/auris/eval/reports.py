"""Honest, deterministic JSON/Markdown reports and A5 model comparisons."""

import json
from collections import Counter
from pathlib import Path

from auris._storage import write_private_text
from auris.eval import FICTIONAL_BANNER, REPLAYABLE_SCORERS
from auris.eval.critical import CRITICAL_ERRORS
from auris.eval.diagnostics import render_diagnostics
from auris.eval.gold import Dataset, digest
from auris.eval.metrics import SCORE_VIEWS, sum_counts
from auris.eval.noncritical import NONCRITICAL_ERRORS
from auris.eval.runs import load_run, scorer_hash, write_json
from auris.eval.statistics import bootstrap


def selection(dataset: Dataset, scores: dict, default: str, *, seed: int, samples: int) -> dict:
    models = scores["models"]
    counts = Counter(e.source for e in dataset.encounters)
    cohort_eligible = dataset.split == "dev" and counts == {"PriMock57": 17, "ACI-Bench": 23}
    inapplicable = (
        "not applicable: fictional harness-check set"
        if dataset.split == "harness-check"
        else "not applicable: requires the A5 17 PriMock57 + 23 ACI-Bench development cohort"
    )
    if default not in models:
        return {
            "verdict": inapplicable
            if dataset.split == "harness-check"
            else "not applicable: default model absent",
            "comparisons": {},
        }
    baseline = models[default]
    comparisons = {}
    for model, candidate in models.items():
        if model == default:
            continue
        rows = [
            (
                baseline["lenient"]["encounter_counts"][e.id],
                candidate["lenient"]["encounter_counts"][e.id],
            )
            for e in dataset.encounters
        ]
        ci = bootstrap(
            rows,
            lambda sample: (
                sum_counts([r[1] for r in sample])["micro"]["f1"]
                - sum_counts([r[0] for r in sample])["micro"]["f1"]
            ),
            seed=seed,
            samples=samples,
        )
        delta = candidate["lenient"]["micro"]["f1"] - baseline["lenient"]["micro"]["f1"]
        equal_coverage = candidate["coverage"] == baseline["coverage"]
        no_increase = all(
            candidate["critical_errors"][stage][name]["count"]
            <= baseline["critical_errors"][stage][name]["count"]
            for stage in ("pre_review", "exported_without_review")
            for name in CRITICAL_ERRORS
        )
        # Sampled extractor RSS cannot stand in for the PLAN whole-pipeline budget.
        budget = candidate["metadata"].get("whole_pipeline_budget_measurement", {})
        within_budget = (
            budget.get("reference") == "laptop"
            and isinstance(budget.get("peak_bytes"), (int, float))
            and isinstance(budget.get("seconds_per_600_audio_seconds"), (int, float))
            and 0 < budget["peak_bytes"] < 12 * 1024**3
            and 0 < budget["seconds_per_600_audio_seconds"] <= 1200
        )
        eligible = (
            cohort_eligible
            and delta >= 0.03 - 1e-12
            and equal_coverage
            and no_increase
            and within_budget
        )
        comparisons[model] = {
            "delta_micro_lenient_f1": delta,
            "paired_delta_ci": ci,
            "equal_declared_coverage": equal_coverage,
            "no_critical_error_increase": no_increase,
            "within_budget": within_budget,
            "provisional": ci["half_width"] > 0.03,
            "eligible_to_replace_default": eligible,
        }
    provisional = baseline["lenient"]["micro_ci"]["f1"]["half_width"] > 0.03 or any(
        c["provisional"] for c in comparisons.values()
    )
    return {
        "verdict": inapplicable
        if not cohort_eligible
        else (
            "replacement eligible"
            if any(c["eligible_to_replace_default"] for c in comparisons.values())
            else "retain default"
        ),
        "default_label": "provisional" if provisional else "not provisional",
        "primary_metric": "micro lenient F1; strict results reported separately",
        "comparisons": comparisons,
    }


def render_report(report: dict) -> str:
    lines = ["# Auris evaluation", ""]
    if report["fictional_harness_check"]:
        lines += [
            f"**{FICTIONAL_BANNER}**",
            "Same party authored transcripts and gold; no independent clinical annotation.",
            "",
        ]
    lines += [
        "Research software; clinician review mandatory. "
        "Public data may have appeared in model pretraining.",
        "Results are mock-consultation results unless consented clinical provenance "
        "is explicitly supplied.",
        "Low-power means gold N < 25 (encounter N < 25 for safety bounds).",
        "",
        f"Dataset: `{report['dataset_id']}`; split: `{report['split']}`.",
        f"Bootstrap seed: {report['seed']}; replicates: {report['bootstrap_samples']}.",
        f"Scorer: {report['scorer_version']} (`{report['scorer_sha256']}`); "
        f"inference-time scorer: {report['inference_scorer']['version']} "
        f"(`{report['inference_scorer']['sha256']}`).",
        "Assertion-strict measures extracted values/units with scorer-owned normalization. "
        "Record-strict requires a built resource for FHIR classes and reads its vital "
        "quantities (both BP components); ledger classes retain assertion strict criteria. "
        "Both views check evidence, available codes and extracted order dose/route/frequency. "
        "Record-strict is not independent full-FHIR annotation or an export/validation pass rate.",
        f"Selection verdict: **{report['selection']['verdict']}**.",
        "",
    ]
    models = report["models"]
    names = list(models)
    lines += ["| Metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for mode in SCORE_VIEWS:
        for scope in ("micro", "macro"):
            for metric in ("precision", "recall", "f1"):
                cells = []
                for model in models.values():
                    value = model[mode][scope][metric]
                    ci = model[mode][f"{scope}_ci"][metric]
                    cells.append(f"{value:.4f} [{ci['low']:.4f}, {ci['high']:.4f}]")
                label = mode.replace("_", "-")
                lines.append(f"| {label} {scope} {metric} (95% CI) | " + " | ".join(cells) + " |")
    for mode in SCORE_VIEWS:
        for name in next(iter(models.values()))[mode]["per_class"]:
            cells = []
            for model in models.values():
                m = model[mode]["per_class"][name]
                ci = model[mode]["per_class_f1_ci"][name]
                cells.append(
                    f"P={m['precision']:.3f}, R={m['recall']:.3f}, "
                    f"F1={m['f1']:.3f} [{ci['low']:.3f}, {ci['high']:.3f}]; "
                    f"N={m['gold_n']}" + (" LOW POWER" if m["low_power"] else "")
                )
            lines.append(f"| {mode.replace('_', '-')} {name} | " + " | ".join(cells) + " |")
    for stage in ("pre_review", "exported_without_review"):
        for name in CRITICAL_ERRORS:
            cells = []
            for model in models.values():
                m = model["critical_errors"][stage][name]
                cells.append(
                    f"{m['count']} events; {m['affected_encounters']}/{m['encounter_n']} "
                    f"encounters; upper 95%={m['upper_95']:.4f}"
                    + (f"; 3/N≈{m['rule_of_three_when_zero']:.4f}" if m["count"] == 0 else "")
                )
            lines.append(f"| {stage}: {name} | " + " | ".join(cells) + " |")
    if report["scorer_version"] == "3":
        for name in NONCRITICAL_ERRORS:
            cells = [str(model["noncritical_errors"][name]["count"]) for model in models.values()]
            lines.append(f"| pre_review non-critical: {name} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "Safety bounds are one-sided exact Clopper–Pearson bounds on encounter incidence. "
        "The rule of three is an approximation when zero errors are observed; "
        "zero is not proof of safety.",
        "",
    ]
    for name, model in models.items():
        kind_counts = {k: v["gold_n"] for k, v in model["lenient"]["by_kind"].items()}
        lines += [
            f"## {name}",
            "",
            f"Coverage: `{json.dumps(model['coverage'], sort_keys=True)}`.",
            f"Review burden: {model['review_burden']['per_encounter']:.3f} "
            "needs-review items/encounter (not measured correction time).",
            f"Validation lanes: `{json.dumps(model['lanes'], sort_keys=True)}`.",
            f"Per-kind gold N: `{json.dumps(kind_counts, sort_keys=True)}`.",
            f"Runtime and memory metadata: `{json.dumps(model['metadata'], sort_keys=True)}`.",
            "",
        ]
        for source, subset in model.get("by_source", {}).items():
            lines.append(
                f"Source {source}: lenient micro F1={subset['lenient']['micro']['f1']:.4f}; "
                f"assertion-strict micro F1={subset['assertion_strict']['micro']['f1']:.4f}; "
                f"record-strict micro F1={subset['record_strict']['micro']['f1']:.4f}; "
                f"encounters={subset['encounter_n']}. Full source results in JSON."
            )
    lines += [
        f"Selection details: `{json.dumps(report['selection'], sort_keys=True)}`.",
        "Annotation/adjudication, provenance, all counts, match denominators, "
        "and per-encounter safety events are recorded in report.json.",
        "",
    ]
    lines += [
        "## Error analysis",
        "",
        "Grouped by error type within each model. False positives/negatives are shown for "
        "each independently optimized scoring view. Strict-failure pairs refer to the "
        "lenient assignment and may be rematched by a strict assignment. Related candidates "
        "beside unmatched items are diagnostic associations, not accepted matches. "
        "Evidence quotes are reconstructed from verified transcript spans; quoted_evidence "
        "retains the model's original claim even when alignment failed.",
        "",
    ]
    for name, model in models.items():
        lines += [f"### {name}", ""]
        lines += render_diagnostics(model["error_analysis"]) or ["No item-level errors.", ""]
    return "\n".join(lines)


def compare_run(output: Path) -> dict:
    document, dataset, _ = load_run(output)
    raw = (output / "scores.json").read_bytes()
    receipt = json.loads((output / "score-receipt.json").read_text("utf-8"))
    if receipt != {
        "scores_sha256": digest(raw),
        "run_sha256": digest((output / "run.json").read_bytes()),
    }:
        raise ValueError("score receipt mismatch; score frozen predictions first")
    report = json.loads(raw)
    if (
        report.get("scorer_version") not in REPLAYABLE_SCORERS
        or report.get("scorer_sha256") != scorer_hash()
    ):
        raise ValueError("scores use a different scorer; run auris eval score before compare")
    report["selection"] = selection(
        dataset,
        report,
        document["default_model"],
        seed=document["seed"],
        samples=document["bootstrap_samples"],
    )
    write_json(output / "report.json", report)
    write_private_text(output / "report.md", render_report(report))
    return report
