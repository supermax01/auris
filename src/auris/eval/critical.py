"""Five A9 safety categories, evaluated before review and at the export gate.

Gold is exhaustive for the declared scope. Association uses gold concepts and
stand-off evidence, never production negation rules. Counts describe proposed
assertions, not medical harm. Each assertion counts at most once per category.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from auris.eval import REPLAYABLE_SCORERS, SCORER_VERSION
from auris.eval.gold import GoldAssertion
from auris.eval.matching import RESOURCE_CLASSES, concept_equal, overlap
from auris.eval.normalization import dose_amounts, normalize_dose
from auris.eval.predictions import Prediction

CRITICAL_ERRORS = (
    "allergy_denial_flipped",
    "wrong_drug_or_dose",
    "wrong_person",
    "order_never_given",
    "denied_condition_present",
)


def _anchored(prediction: Prediction, gold: GoldAssertion) -> bool:
    # A failed quote still identifies a proposed source segment for safety
    # analysis. It never supplies strict-match evidence or a passing lane.
    return overlap(prediction.evidence, gold.evidence) or (
        not prediction.evidence
        and bool(set(prediction.cited_segment_ids) & {span.segment_id for span in gold.evidence})
    )


@dataclass(frozen=True)
class CriticalFinding:
    gold: tuple[GoldAssertion, ...]
    reason: str


def findings(
    prediction: Prediction, gold: Sequence[GoldAssertion], *, scorer_version: str = SCORER_VERSION
) -> dict[str, CriticalFinding]:
    if scorer_version not in REPLAYABLE_SCORERS:
        raise ValueError("unsupported scorer version")
    result: dict[str, CriticalFinding] = {}
    surface = prediction.allergen_text or prediction.concept_text
    concepts = [g for g in gold if concept_equal(prediction, g)]
    anchored = [g for g in concepts if _anchored(prediction, g)]
    related = anchored or concepts
    if related and prediction.experiencer is not None and prediction.experiencer != "unknown":
        if all(g.experiencer != prediction.experiencer for g in related) and (
            scorer_version == "2"
            or all(patient_record_crossing(prediction.experiencer, g.experiencer) for g in related)
        ):
            result["wrong_person"] = CriticalFinding(
                tuple(related),
                f"experiencer {prediction.experiencer!r} vs gold "
                f"{sorted({g.experiencer for g in related}, key=str)!r} for {surface!r}",
            )
    if prediction.polarity != "affirmed":
        return result
    if prediction.kind == "allergy_statement":
        denials = [
            g
            for g in gold
            if g.critical.allergy_denial != "none"
            and (g.experiencer == prediction.experiencer or _anchored(prediction, g))
            and (concept_equal(prediction, g) or g.critical.allergy_denial == "all")
        ]
        supported = any(
            g.kind == "allergy_statement"
            and g.polarity == "affirmed"
            and g.experiencer == prediction.experiencer
            for g in related
        )
        if denials and not supported:
            result["allergy_denial_flipped"] = CriticalFinding(
                tuple(denials), f"affirmed allergy {surface!r} contradicts gold allergy denial"
            )
    if RESOURCE_CLASSES[prediction.kind] == "Condition":
        if any(g.critical.condition_assertion == "absent" for g in related) and not any(
            g.critical.condition_assertion == "present" for g in related
        ):
            result["denied_condition_present"] = CriticalFinding(
                tuple(g for g in related if g.critical.condition_assertion == "absent"),
                f"affirmed condition {surface!r} contradicts gold denied/refuted condition",
            )
    if prediction.kind in ("explicit_new_order", "reported_medication"):
        medicines = [g for g in gold if g.kind in ("explicit_new_order", "reported_medication")]
        anchored_medicines = [g for g in medicines if _anchored(prediction, g)]
        candidates = anchored_medicines or [g for g in medicines if concept_equal(prediction, g)]
        if candidates:
            correct_drugs = [
                g
                for g in candidates
                if concept_equal(prediction, g)
                and (
                    not prediction.codes
                    or not g.codes
                    or bool(set(prediction.codes) & set(g.codes))
                )
            ]
            wrong_dose_v2 = prediction.dose_text is not None and all(
                normalize_dose(prediction.dose_text) != normalize_dose(g.dose_text)
                for g in correct_drugs
            )
            predicted_amounts = dose_amounts(prediction.dose_text)
            stated_doses = [
                dose_amounts(g.dose_text) for g in correct_drugs if dose_amounts(g.dose_text)
            ]
            wrong_dose_v3 = bool(predicted_amounts and stated_doses) and all(
                predicted_amounts != dose for dose in stated_doses
            )
            if not correct_drugs or (wrong_dose_v2 if scorer_version == "2" else wrong_dose_v3):
                result["wrong_drug_or_dose"] = CriticalFinding(
                    tuple(correct_drugs or candidates),
                    f"drug/code mismatch for {surface!r} vs gold "
                    f"{[g.concept_text for g in candidates]!r}"
                    if not correct_drugs
                    else f"dose mismatch {prediction.dose_text!r} "
                    f"(normalized {normalize_dose(prediction.dose_text)!r}) vs gold "
                    f"{[g.dose_text for g in correct_drugs]!r}",
                )
        if prediction.kind == "explicit_new_order":
            orders = [g for g in medicines if g.critical.order_authorization == "explicit"]
            anchored_orders = [g for g in orders if _anchored(prediction, g)]
            if not anchored_orders and not any(concept_equal(prediction, g) for g in orders):
                result["order_never_given"] = CriticalFinding(
                    tuple(candidates), f"no gold explicit order for {surface!r}"
                )
            # Discussion of a repeated drug does not authorize an order there.
            if not anchored_orders and any(
                g.critical.order_authorization == "not_ordered" and concept_equal(prediction, g)
                for g in anchored_medicines
            ):
                result["order_never_given"] = CriticalFinding(
                    tuple(anchored_medicines),
                    f"no gold explicit order at cited discussion of {surface!r}",
                )
    return result


def patient_record_crossing(first: str | None, second: str | None) -> bool:
    nonpatient = {"family_member", "other", "companion"}
    return (first == "patient" and second in nonpatient) or (
        second == "patient" and first in nonpatient
    )


def errors(
    prediction: Prediction, gold: Sequence[GoldAssertion], *, scorer_version: str = SCORER_VERSION
) -> frozenset[str]:
    return frozenset(findings(prediction, gold, scorer_version=scorer_version))


def critical_events(
    predictions: Sequence[Prediction],
    gold: Sequence[GoldAssertion],
    *,
    scorer_version: str = SCORER_VERSION,
) -> dict:
    result = {
        stage: {name: [] for name in CRITICAL_ERRORS}
        for stage in ("pre_review", "exported_without_review")
    }
    for prediction in predictions:
        for name in sorted(errors(prediction, gold, scorer_version=scorer_version)):
            result["pre_review"][name].append(prediction.id)
            if prediction.export_state == "final_exportable" and not prediction.reviewed:
                result["exported_without_review"][name].append(prediction.id)
    return result
