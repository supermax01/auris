"""Material-assertion classification (PLAN A4).

A material assertion is any medication, allergy, negated or refuted,
or non-patient-experiencer assertion. Review is required for every
material assertion before final export (A4; gating itself is a later
step).

``review_reasons`` additionally reports abstentions and the A3 review
triggers visible in this form (historical/unclear conditions and stated
allergy resolution). Unknown/null experiencers are treated as abstentions,
not evidence of a non-patient experiencer; they still require review.
Evidence, coding, prescriber identification and other mapper-specific
reasons are added by later stages. This module does not authorize export.
"""

from __future__ import annotations

from enum import StrEnum

from auris.assertions import (
    AllergyStatementAssertion,
    AssertionBase,
    Experiencer,
    Polarity,
    Temporality,
)
from auris.transcript import SpeakerRole


class ReviewReason(StrEnum):
    # Material under PLAN A4.
    MEDICATION = "medication"
    ALLERGY = "allergy"
    NEGATED = "negated"
    REFUTED = "refuted"
    NON_PATIENT_EXPERIENCER = "non_patient_experiencer"
    # Not material under A4, but still needs review before export.
    POLARITY_NOT_STATED = "polarity_not_stated"
    EXPERIENCER_UNKNOWN = "experiencer_unknown"
    CONCEPT_TEXT_MISSING = "concept_text_missing"
    SOURCE_ROLE_UNKNOWN = "source_role_unknown"
    HISTORICAL_CONDITION = "historical_condition"
    CONDITION_TEMPORALITY_UNCLEAR = "condition_temporality_unclear"
    ALLERGY_RESOLUTION_STATED = "allergy_resolution_stated"


MATERIAL_REASONS: frozenset[ReviewReason] = frozenset(
    {
        ReviewReason.MEDICATION,
        ReviewReason.ALLERGY,
        ReviewReason.NEGATED,
        ReviewReason.REFUTED,
        ReviewReason.NON_PATIENT_EXPERIENCER,
    }
)

_MEDICATION_KINDS = frozenset({"explicit_new_order", "reported_medication"})
_ALLERGY_KINDS = frozenset({"allergy_statement", "allergy_denial"})
_POSITIVE_CONDITION_KINDS = frozenset({"clinician_assessed_problem", "patient_reported_condition"})


def material_reasons(assertion: AssertionBase) -> tuple[ReviewReason, ...]:
    """The A4 reasons this assertion is material, in stable order."""
    reasons: list[ReviewReason] = []
    if assertion.kind in _MEDICATION_KINDS:
        reasons.append(ReviewReason.MEDICATION)
    if assertion.kind in _ALLERGY_KINDS:
        reasons.append(ReviewReason.ALLERGY)
    if assertion.polarity is Polarity.NEGATED:
        reasons.append(ReviewReason.NEGATED)
    if assertion.kind == "clinician_ruled_out_problem":
        # The kind itself is the refutation (A3 maps it to
        # verificationStatus=refuted), whatever the polarity field says.
        reasons.append(ReviewReason.REFUTED)
    if assertion.experiencer in (Experiencer.FAMILY_MEMBER, Experiencer.OTHER):
        reasons.append(ReviewReason.NON_PATIENT_EXPERIENCER)
    return tuple(reasons)


def is_material(assertion: AssertionBase) -> bool:
    """True when the assertion is material under PLAN A4."""
    return bool(material_reasons(assertion))


def review_reasons(assertion: AssertionBase) -> tuple[ReviewReason, ...]:
    """Review reasons available from the reduced form, in stable order."""
    reasons = list(material_reasons(assertion))
    if assertion.polarity is None:
        reasons.append(ReviewReason.POLARITY_NOT_STATED)
    if assertion.experiencer is None or assertion.experiencer is Experiencer.UNKNOWN:
        reasons.append(ReviewReason.EXPERIENCER_UNKNOWN)
    if assertion.concept_text is None:
        reasons.append(ReviewReason.CONCEPT_TEXT_MISSING)
    if assertion.source_speaker_role in (None, SpeakerRole.UNKNOWN):
        reasons.append(ReviewReason.SOURCE_ROLE_UNKNOWN)
    if assertion.kind in _POSITIVE_CONDITION_KINDS:
        if assertion.temporality in (Temporality.PAST, Temporality.RESOLVED):
            reasons.append(ReviewReason.HISTORICAL_CONDITION)
        elif assertion.temporality in (None, Temporality.UNCLEAR):
            reasons.append(ReviewReason.CONDITION_TEMPORALITY_UNCLEAR)
    if isinstance(assertion, AllergyStatementAssertion) and (
        assertion.stated_resolution_text is not None
        or assertion.temporality is Temporality.RESOLVED
    ):
        reasons.append(ReviewReason.ALLERGY_RESOLUTION_STATED)
    return tuple(reasons)
