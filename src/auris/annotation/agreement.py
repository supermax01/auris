"""Pre-adjudication agreement, explicit denominators and conservative freeze gates."""

from collections import Counter
from collections.abc import Sequence
from typing import Any

from auris.annotation.comparison import align, validate_pair
from auris.annotation.models import AnnotationDocument, AnnotationFact, Assignment
from auris.assertions import AssertionKind
from auris.eval.gold import canonical_json
from auris.eval.statistics import bootstrap

# The issued guideline's freeze list. Other kappas are published, not silently
# added to that list. Changing the list requires a new versioned guideline.
REQUIRED_KAPPAS = ("kind", "polarity", "experiencer", "temporality")
CRITICAL_FIELDS = ("allergy_denial", "condition_assertion", "order_authorization")
LABEL_FIELDS = (*REQUIRED_KAPPAS, "medication_intent", "source_speaker_role")
EXACT_FIELDS = (
    "value_text",
    "unit_text",
    "canonical_value",
    "ucum_unit",
    "dose_text",
    "route_text",
    "frequency_text",
    "gold_concept_id",
    "code_system",
    "codes",
)


def categorical(pairs: Sequence[tuple[Any, Any]]) -> dict:
    """Cohen's kappa; missing is a category, p(expected)=1 stays undefined."""
    n = len(pairs)
    left, right = (
        Counter(canonical_json(a).strip() for a, _ in pairs),
        Counter(canonical_json(b).strip() for _, b in pairs),
    )
    categories = sorted(left.keys() | right.keys())
    raw = sum(a == b for a, b in pairs) / n if n else None
    expected = sum(left[c] * right[c] for c in categories) / n**2 if n else None
    return {
        "n": n,
        "agree_n": sum(a == b for a, b in pairs),
        "raw_agreement": raw,
        "kappa": (raw - expected) / (1 - expected)
        if expected is not None and expected < 1
        else None,
        "undefined_reason": "no applicable matched facts"
        if not n
        else ("single shared category; expected agreement is one" if expected == 1 else None),
        "prevalence": {
            "a": {c: left[c] / n for c in categories},
            "b": {c: right[c] / n for c in categories},
        },
        "missing_a": sum(a is None for a, _ in pairs),
        "missing_b": sum(b is None for _, b in pairs),
    }


def detection(matches: int, a: int, b: int) -> dict:
    return {
        "matches": matches,
        "a_n": a,
        "b_n": b,
        "a_only": a - matches,
        "b_only": b - matches,
        "f1": 2 * matches / (a + b) if a + b else None,
    }


def attribute(fact: AnnotationFact, name: str) -> Any:
    if name in CRITICAL_FIELDS:
        return getattr(fact.critical, name)
    if name == "code_system":
        return tuple(sorted({c.system for c in fact.codes}))
    if name == "codes":
        return tuple(sorted((c.system, c.code) for c in fact.codes))
    return getattr(fact, name)


def applicable(fact: AnnotationFact, name: str) -> bool:
    if name in ("value_text", "unit_text", "canonical_value", "ucum_unit"):
        return fact.kind == "vital_sign"
    if name in (
        "medication_intent",
        "dose_text",
        "route_text",
        "frequency_text",
        "order_authorization",
    ):
        return fact.kind in ("explicit_new_order", "reported_medication")
    if name == "allergy_denial":
        return fact.kind in ("allergy_denial", "allergy_statement")
    if name == "condition_assertion":
        return "problem" in fact.kind or fact.kind == "patient_reported_condition"
    return True


def attributes(pairs: Sequence[tuple[AnnotationFact, AnnotationFact]]) -> dict:
    result = {}
    for name in (*LABEL_FIELDS, *CRITICAL_FIELDS, *EXACT_FIELDS):
        eligible = [(a, b) for a, b in pairs if applicable(a, name) or applicable(b, name)]
        metric = categorical([(attribute(a, name), attribute(b, name)) for a, b in eligible])
        metric["inapplicable_n"] = len(pairs) - len(eligible)
        if name in EXACT_FIELDS:
            metric.pop("kappa")
            metric.pop("undefined_reason")
        result[name] = metric
    return result


def freeze_check(overall: dict, per_kind: dict, labels: dict, *, round_number: int) -> dict:
    failures = []
    if overall["f1"] is None or overall["f1"] < 0.80:
        failures.append({"item": "overall detection F1", "value": overall["f1"], "bar": 0.80})
    for name in REQUIRED_KAPPAS:
        value = labels[name]["kappa"]
        if value is None or value < 0.70:
            failures.append({"item": f"{name} kappa", "value": value, "bar": 0.70})
    for kind, metric in per_kind.items():
        if metric["f1"] is not None and metric["f1"] < 0.80:
            failures.append({"item": f"{kind} detection F1", "value": metric["f1"], "bar": 0.80})
    failures.sort(key=lambda f: -1 if f["value"] is None else f["value"])
    return {
        "status": "FAIL" if failures else "PASS",
        "round": round_number,
        "required_kappas": list(REQUIRED_KAPPAS),
        "worst_items": failures,
        "instruction": "narrow the schema; never lower the bar"
        if failures and round_number >= 3
        else "revise guidelines and repeat independent pilot"
        if failures
        else "eligible to freeze",
    }


def timing_budget(
    a: dict[str, AnnotationDocument],
    b: dict[str, AnnotationDocument],
    assignment: Assignment | None,
    *,
    overhead_hours: float,
    seed: int,
) -> dict:
    strata = {e.id: e.length_stratum for e in assignment.dataset.encounters} if assignment else {}
    rows = [
        {
            "encounter_id": key,
            "length_stratum": strata.get(key, "unknown"),
            "minutes": {
                a[key].annotator_id: a[key].timing.active_seconds / 60,
                b[key].annotator_id: b[key].timing.active_seconds / 60,
            },
        }
        for key in sorted(a)
    ]
    ids = (next(iter(a.values())).annotator_id, next(iter(b.values())).annotator_id)
    groups = {}
    for label in ("overall", "short", "medium", "long", "unknown"):
        subset = [r for r in rows if label == "overall" or r["length_stratum"] == label]
        if not subset:
            continue
        groups[label] = {
            annotator: {
                "encounter_n": len(subset),
                "mean_active_minutes": sum(r["minutes"][annotator] for r in subset) / len(subset),
                "ci_95": bootstrap(
                    subset,
                    lambda sample, k=annotator: sum(r["minutes"][k] for r in sample) / len(sample),
                    seed=seed,
                ),
            }
            for annotator in ids
        }

    # Resample paired encounters to preserve A/B dependence. This is two-person
    # annotation time, with adjudication/training explicitly outside the mean.
    def mean(sample: Sequence[dict]) -> float:
        return sum(sum(r["minutes"].values()) / 60 for r in sample) / len(sample)

    ci = bootstrap(rows, mean, seed=seed)
    rate = mean(rows)
    return {
        "per_encounter": rows,
        "by_stratum_and_annotator": groups,
        "budget": {
            "overhead_hours": overhead_hours,
            "overhead_definition": (
                "fixed assumed training, coordination and adjudication; "
                "replace with measured totals"
            ),
            "paired_annotation_hours_per_visit": rate,
            "ci_95": ci,
            "formula": (
                "H = overhead_hours + N_dev * measured_pair_mean_h + N_test * measured_pair_mean_h"
            ),
            "full_public_40_dev_100_test_hours": overhead_hours + 140 * rate,
            "full_public_ci_95_hours": [overhead_hours + 140 * ci[x] for x in ("low", "high")],
            "reduced_public_40_dev_60_test_hours": overhead_hours + 100 * rate,
            "reduced_public_ci_95_hours": [overhead_hours + 100 * ci[x] for x in ("low", "high")],
            "uncertainty": (
                "paired encounter bootstrap 95%; conditional on fixed overhead. "
                "Dev pilot extrapolation to test; stratum/corpus mix and idle undercount "
                "are not covered. One encounter cannot estimate sampling uncertainty. "
                "Private visits and independent FHIR subset excluded."
            ),
        },
    }


def agreement(
    a: dict[str, AnnotationDocument],
    b: dict[str, AnnotationDocument],
    assignment: Assignment | None = None,
    *,
    round_number: int = 1,
    overhead_hours: float = 65,
    seed: int = 1729,
) -> dict:
    import math

    if round_number < 1 or not math.isfinite(overhead_hours) or overhead_hours < 0:
        raise ValueError("positive round and finite nonnegative overhead required")
    validate_pair(a, b, assignment)
    pairs = []
    counts = {kind.value: [0, 0, 0] for kind in AssertionKind}
    encounters = {}
    for key in sorted(a):
        left, right = a[key].assertions, b[key].assertions
        matches = align(left, right)
        pairs.extend((left[i], right[j]) for i, j in matches)
        encounters[key] = detection(len(matches), len(left), len(right))
        for kind in counts:
            x, y = [f for f in left if f.kind == kind], [f for f in right if f.kind == kind]
            for index, value in enumerate((len(align(x, y)), len(x), len(y))):
                counts[kind][index] += value
    per_kind = {kind: detection(*count) for kind, count in counts.items()}
    overall = detection(
        len(pairs),
        sum(len(d.assertions) for d in a.values()),
        sum(len(d.assertions) for d in b.values()),
    )
    labels = attributes(pairs)
    return {
        "schema_version": "agreement-1",
        "round": round_number,
        "guidelines_sha256": next(iter(a.values())).guidelines_sha256,
        "annotator_ids": [next(iter(a.values())).annotator_id, next(iter(b.values())).annotator_id],
        "matching": (
            "maximum-cardinality Hungarian; resource class, experiencer, polarity, "
            "concept id or normalized acceptable-text intersection, and same-turn "
            "evidence overlap. Per-kind independently aligned with exact kind."
        ),
        "attribute_caveat": (
            "conditional on detection matches: polarity and experiencer agree by "
            "construction; disagreements on these appear as unmatched facts. "
            "Missing labels are categories; inapplicable attributes excluded explicitly. "
            "Detection has no enumerable negatives; no detection kappa."
        ),
        "detection": {"overall": overall, "per_kind": per_kind, "encounters": encounters},
        "attributes": labels,
        "attributes_per_kind": {
            kind.value: attributes([(x, y) for x, y in pairs if x.kind == kind or y.kind == kind])
            for kind in AssertionKind
        },
        "freeze": freeze_check(overall, per_kind, labels, round_number=round_number),
        "timing": timing_budget(a, b, assignment, overhead_hours=overhead_hours, seed=seed),
    }


def markdown(report: dict) -> str:
    lines = [
        "# Independent human agreement",
        "",
        f"Round {report['round']} · freeze {report['freeze']['status']}",
        "",
        report["matching"],
        "",
        report["attribute_caveat"],
        "",
        "Required kappas: " + ", ".join(report["freeze"]["required_kappas"]),
        "",
        "| Kind | A | B | Matched | A only | B only | F1 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for kind, row in {
        "overall": report["detection"]["overall"],
        **report["detection"]["per_kind"],
    }.items():
        lines.append(
            f"| {kind} | {row['a_n']} | {row['b_n']} | {row['matches']} | "
            f"{row['a_only']} | {row['b_only']} | {row['f1']} |"
        )
    for title, labels in [
        ("Overall attributes", report["attributes"]),
        *(
            (kind, labels)
            for kind, labels in report["attributes_per_kind"].items()
            if labels["kind"]["n"]
        ),
    ]:
        lines.extend(
            [
                "",
                f"## {title}",
                "",
                "| Attribute | N | Inapplicable N | Raw | Kappa | Missing A/B | Prevalence A/B |",
                "|---|---:|---:|---:|---|---|---|",
            ]
        )
        for name, metric in labels.items():
            kappa = metric.get("kappa", "exact only")
            if kappa is None:
                kappa = f"undefined: {metric['undefined_reason']}"
            lines.append(
                f"| {name} | {metric['n']} | {metric['inapplicable_n']} | "
                f"{metric['raw_agreement']} | {kappa} | "
                f"{metric['missing_a']}/{metric['missing_b']} | {metric['prevalence']} |"
            )
    lines.extend(["", "## Freeze", "", report["freeze"]["instruction"], ""])
    lines.extend(
        f"- {f['item']}: {f['value']} (requires {f['bar']})"
        for f in report["freeze"]["worst_items"]
    )
    lines.extend(
        [
            "",
            "## Timing and repriced budget",
            "",
            "```json",
            canonical_json(report["timing"]).strip(),
            "```",
            "",
        ]
    )
    return "\n".join(lines)
