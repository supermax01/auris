"""Consistency-lane hygiene on fictional assertions, including Qwen regressions."""

import pytest
from extract_helpers import case_input
from validator_helpers import passing_run

from auris.consistency import check_consistency
from auris.evidence import align_assertion_evidence
from auris.fhir import Mapper
from auris.validation.report import apply_validation


@pytest.mark.parametrize("literal", ["null", " NONE ", "N/A", "unknown"])
@pytest.mark.parametrize(
    "case,field",
    [
        ("vital_sign", "value_text"),
        ("vital_sign", "unit_text"),
        ("vital_sign", "stated_time_text"),
        ("ordered_medication", "dose_text"),
        ("ordered_medication", "route_text"),
        ("ordered_medication", "frequency_text"),
    ],
)
def test_literal_nulls_are_informational_and_raw_extraction_survives(case, field, literal):
    transcript, assertion = case_input(case, changes={field: literal})
    result = check_consistency(
        assertion, align_assertion_evidence(assertion, transcript), transcript
    )
    assert result.normalised_null_fields == (field,)
    assert result.informational_flags == ("literal_null_normalised",)
    assert not result.force_material
    assert getattr(result.usable_assertion(assertion), field) is None
    assert getattr(assertion, field) == literal


@pytest.mark.parametrize("field", ["concept_text", "allergen_text"])
@pytest.mark.parametrize("literal", ["null", "none", "n/a", "unknown"])
def test_concept_and_allergen_are_never_cleaned(field, literal):
    transcript, assertion = case_input("affirmed_allergy", changes={field: literal})
    result = check_consistency(
        assertion, align_assertion_evidence(assertion, transcript), transcript
    )
    assert not result.informational_flags
    assert getattr(result.usable_assertion(assertion), field) == literal
    assert f"{field}_not_in_quote" in result.failures


@pytest.mark.parametrize(
    "case,field",
    [
        ("vital_sign", "value_text"),
        ("vital_sign", "unit_text"),
        ("ordered_medication", "dose_text"),
    ],
)
@pytest.mark.parametrize(
    "comment", ["(stated)", "Implied", "not explicitly stated", "presumably", "likely", "context"]
)
def test_commentary_is_unusable_and_requires_review(case, field, comment):
    transcript, assertion = case_input(case, changes={field: f"500 {comment}"})
    result = check_consistency(
        assertion, align_assertion_evidence(assertion, transcript), transcript
    )
    assert result.unusable_fields == (field,)
    assert result.review_reasons == ("field_contains_commentary",)
    assert result.force_material
    assert not result.failures  # Discarded commentary does not supply numeric evidence.
    assert getattr(result.usable_assertion(assertion), field) is None


def test_qwen_literal_null_dose_does_not_refuse_order(terminology, units, intake):
    transcript, assertion = case_input(
        "ordered_medication",
        changes={"dose_text": "null", "route_text": None, "frequency_text": None},
    )
    outcome = Mapper(intake, transcript, terminology, units).map_assertion(assertion, 0)
    assert outcome.resource["resourceType"] == "MedicationRequest"
    assert "dosageInstruction" not in outcome.resource
    assert "literal_null_normalised" in outcome.mapper_flags
    assert "literal_null_normalised" not in outcome.review_reasons
    assert "dosage_text_not_in_quote" not in outcome.review_reasons
    assert assertion.dose_text == "null"


@pytest.mark.parametrize("spoken_unit", [" mmHg", ""])
def test_exact_qwen_commentary_unit_never_parsed_as_unit(map_case, spoken_unit):
    _, assertion, outcome = map_case(
        "blood-pressure",
        changes={
            "value_text": "126 over 78",
            "unit_text": "mmHg (implied by context of blood pressure reading)",
        },
        quote=f"The cuff display for you reads blood pressure 126 over 78{spoken_unit}.",
    )
    assert outcome.consistency.unusable_fields == ("unit_text",)
    assert "field_contains_commentary" in outcome.review_reasons
    assert outcome.is_material
    assert "implied" in assertion.unit_text
    if spoken_unit:
        assert [c["valueQuantity"]["value"] for c in outcome.resource["component"]] == [126, 78]
        assert all(c["valueQuantity"]["code"] == "mm[Hg]" for c in outcome.resource["component"])
    else:
        assert outcome.resource is None
        assert "unit_missing" in outcome.review_reasons


def test_unit_recovery_uses_quote_and_stays_in_review(map_case):
    mapper, _, outcome = map_case(
        "body-weight",
        changes={"value_text": "70", "unit_text": "pounds (likely)"},
        quote="Your weight is 70 kilograms.",
    )
    assert outcome.resource["valueQuantity"]["code"] == "kg"
    checked = apply_validation(outcome, passing_run(mapper.intake, [outcome]))
    assert checked.export_state == "needs_review"


@pytest.mark.parametrize(
    "quote",
    [
        "Your weight is 70.",
        "Your weight is 70. Another reading was 80 kilograms.",
        "Your weight is 70 kilograms or 70 pounds.",
        "Your weight is 70 oz.",
    ],
)
def test_unit_recovery_refuses_absent_unrelated_ambiguous_or_unknown_units(map_case, quote):
    _, _, outcome = map_case(
        "body-weight", changes={"value_text": "70", "unit_text": "kg (implied)"}, quote=quote
    )
    assert outcome.resource is None
    assert "unit_missing" in outcome.review_reasons


def test_unusable_value_refuses_and_dose_is_omitted(map_case):
    _, _, vital = map_case("heart-rate", changes={"value_text": "72 (likely)"})
    assert vital.resource is None
    _, _, order = map_case(
        "new-order",
        changes={"dose_text": "500 mg (presumably)", "route_text": None, "frequency_text": None},
    )
    assert order.resource is not None and "dosageInstruction" not in order.resource
    assert "field_contains_commentary" in order.review_reasons


def test_null_time_and_bp_split_recovery_remain_informational(map_case):
    _, _, outcome = map_case(
        "blood-pressure",
        changes={"value_text": "126", "unit_text": "over 78", "stated_time_text": "unknown"},
        quote="Your blood pressure is 126 over 78.",
    )
    assert [c["valueQuantity"]["value"] for c in outcome.resource["component"]] == [126, 78]
    assert set(outcome.mapper_flags) == {"bp_parsed_from_value_and_unit", "literal_null_normalised"}
    assert not outcome.is_material
