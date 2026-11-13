"""Deterministic summaries; failed encounters remain in all gold denominators."""

from collections import Counter
from collections.abc import Sequence
from typing import Any

from auris.assertions import AssertionKind
from auris.eval import REPLAYABLE_SCORERS, SCORER_VERSION
from auris.eval.critical import CRITICAL_ERRORS, critical_events
from auris.eval.gold import Dataset, Encounter
from auris.eval.matching import RESOURCE_CLASSES, match
from auris.eval.noncritical import NONCRITICAL_ERRORS
from auris.eval.noncritical import findings as noncritical_findings
from auris.eval.predictions import Prediction
from auris.eval.statistics import bootstrap, clopper_pearson_upper

LANES = ("reduced_schema", "evidence", "base_r4", "us_core_profile", "terminology")
SCORE_VIEWS = ("lenient", "assertion_strict", "record_strict")


def prf(tp: int, predicted: int, gold: int) -> dict[str, int | float | bool]:
    return {
        "tp": tp,
        "fp": predicted - tp,
        "fn": gold - tp,
        "gold_n": gold,
        "predicted_n": predicted,
        "precision": tp / predicted if predicted else 0.0,
        "recall": tp / gold if gold else 0.0,
        "f1": 2 * tp / (predicted + gold) if predicted + gold else 0.0,
        "low_power": gold < 25,
    }


def encounter_counts(
    encounter: Encounter,
    predictions: Sequence[Prediction],
    *,
    strict: bool,
    by_kind: bool = False,
    record: bool = False,
) -> dict[str, tuple[int, int, int]]:
    key = (lambda f: str(f.kind)) if by_kind else (lambda f: RESOURCE_CLASSES[f.kind])
    pairs = match(
        predictions, encounter.assertions, strict=strict, same_kind=by_kind, record=record
    )
    matched = Counter(key(predictions[i]) for i, _ in pairs)
    proposed, expected = Counter(map(key, predictions)), Counter(map(key, encounter.assertions))
    classes = (
        [k.value for k in AssertionKind] if by_kind else sorted(set(RESOURCE_CLASSES.values()))
    )
    return {name: (matched[name], proposed[name], expected[name]) for name in classes}


def sum_counts(rows: Sequence[dict[str, tuple[int, int, int]]]) -> dict[str, Any]:
    classes = rows[0].keys()
    per_class = {
        name: prf(*(sum(row[name][i] for row in rows) for i in range(3))) for name in classes
    }
    micro = prf(*(sum(row[name][i] for row in rows for name in classes) for i in range(3)))
    active = [m for m in per_class.values() if m["gold_n"] or m["predicted_n"]]
    macro = {
        metric: sum(float(m[metric]) for m in active) / len(active) if active else 0.0
        for metric in ("precision", "recall", "f1")
    }
    return {
        "per_class": per_class,
        "micro": micro,
        "macro": macro,
        "macro_definition": "unweighted mean over classes with gold or predictions",
    }


def score_model(
    dataset: Dataset,
    predictions: dict[str, tuple[Prediction, ...]],
    *,
    seed: int,
    samples: int = 2000,
    metadata: dict | None = None,
    stratify: bool = True,
    scorer_version: str = SCORER_VERSION,
) -> dict:
    if scorer_version not in REPLAYABLE_SCORERS:
        raise ValueError("unsupported scorer version")
    if set(predictions) != {e.id for e in dataset.encounters}:
        raise ValueError("frozen predictions must include every encounter exactly once")
    result: dict[str, Any] = {}
    for mode in SCORE_VIEWS:
        strict, record = mode != "lenient", mode == "record_strict"
        rows = [
            encounter_counts(e, predictions[e.id], strict=strict, record=record)
            for e in dataset.encounters
        ]
        summary = sum_counts(rows)
        # Cluster sampling resamples whole encounters, preserving within-visit dependence.
        summary["micro_ci"] = {
            metric: bootstrap(
                rows,
                lambda sample, m=metric: sum_counts(sample)["micro"][m],
                seed=seed,
                samples=samples,
            )
            for metric in ("precision", "recall", "f1")
        }
        summary["macro_ci"] = {
            metric: bootstrap(
                rows,
                lambda sample, m=metric: sum_counts(sample)["macro"][m],
                seed=seed,
                samples=samples,
            )
            for metric in ("precision", "recall", "f1")
        }
        summary["per_class_f1_ci"] = {
            name: bootstrap(
                rows,
                lambda sample, key=name: prf(
                    *(sum(row[key][i] for row in sample) for i in range(3))
                )["f1"],
                seed=seed,
                samples=samples,
            )
            for name in summary["per_class"]
        }
        summary["by_kind"] = sum_counts(
            [
                encounter_counts(e, predictions[e.id], strict=strict, by_kind=True, record=record)
                for e in dataset.encounters
            ]
        )["per_class"]
        summary["encounter_counts"] = {
            e.id: row for e, row in zip(dataset.encounters, rows, strict=True)
        }
        result[mode] = summary
    all_predictions = [p for e in dataset.encounters for p in predictions[e.id]]
    n = len(dataset.encounters)
    gold_n = sum(len(e.assertions) for e in dataset.encounters)
    out_n = sum(len(e.out_of_scope) for e in dataset.encounters)
    captured_n = sum(
        a.kind in dataset.declared_kinds for e in dataset.encounters for a in e.assertions
    )
    result["coverage"] = {
        "declared_kinds": sorted(dataset.declared_kinds),
        "gold_in_declared_scope": captured_n,
        "gold_assertions": gold_n,
        "out_of_scope_important_facts": out_n,
        "declared_fact_coverage": captured_n / (gold_n + out_n) if gold_n + out_n else None,
        "definition": "declared-scope gold / (all gold + important out-of-scope facts)",
    }
    result["lanes"] = {}
    for lane in LANES:
        counts = Counter(str(getattr(p.lanes, lane)) for p in all_predictions)
        result["lanes"][lane] = {state: counts[state] for state in ("PASS", "FAIL", "NOT_CHECKED")}
        checked = counts["PASS"] + counts["FAIL"]
        result["lanes"][lane].update(
            {
                "pass_rate_all": counts["PASS"] / len(all_predictions) if all_predictions else None,
                "pass_rate_checked": counts["PASS"] / checked if checked else None,
            }
        )
    review = {
        e.id: sum(p.export_state == "needs_review" for p in predictions[e.id])
        for e in dataset.encounters
    }
    result["review_burden"] = {
        "needs_review_items": sum(review.values()),
        "per_encounter": sum(review.values()) / n,
        "encounters": review,
        "definition": "needs_review state, not measured correction time",
    }
    result["export_states"] = dict(sorted(Counter(p.export_state for p in all_predictions).items()))
    events = {
        e.id: critical_events(predictions[e.id], e.assertions, scorer_version=scorer_version)
        for e in dataset.encounters
    }
    result["critical_errors"] = {}
    for stage in ("pre_review", "exported_without_review"):
        stage_report = {}
        for name in CRITICAL_ERRORS:
            counts = [len(events[e.id][stage][name]) for e in dataset.encounters]
            affected = sum(bool(c) for c in counts)
            stage_report[name] = {
                "count": sum(counts),
                "affected_encounters": affected,
                "encounter_n": n,
                "encounter_rate": affected / n,
                "upper_95": clopper_pearson_upper(affected, n),
                "bound": "one-sided exact Clopper-Pearson, encounter incidence",
                "rule_of_three_when_zero": min(1.0, 3 / n) if affected == 0 else None,
                "low_power": n < 25,
            }
        result["critical_errors"][stage] = stage_report
    result["critical_events"] = events
    if scorer_version == "3":
        noncritical = {
            e.id: {
                name: [
                    p.id for p in predictions[e.id] if name in noncritical_findings(p, e.assertions)
                ]
                for name in NONCRITICAL_ERRORS
            }
            for e in dataset.encounters
        }
        result["noncritical_events"] = noncritical
        result["noncritical_errors"] = {
            name: {
                "count": sum(len(row[name]) for row in noncritical.values()),
                "affected_encounters": sum(bool(row[name]) for row in noncritical.values()),
                "encounter_n": n,
                "definition": "pre-review assertions; at most once per assertion per category",
            }
            for name in NONCRITICAL_ERRORS
        }
    result["encounter_n"] = n
    result["low_power"] = n < 25
    result["metadata"] = metadata or {}
    if stratify:
        sources = sorted({e.source for e in dataset.encounters})
        result["sources"] = sources
        result["by_source"] = {}
        if len(sources) > 1:
            for source in sources:
                encounters = tuple(e for e in dataset.encounters if e.source == source)
                subset = dataset.model_copy(update={"encounters": encounters})
                result["by_source"][source] = score_model(
                    subset,
                    {e.id: predictions[e.id] for e in encounters},
                    seed=seed,
                    samples=samples,
                    stratify=False,
                    scorer_version=scorer_version,
                )
    return result
