"""Lane-2 safety rules on fictional quotes, including the measured regressions."""

import pytest
from extract_helpers import case_input
from validator_helpers import passing_run

from auris.consistency import check_consistency, numeric_tokens
from auris.evidence import EvidenceLane, align_assertion_evidence, has_negation
from auris.fhir import Mapper, build_collection
from auris.fhir.models import LaneState, resource_digest
from auris.validation.report import apply_validation, validation_document


def checked(name, *, changes=None, quote=None, role=None):
    transcript, assertion = case_input(name, changes=changes, quote=quote, role=role)
    aligned = align_assertion_evidence(assertion, transcript)
    return check_consistency(assertion, aligned, transcript)


@pytest.mark.parametrize("cue", ["none", "nil", "negative", "nothing", "absent"])
def test_shared_extended_negation_cues(cue):
    assert has_negation(cue.upper())
    assert not has_negation(cue + "ish")
    transcript, assertion = case_input("symptom", quote=f"Cough: {cue}.")
    result = checked("symptom", quote=f"Cough: {cue}.")
    assert result.review_reasons == ("polarity_conflicts_with_quote",)
    # Dropping the newly recognized cue also fails the original evidence lane.
    clipped = assertion.model_copy(
        update={"evidence": (assertion.evidence[0].model_copy(update={"quote": "Cough"}),)}
    )
    assert align_assertion_evidence(clipped, transcript).lane == EvidenceLane.FAIL


@pytest.mark.parametrize("case", ["negated_symptom", "ruled_out_problem", "no_known_allergies"])
def test_supported_negation_does_not_conflict(case):
    result = checked(case)
    assert not result.failures and not result.review_reasons


@pytest.mark.parametrize("case", ["vital_sign", "symptom", "assessed_problem"])
def test_supported_affirmation_does_not_conflict(case):
    result = checked(case)
    assert not result.failures and not result.review_reasons


def test_natural_demo_medication_ignores_negation_in_another_sentence():
    result = checked(
        "continuing_medication",
        changes={"concept_text": "albuterol"},
        quote=(
            "I still use albuterol. I brought the inhaler with me because I can never "
            "remember everything on the label."
        ),
    )
    assert not result.failures and not result.review_reasons


@pytest.mark.parametrize(
    "quote",
    [
        "I take albuterol but not every day.",
        "I take albuterol. I do not take albuterol every day.",
        "I do not take albuterol every day. I take albuterol.",
    ],
)
def test_same_sentence_medication_negation_still_requires_review(quote):
    result = checked("continuing_medication", changes={"concept_text": "albuterol"}, quote=quote)
    assert not result.failures
    assert result.review_reasons == ("polarity_conflicts_with_quote",)


@pytest.mark.parametrize("polarity", ["affirmed", "negated"])
def test_absent_key_term_keeps_whole_quote_negation_scope(polarity):
    result = checked(
        "continuing_medication",
        changes={"concept_text": "albuterol", "polarity": polarity},
        quote="I use the inhaler. I can never remember the label.",
    )
    assert result.failures == ("concept_text_not_in_quote",)
    assert result.review_reasons == (
        ("polarity_conflicts_with_quote",) if polarity == "affirmed" else ()
    )


def test_unrelated_sentence_cannot_support_extracted_negation():
    result = checked(
        "continuing_medication",
        changes={"concept_text": "albuterol", "polarity": "negated"},
        quote="I still use albuterol. I can never remember the label.",
    )
    assert not result.failures
    assert result.review_reasons == ("negation_not_in_quote",)


@pytest.mark.parametrize("separator", [".", "?", "!", ";"])
@pytest.mark.parametrize("negated", [False, True])
def test_sentence_scope_uses_existing_terminology_aliases(
    terminology, units, intake, separator, negated
):
    action = "do not take" if negated else "take"
    transcript, assertion = case_input(
        "continuing_medication",
        changes={"concept_text": "ACETAMINOPHEN"},
        quote=f"I {action} PARACETAMOL{separator} I never read labels.",
    )
    outcome = Mapper(intake, transcript, terminology, units).map_assertion(assertion, 0)
    assert not outcome.consistency.failures
    assert outcome.consistency.review_reasons == (
        ("polarity_conflicts_with_quote",) if negated else ()
    )


def test_sentence_scope_uses_allergen_when_concept_is_absent():
    result = checked(
        "affirmed_allergy",
        changes={"concept_text": None},
        quote="Penicillin gives me a rash. I can never remember when it started.",
    )
    assert not result.failures and not result.review_reasons
    denied = checked(
        "affirmed_allergy", changes={"concept_text": None}, quote="I am not allergic to penicillin."
    )
    assert denied.review_reasons == ("polarity_conflicts_with_quote",)


@pytest.mark.parametrize(
    "case,changes,quote,reason",
    [
        (
            "affirmed_allergy",
            {},
            "I am not allergic to penicillin.",
            "polarity_conflicts_with_quote",
        ),
        (
            "vital_sign",
            {"polarity": "negated", "concept_text": "blood pressure", "value_text": "128 over 82"},
            "Your blood pressure is 128 over 82.",
            "negation_not_in_quote",
        ),
        ("no_known_allergies", {}, "I have allergies.", "negation_not_in_quote"),
        ("ruled_out_problem", {}, "This is pneumonia.", "negation_not_in_quote"),
        ("family_member_condition", {"experiencer": "patient"}, None, "experiencer_cue_conflict"),
        ("assessed_problem", {"source_speaker_role": "patient"}, None, "kind_role_mismatch"),
    ],
)
def test_review_conflicts_force_material(case, changes, quote, reason, terminology, units, intake):
    transcript, assertion = case_input(case, changes=changes, quote=quote)
    mapper = Mapper(intake, transcript, terminology, units)
    outcome = mapper.map_assertion(assertion, 0)
    assert reason in outcome.review_reasons
    assert outcome.is_material
    # Passing official lanes cannot make these unreviewed facts exportable.
    run = passing_run(intake, [outcome])
    outcome = apply_validation(outcome, run)
    assert outcome.export_state != "final_exportable"
    with pytest.raises(ValueError, match="material assertion"):
        build_collection(intake, [outcome], validation=run)


@pytest.mark.parametrize("case", ["assessed_problem", "ruled_out_problem", "ordered_medication"])
def test_nonclinician_evidence_rejects_clinician_kinds(case):
    assert "kind_role_mismatch" in checked(case, role="patient").review_reasons
    assert "kind_role_mismatch" not in checked(case).review_reasons


@pytest.mark.parametrize(
    "cue",
    [
        "mother",
        "father",
        "sister",
        "brother",
        "son",
        "daughter",
        "wife",
        "husband",
        "partner",
        "grandmother",
        "grandfather",
        "aunt",
        "uncle",
        "mother’s",
        "cousin",
    ],
)
def test_family_cue_review_and_correct_experiencer(cue):
    quote = f"My {cue} has diabetes."
    assert checked("patient_condition", quote=quote).review_reasons == ("experiencer_cue_conflict",)
    assert not checked("family_member_condition", quote=quote).review_reasons


@pytest.mark.parametrize(
    "case,field,wrong,quote",
    [
        ("vital_sign", "value_text", "73", "Your heart rate is 72."),
        (
            "ordered_medication",
            "dose_text",
            "500 milligrams",
            "I prescribe amoxicillin 50 milligrams.",
        ),
        ("ordered_medication", "dose_text", "1 mg", "I prescribe amoxicillin 1.5 mg."),
        ("ordered_medication", "dose_text", "5 mg", "I prescribe amoxicillin .5 mg."),
        ("ordered_medication", "dose_text", "5 mg", "I prescribe amoxicillin -5 mg."),
    ],
)
def test_invented_numbers_fail_and_cannot_be_waived(
    case, field, wrong, quote, terminology, units, intake
):
    transcript, assertion = case_input(case, changes={field: wrong}, quote=quote)
    mapper = Mapper(intake, transcript, terminology, units)
    outcome = mapper.map_assertion(assertion, 0)
    assert f"{field}_number_not_in_quote" in outcome.consistency.failures
    assert outcome.lanes.evidence == LaneState.FAIL
    run = passing_run(intake, [outcome])
    approved = apply_validation(outcome, run).model_copy(
        update={"reviewed_resource_digest": resource_digest(outcome.resource)}
    )
    assert approved.export_state == "validation_failed"
    doc = validation_document(intake, [approved], run)
    assert doc["resources"][-1]["lanes"]["evidence"]["issues"][0]["code"] == "consistency"


@pytest.mark.parametrize("value", ["500", "1.5", ".5", "-5", "128/82", "128 over 82", "500mg"])
def test_numeric_support_preserves_punctuation(value):
    result = checked("vital_sign", changes={"value_text": value}, quote=f"Heart rate is {value}.")
    assert not result.failures
    assert numeric_tokens(value)


@pytest.mark.parametrize(
    "case,field,value",
    [
        ("patient_condition", "concept_text", "pneumonia"),
        ("affirmed_allergy", "allergen_text", "peanuts"),
        ("vital_sign", "concept_text", "weight"),
    ],
)
def test_absent_concept_or_allergen_fails(case, field, value):
    assert f"{field}_not_in_quote" in checked(case, changes={field: value}).failures
    assert not checked(case).failures


def test_exact_synonyms_allowed_but_fuzzy_candidates_not(terminology, units, intake):
    transcript, assertion = case_input(
        "ordered_medication",
        changes={"concept_text": "acetaminophen"},
        quote="I prescribe paracetamol 500 milligrams.",
    )
    outcome = Mapper(intake, transcript, terminology, units).map_assertion(assertion, 0)
    assert not outcome.consistency.failures
    # Wrong drug with similar text must not pass by a Jaccard candidate.
    wrong = assertion.model_copy(update={"concept_text": "amoxicillin"})
    assert (
        Mapper(intake, transcript, terminology, units).map_assertion(wrong, 0).consistency.failures
    )


def test_vital_alias_reuses_mapper_names(terminology, units, intake):
    transcript, assertion = case_input("vital_sign", quote="Your pulse is 72.")
    assert (
        not Mapper(intake, transcript, terminology, units)
        .map_assertion(assertion, 0)
        .consistency.failures
    )


def test_alignment_failure_does_not_make_up_consistency_results():
    transcript, assertion = case_input("vital_sign")
    missing = assertion.model_copy(
        update={"evidence": (assertion.evidence[0].model_copy(update={"segment_id": "missing"}),)}
    )
    assert not check_consistency(
        missing, align_assertion_evidence(missing, transcript), transcript
    ).failures
