"""Maximum-weight assignment and blind, gold-only concept equivalence."""

import math
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from auris.assertions import AssertionKind
from auris.eval.gold import Fact, GoldAssertion, Span
from auris.eval.normalization import normalize, normalize_dose, vital_unit, vital_value
from auris.eval.predictions import Prediction, record_quantities

RESOURCE_CLASSES = {
    "vital_sign": "Observation",
    "symptom": "ledger:symptom",
    "clinician_assessed_problem": "Condition",
    "clinician_ruled_out_problem": "Condition",
    "patient_reported_condition": "Condition",
    "explicit_new_order": "MedicationRequest",
    "reported_medication": "ledger:medication",
    "allergy_statement": "AllergyIntolerance",
    "allergy_denial": "AllergyIntolerance",
}


def concept_equal(prediction: Fact, gold: GoldAssertion) -> bool:
    # A code can identify a concept only via the adjudicator's canonical ID.
    if any(f"{c.system}:{c.code}" == gold.gold_concept_id for c in prediction.codes):
        return True
    surface = prediction.allergen_text or prediction.concept_text
    return bool(normalize(surface)) and normalize(surface) in {
        normalize(text) for text in gold.acceptable_texts
    }


def overlap(first: Sequence[Span], second: Sequence[Span]) -> bool:
    return any(
        a.segment_id == b.segment_id
        and max(a.char_start, b.char_start) < min(a.char_end, b.char_end)
        for a in first
        for b in second
    )


def _values_equal(first: tuple[str, ...] | None, second: tuple[str, ...] | None) -> bool:
    if first is None or second is None:
        return False
    try:
        left, right = tuple(map(Decimal, first)), tuple(map(Decimal, second))
        return all(n.is_finite() for n in (*left, *right)) and left == right
    except InvalidOperation:
        return False


def strict_reasons(
    prediction: Prediction, gold: GoldAssertion, *, record: bool = False
) -> list[str]:
    """Explain the extra strict constraints on an otherwise lenient pair."""
    reasons = []
    if not overlap(prediction.evidence, gold.evidence):
        reasons.append("evidence does not overlap gold in the same segment")
    if prediction.codes and gold.codes and not (set(prediction.codes) & set(gold.codes)):
        reasons.append(f"code mismatch {prediction.codes!r} vs {gold.codes!r}")
    resource_class = RESOURCE_CLASSES[gold.kind]
    if record and not resource_class.startswith("ledger:"):
        if not prediction.resource:
            reasons.append("no built FHIR resource")
        elif prediction.resource.get("resourceType") != resource_class:
            reasons.append(f"built FHIR resource type is not {resource_class}")
    if gold.kind == AssertionKind.VITAL_SIGN:
        if record:
            quantities = record_quantities(prediction.resource)
            values = (
                tuple(str(q["value"]) for q in quantities)
                if quantities and all("value" in q for q in quantities)
                else None
            )
            units = tuple(q.get("code") for q in quantities)
            unit_ok = bool(units) and all(
                q.get("system") == "http://unitsofmeasure.org" and q.get("code") == gold.ucum_unit
                for q in quantities
            )
            value_description, unit_description = repr(values), repr(units)
        else:
            values = vital_value(prediction.value_text)
            unit = vital_unit(prediction.unit_text)
            unit_ok = unit is not None and unit == gold.ucum_unit
            value_description = f"{prediction.value_text!r} (canonical {values!r})"
            unit_description = f"{prediction.unit_text!r} (canonical {unit!r})"
        if not _values_equal(values, gold.canonical_value):
            reasons.append(f"value mismatch {value_description} vs {gold.canonical_value!r}")
        if not gold.ucum_unit or not unit_ok:
            reasons.append(f"unit mismatch {unit_description} vs {gold.ucum_unit!r}")
    if gold.kind == AssertionKind.EXPLICIT_NEW_ORDER:
        for field in ("dose_text", "route_text", "frequency_text"):
            normalizer = normalize_dose if field == "dose_text" else normalize
            proposed, expected = getattr(prediction, field), getattr(gold, field)
            if normalizer(proposed) != normalizer(expected):
                reasons.append(
                    f"{field.removesuffix('_text')} mismatch {proposed!r} vs {expected!r}"
                )
    return reasons


def eligible(
    prediction: Prediction, gold: GoldAssertion, *, strict: bool = False, record: bool = False
) -> bool:
    if (RESOURCE_CLASSES[prediction.kind], prediction.experiencer, prediction.polarity) != (
        RESOURCE_CLASSES[gold.kind],
        gold.experiencer,
        gold.polarity,
    ) or not concept_equal(prediction, gold):
        return False
    return not strict or not strict_reasons(prediction, gold, record=record)


def hungarian(weights: Sequence[Sequence[float]]) -> tuple[tuple[int, int], ...]:
    """Maximum-weight rectangular assignment, O(max(n,m)^3), deterministic ties.

    Returns min(n,m) pairs; callers discard zero-weight/noneligible pairs.
    Shortest augmenting paths with row/column potentials, transposing when
    needed. Negative weights are supported. Empty dimensions are legal.
    """
    n = len(weights)
    if not n:
        return ()
    m = len(weights[0])
    if any(len(row) != m for row in weights):
        raise ValueError("assignment matrix must be rectangular")
    if any(not math.isfinite(x) for row in weights for x in row):
        raise ValueError("assignment weights must be finite")
    if not m:
        return ()
    if n > m:
        return tuple(sorted((j, i) for i, j in hungarian(list(zip(*weights, strict=True)))))
    u, v = [0.0] * (n + 1), [0.0] * (m + 1)
    p, way = [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        distances, used = [math.inf] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], math.inf, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cost = -weights[i0 - 1][j - 1] - u[i0] - v[j]
                    if cost < distances[j]:
                        distances[j], way[j] = cost, j0
                    if distances[j] < delta:
                        delta, j1 = distances[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    distances[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    return tuple(sorted((p[j] - 1, j - 1) for j in range(1, m + 1) if p[j]))


def match(
    predictions: Sequence[Prediction],
    gold: Sequence[GoldAssertion],
    *,
    strict: bool = False,
    same_kind: bool = False,
    record: bool = False,
) -> tuple[tuple[int, int], ...]:
    # Cardinality dominates evidence tie-breaking; overlapping evidence cannot
    # trade away a valid match. Strict and lenient are optimized independently.
    base = min(len(predictions), len(gold)) + 1
    weights = [
        [
            (base + int(overlap(p.evidence, g.evidence)))
            if eligible(p, g, strict=strict, record=record) and (not same_kind or p.kind == g.kind)
            else 0
            for g in gold
        ]
        for p in predictions
    ]
    return tuple((i, j) for i, j in hungarian(weights) if weights[i][j] > 0)
