"""A3/A4 review and contrastive evidence tests. All cases are fictional."""

import json
from pathlib import Path
from typing import Any

import pytest

from auris.assertions import AssertionKind, parse_assertion
from auris.evidence import EvidenceLane, align_assertion_evidence
from auris.review import ReviewReason, is_material, material_reasons, review_reasons
from auris.transcript import Transcript

FIXTURE = Path(__file__).parent / "fixtures" / "contrastive_cases.json"
FIXTURE_DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = FIXTURE_DATA["cases"]


def test_fixture_is_fictional_and_covers_all_contrasts_and_kinds() -> None:
    assert FIXTURE_DATA["fictional"] is True
    assert {case["contrast"] for case in CASES} >= {
        "negated vs affirmed allergy",
        "family-member vs patient condition",
        "ruled-out vs assessed problem",
        "discussed vs ordered medicine",
        "historical vs current condition",
    }
    assert {assertion["kind"] for case in CASES for assertion in case["assertions"]} == {
        kind.value for kind in AssertionKind
    }
    assert len({case["name"] for case in CASES}) == len(CASES)


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_contrastive_case_alignment_and_review(case: dict[str, Any]) -> None:
    transcript = Transcript.model_validate(case["transcript"])
    assert case["assertions"]  # An empty case must not pass vacuously.
    for data, expected_material, expected_reasons, expected_review in zip(
        case["assertions"],
        case["expected_material"],
        case["expected_material_reasons"],
        case["expected_review_reasons"],
        strict=True,
    ):
        assertion = parse_assertion(data)
        assert assertion.model_dump(mode="json") == data
        result = align_assertion_evidence(assertion, transcript)
        assert result.lane is EvidenceLane.PASS
        assert result.label == "transcript-supported"
        assert result.failures == ()
        assert len(result.items) == len(assertion.evidence)
        for item in result.items:
            assert item.aligned is not None
            segment = transcript.segment_by_id(item.segment_id)
            assert segment is not None
            span = item.aligned
            assert segment.text[span.char_start : span.char_end] == span.aligned_text
            assert span.char_start < span.char_end
        assert is_material(assertion) is expected_material
        assert [reason.value for reason in material_reasons(assertion)] == expected_reasons
        assert [reason.value for reason in review_reasons(assertion)] == expected_review


def _data(**overrides: Any) -> dict[str, Any]:
    return {
        "kind": "symptom",
        "concept_text": "cough",
        "polarity": "affirmed",
        "experiencer": "patient",
        "temporality": "current",
        "source_speaker_role": "patient",
        "evidence": [{"segment_id": "s1", "quote": "I have a cough"}],
        **overrides,
    }


@pytest.mark.parametrize("experiencer", ["family_member", "other"])
def test_non_patient_experiencer_alone_is_material(experiencer: str) -> None:
    assertion = parse_assertion(_data(experiencer=experiencer))
    assert is_material(assertion)
    assert material_reasons(assertion) == (ReviewReason.NON_PATIENT_EXPERIENCER,)


@pytest.mark.parametrize("experiencer", [None, "unknown"])
def test_abstaining_on_experiencer_requires_review(experiencer: str | None) -> None:
    assertion = parse_assertion(_data(experiencer=experiencer))
    assert not is_material(assertion)
    assert review_reasons(assertion) == (ReviewReason.EXPERIENCER_UNKNOWN,)


def test_negation_alone_is_material() -> None:
    assertion = parse_assertion(_data(polarity="negated"))
    assert is_material(assertion)
    assert material_reasons(assertion) == (ReviewReason.NEGATED,)


def test_refuted_kind_remains_material_when_polarity_is_missing() -> None:
    assertion = parse_assertion(_data(kind="clinician_ruled_out_problem", polarity=None))
    assert is_material(assertion)
    assert review_reasons(assertion) == (ReviewReason.REFUTED, ReviewReason.POLARITY_NOT_STATED)


@pytest.mark.parametrize("kind", ["explicit_new_order", "reported_medication"])
@pytest.mark.parametrize(
    "intent", ["new_order", "continuing", "historical", "discussed", "unclear", None]
)
def test_every_medication_intent_is_material(kind: str, intent: str | None) -> None:
    assertion = parse_assertion(
        _data(
            kind=kind,
            medication_intent=intent,
            dose_text=None,
            route_text=None,
            frequency_text=None,
        )
    )
    assert is_material(assertion)
    assert material_reasons(assertion) == (ReviewReason.MEDICATION,)


@pytest.mark.parametrize("kind", ["allergy_statement", "allergy_denial"])
def test_allergy_kind_is_material_without_polarity(kind: str) -> None:
    data = _data(kind=kind, polarity=None, allergen_text=None)
    if kind == "allergy_statement":
        data.update(reaction_manifestation_text=None, stated_resolution_text=None)
    assertion = parse_assertion(data)
    assert is_material(assertion)
    assert material_reasons(assertion) == (ReviewReason.ALLERGY,)


def test_multiple_material_reasons_are_preserved_in_stable_order() -> None:
    assertion = parse_assertion(
        _data(
            kind="allergy_denial",
            allergen_text="penicillin",
            polarity="negated",
            experiencer="other",
        )
    )
    assert material_reasons(assertion) == (
        ReviewReason.ALLERGY,
        ReviewReason.NEGATED,
        ReviewReason.NON_PATIENT_EXPERIENCER,
    )


def test_null_common_fields_all_get_review_reasons() -> None:
    assertion = parse_assertion(
        _data(
            concept_text=None,
            polarity=None,
            experiencer=None,
            source_speaker_role=None,
        )
    )
    assert not is_material(assertion)
    assert review_reasons(assertion) == (
        ReviewReason.POLARITY_NOT_STATED,
        ReviewReason.EXPERIENCER_UNKNOWN,
        ReviewReason.CONCEPT_TEXT_MISSING,
        ReviewReason.SOURCE_ROLE_UNKNOWN,
    )


@pytest.mark.parametrize("kind", ["clinician_assessed_problem", "patient_reported_condition"])
@pytest.mark.parametrize("temporality", ["past", "unclear", None])
def test_historical_or_unclear_condition_requires_review(
    kind: str, temporality: str | None
) -> None:
    assertion = parse_assertion(_data(kind=kind, temporality=temporality))
    assert not is_material(assertion)
    expected = (
        ReviewReason.HISTORICAL_CONDITION
        if temporality == "past"
        else ReviewReason.CONDITION_TEMPORALITY_UNCLEAR
    )
    assert review_reasons(assertion) == (expected,)


def test_stated_allergy_resolution_requires_review() -> None:
    assertion = parse_assertion(
        _data(
            kind="allergy_statement",
            allergen_text="penicillin",
            reaction_manifestation_text=None,
            stated_resolution_text="I outgrew it",
        )
    )
    assert review_reasons(assertion) == (
        ReviewReason.ALLERGY,
        ReviewReason.ALLERGY_RESOLUTION_STATED,
    )
