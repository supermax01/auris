"""Review triage: which items need a clinician's individual attention.

Every record still needs a clinician's approval. Triage only decides how the
approval is given: items that need attention are reviewed one by one; the
rest ("routine") may be approved together from a compact list, which writes
one receipt per item labelled as a batch approval.

The rule is conservative by construction: a flag counts as routine only when
it is listed in ``INFORMATIONAL`` (or passes a narrower rule below), so any
new or unknown flag needs attention. This keeps the clinician's
review time for the items where it matters.
"""

from __future__ import annotations

from typing import Any, Literal

from auris.fhir.models import LaneState, MappingOutcome

Triage = Literal["attention", "routine"]

# Kinds that always need individual attention: a new order, allergies and
# refutations change care directly.
ATTENTION_KINDS = frozenset(
    {"explicit_new_order", "allergy_statement", "allergy_denial", "clinician_ruled_out_problem"}
)
# A medicine the patient already takes (or took) is routine; a start, stop,
# change or discussion is not.
ROUTINE_MEDICATION_INTENTS = frozenset({"continuing", "historical"})

# Flags that describe the checker, coding or an already-preserved uncertainty,
# not a likely error in the fact.
INFORMATIONAL = frozenset(
    {
        "negated",
        "medication",  # kind rules above decide medicines
        "needs_coding_review",
        "uncoded_text_only",
        "terminology_release_out_of_period",
        "snomed_not_supplied",
        "historical_condition",  # no "active" status is written
        "condition_temporality_unclear",
        "temporality_unclear",
        "historical_without_date",
        "measurement_date_not_day_precision",
        "literal_null_normalised",
        "concept_text_not_in_quote",  # except for a condition record, below
        "negation_refusal_cleared",  # a clinician already cleared it with a reason
    }
)
_CONDITION_KINDS = frozenset({"clinician_assessed_problem", "patient_reported_condition"})
_SHORT_QUOTE_WORDS = 4


def _quotes(outcome: MappingOutcome) -> list[str]:
    return [
        item.aligned.aligned_text if item.aligned else item.quote for item in outcome.evidence.items
    ]


def triage(
    outcome: MappingOutcome, fields: dict[str, Any] | None = None
) -> tuple[Triage, tuple[str, ...]]:
    """("attention" or "routine", the reasons that need attention, in order).

    ``fields`` are the assertion's fields when available (else the ledger's).
    """
    fields = fields if fields is not None else (outcome.ledger or {})
    reasons = tuple(
        dict.fromkeys(
            (*outcome.review_reasons, *outcome.consistency.review_reasons, *outcome.mapper_flags)
        )
    )
    attention: list[str] = []
    if LaneState.FAIL in outcome.lanes.model_dump().values():
        attention.append("check_failed")
    if outcome.consistency.failures:
        attention.append("consistency_failed")
    if outcome.kind in ATTENTION_KINDS:
        attention.append(outcome.kind)
    if (
        outcome.kind == "reported_medication"
        and fields.get("medication_intent") not in ROUTINE_MEDICATION_INTENTS
    ):
        attention.append("medication_change_or_discussion")
    family = fields.get("experiencer") == "family_member"
    negated = "negated" in reasons
    condition_record = (
        outcome.resource is not None and outcome.resource.get("resourceType") == "Condition"
    )
    quotes = _quotes(outcome)
    short_quote = max((len(q.split()) for q in quotes), default=0) <= _SHORT_QUOTE_WORDS
    for reason in reasons:
        if reason == "concept_text_not_in_quote" and condition_record and not negated:
            # A matching quote proves the words exist, not the diagnosis.
            attention.append("diagnosis_not_in_quote")
        elif reason in INFORMATIONAL:
            continue
        elif reason in ("non_patient_experiencer", "patient_experiencer_required") and family:
            continue  # family history, kept as such and never a patient record
        elif reason == "polarity_conflicts_with_quote" and not (
            short_quote or "concept_text_not_in_quote" in reasons
        ):
            # A denial elsewhere in a long quote that names the concept ("nausea,
            # but no vomiting") is scope noise; a short answer like "No." is not.
            continue
        elif reason == "patient_report_without_patient_turn" and negated:
            continue  # a denied history ("Any diabetes?" "No.") is the patient's own answer
        else:
            attention.append(reason)
    attention = list(dict.fromkeys(attention))
    return ("attention" if attention else "routine"), tuple(attention)


def routine_group(outcome: MappingOutcome, fields: dict[str, Any] | None = None) -> str:
    """A heading for the compact routine list, so like items are read together."""
    fields = fields if fields is not None else (outcome.ledger or {})
    if "negated" in outcome.review_reasons:
        return "Pertinent negatives"
    if fields.get("experiencer") == "family_member":
        return "Family history"
    return {
        "symptom": "Symptoms",
        "patient_reported_condition": "Conditions",
        "clinician_assessed_problem": "Conditions",
        "reported_medication": "Medicines the patient already takes",
        "vital_sign": "Measurements",
        "plan_item": "Plan",
        "social_history": "Social history",
    }.get(outcome.kind, "Other")
