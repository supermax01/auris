"""A3 exact golden outcomes and refusal cases. Every visit is fictional."""

import copy
import json
from pathlib import Path
from uuid import UUID

import pytest
from checklist_assertions import check_resource
from fhir_helpers import BY_NAME, GOLDENS
from validator_helpers import passing_run

from auris.assertions import ExtractionResult
from auris.evidence import align_assertion_evidence
from auris.fhir import Mapper, build_collection, bundle_bytes, write_bundle
from auris.fhir.models import LaneState
from auris.intake import Intake
from auris.transcript import Transcript
from auris.validation.report import apply_validation


@pytest.mark.parametrize("case", GOLDENS, ids=lambda case: case["name"])
def test_a3_exact_golden_outcome(case, map_case):
    _, _, outcome = map_case(case["name"])
    assert outcome.model_dump(mode="json") == case["expected"]


@pytest.mark.parametrize(
    "name,changes,reason",
    [
        ("blood-pressure", {"value_text": "128"}, "bp_component_missing"),
        ("blood-pressure", {"value_text": "over 82"}, "bp_component_missing"),
        ("blood-pressure", {"value_text": None}, "bp_component_missing"),
        ("blood-pressure", {"unit_text": "kPa"}, "unit_not_in_profile_set"),
        ("heart-rate", {"unit_text": "kg"}, "unit_not_in_profile_set"),
        ("body-temperature", {"unit_text": None}, "unit_missing"),
        ("body-weight", {"unit_text": None}, "unit_missing"),
        ("body-height", {"unit_text": None}, "unit_missing"),
        ("body-temperature", {"unit_text": "K"}, "unit_not_in_profile_set"),
        ("body-weight", {"unit_text": "oz"}, "unit_not_in_profile_set"),
        ("body-height", {"unit_text": "m"}, "unit_not_in_profile_set"),
        ("heart-rate", {"temporality": "past"}, "historical_without_date"),
        ("heart-rate", {"source_speaker_role": "patient"}, "historical_without_date"),
        (
            "heart-rate",
            {"temporality": "past", "stated_time_text": "2026-11"},
            "historical_without_date",
        ),
        (
            "heart-rate",
            {"temporality": "past", "stated_time_text": "yesterday"},
            "historical_without_date",
        ),
        ("heart-rate", {"stated_time_text": "2026-02-30"}, "measurement_date_not_day_precision"),
        ("heart-rate", {"stated_time_text": "11/12/2026"}, "measurement_date_not_day_precision"),
        ("heart-rate", {"temporality": "future"}, "vital_temporality_unclear"),
        ("heart-rate", {"temporality": None}, "vital_temporality_unclear"),
        ("heart-rate", {"value_text": "seventy-ish"}, "value_missing_or_unparseable"),
        ("heart-rate", {"value_text": "72 73"}, "value_missing_or_unparseable"),
        ("heart-rate", {"value_text": "NaN"}, "value_missing_or_unparseable"),
        ("heart-rate", {"value_text": None}, "value_missing_or_unparseable"),
        ("heart-rate", {"polarity": "negated"}, "polarity_kind_mismatch"),
        ("new-order", {"source_speaker_role": "patient"}, "prescriber_not_identified"),
        ("new-order", {"source_speaker_role": None}, "prescriber_not_identified"),
        ("new-order", {"medication_intent": "discussed"}, "not_explicit_new_order"),
        ("new-order", {"medication_intent": "continuing"}, "not_explicit_new_order"),
        ("new-order", {"medication_intent": None}, "not_explicit_new_order"),
        ("new-order", {"temporality": "past"}, "order_temporality_unclear"),
        ("new-order", {"polarity": "negated"}, "polarity_kind_mismatch"),
        ("new-order", {"polarity": None}, "polarity_kind_mismatch"),
        (
            "clinician_assessed_problem",
            {"source_speaker_role": "patient"},
            "clinician_not_identified",
        ),
        (
            "clinician_ruled_out_problem",
            {"source_speaker_role": "unknown"},
            "clinician_not_identified",
        ),
        ("clinician_assessed_problem", {"polarity": "negated"}, "polarity_kind_mismatch"),
        ("clinician_ruled_out_problem", {"polarity": "affirmed"}, "polarity_kind_mismatch"),
        (
            "patient_reported_condition",
            {"experiencer": "family_member"},
            "patient_experiencer_required",
        ),
        ("allergy-ingredient", {"allergen_text": None}, "allergen_not_stated"),
        ("allergy-ingredient", {"polarity": None}, "polarity_kind_mismatch"),
    ],
)
def test_resource_refusals_remain_review_entries(map_case, name, changes, reason):
    mapper, assertion, outcome = map_case(name, changes=changes)
    assert outcome.resource is None
    assert outcome.ledger == assertion.model_dump(mode="json")
    assert reason in outcome.review_reasons
    assert outcome.export_state == (
        "validation_failed" if outcome.consistency.failures else "needs_review"
    )
    if outcome.is_material:
        with pytest.raises(ValueError, match="material assertion requires review"):
            build_collection(mapper.intake, [outcome])
    else:
        assert (
            len(
                build_collection(mapper.intake, [outcome], validation=passing_run(mapper.intake))[
                    "entry"
                ]
            )
            == 3
        )
    assert "dataAbsentReason" not in outcome.model_dump_json()


@pytest.mark.parametrize(
    "name",
    [
        "heart-rate",
        "clinician_assessed_problem",
        "clinician_ruled_out_problem",
        "patient_reported_condition",
        "new-order",
        "allergy-ingredient",
    ],
)
@pytest.mark.parametrize("experiencer", ["other", "family_member", "unknown", None])
def test_never_attach_nonpatient_or_unknown_experiencer_to_intake_patient(
    map_case, name, experiencer
):
    _, _, outcome = map_case(name, changes={"experiencer": experiencer})
    assert outcome.resource is None
    assert "patient_experiencer_required" in outcome.review_reasons
    assert outcome.export_state == "needs_review"


@pytest.mark.parametrize(
    "name",
    [
        "heart-rate",
        "clinician_assessed_problem",
        "clinician_ruled_out_problem",
        "patient_reported_condition",
        "new-order",
        "allergy-ingredient",
    ],
)
def test_missing_concept_never_creates_empty_code(map_case, name):
    _, _, outcome = map_case(name, changes={"concept_text": None})
    assert outcome.resource is None and "concept_text_missing" in outcome.review_reasons


@pytest.mark.parametrize(
    "name,unit,value,expected",
    [
        ("body-temperature", "degrees Fahrenheit", "98.6", "[degF]"),
        ("body-temperature", "Cel", "37.2", "Cel"),
        ("body-weight", "grams", "2200", "g"),
        ("body-weight", "pounds", "154", "[lb_av]"),
        ("body-weight", "kg", "70", "kg"),
        ("body-height", "inches", "67.5", "[in_i]"),
        ("body-height", "cm", "172", "cm"),
        ("heart-rate", "beats per minute", "72", "/min"),
        ("respiratory-rate", "breaths per minute", "16", "/min"),
        ("pulse-oximetry", "percent", "98", "%"),
    ],
)
def test_supported_units_are_not_converted(map_case, name, unit, value, expected):
    _, _, outcome = map_case(
        name,
        changes={"unit_text": unit, "value_text": value},
        quote=f"{BY_NAME[name]['assertion']['concept_text']} is {value} {unit}.",
    )
    assert outcome.resource["valueQuantity"] == {
        "value": float(value),
        "unit": expected,
        "system": "http://unitsofmeasure.org",
        "code": expected,
    }
    assert outcome.export_state == "pending_validation"


@pytest.mark.parametrize(
    "date,expected",
    [
        ("2026-11-01", "2026-11-01"),
        ("November 1, 2026", "2026-11-01"),
        ("2026-11-01T10:00:00Z", "2026-11-01T10:00:00Z"),
    ],
)
def test_historical_vital_uses_stated_day_without_inventing_time(map_case, date, expected):
    _, _, outcome = map_case(
        "heart-rate",
        changes={"temporality": "past", "stated_time_text": date},
        quote=f"Heart rate was 72 on {date}.",
    )
    assert outcome.resource["effectiveDateTime"] == expected
    assert outcome.provenance_notes == ()


def test_current_reported_vital_needs_date_and_uses_it(map_case):
    transcript = copy.deepcopy(BY_NAME["heart-rate"]["transcript"])
    transcript["segments"][0]["role"] = "patient"
    _, _, outcome = map_case(
        "heart-rate",
        transcript=transcript,
        changes={"source_speaker_role": "patient", "stated_time_text": "2026-11-12"},
    )
    assert outcome.resource["effectiveDateTime"] == "2026-11-12"
    assert outcome.export_state == "pending_validation"


def test_low_precision_intake_time_cannot_supply_vital_date(map_case, intake):
    data = intake.model_dump(mode="json", by_alias=True)
    data["encounter"]["period"] = {"start": "2026-11"}
    _, _, outcome = map_case("heart-rate", intake=Intake.model_validate(data))
    assert outcome.resource is None
    assert "measurement_date_not_day_precision" in outcome.review_reasons


@pytest.mark.parametrize(
    "concept",
    ["oxygen flow rate", "oxygen concentration", "oxygen flow", "unsupported fictional vital"],
)
def test_oxygen_supplements_and_unsupported_vitals_go_to_ledger(map_case, concept):
    _, _, outcome = map_case(
        "pulse-oximetry", changes={"concept_text": concept}, quote=f"{concept} is 98 percent."
    )
    assert outcome.resource is None and outcome.export_state == "ledger_only"
    assert "vital_not_supported" in outcome.review_reasons


@pytest.mark.parametrize("name", ["clinician_assessed_problem", "patient_reported_condition"])
@pytest.mark.parametrize(
    "temporality,status",
    [
        ("current", "active"),
        ("resolved", "resolved"),
        ("past", None),
        ("unclear", None),
        (None, None),
        ("future", None),
    ],
)
def test_condition_status_from_explicit_temporality_only(map_case, name, temporality, status):
    _, _, outcome = map_case(name, changes={"temporality": temporality})
    if status:
        assert outcome.resource["clinicalStatus"]["coding"][0]["code"] == status
    else:
        assert "clinicalStatus" not in outcome.resource
    assert "onsetDateTime" not in outcome.resource and "abatementDateTime" not in outcome.resource
    assert outcome.export_state == (
        "pending_validation" if temporality == "current" else "needs_review"
    )


@pytest.mark.parametrize("temporality", ["current", "past", "resolved", None])
def test_refutation_never_has_clinical_status(map_case, temporality):
    _, _, outcome = map_case("clinician_ruled_out_problem", changes={"temporality": temporality})
    assert "clinicalStatus" not in outcome.resource
    assert outcome.resource["verificationStatus"]["coding"][0]["code"] == "refuted"
    assert outcome.export_state == "needs_review"


@pytest.mark.parametrize(
    "name,changes,field",
    [
        ("clinician_assessed_problem", {"concept_text": "acute bronchitis"}, "code"),
        ("patient_reported_condition", {"concept_text": "Cholera"}, "code"),
        ("new-order", {"concept_text": "amoxicillin 500"}, "medicationCodeableConcept"),
        ("new-order", {"concept_text": "penicillin"}, "medicationCodeableConcept"),
        ("new-order", {"concept_text": "co-amoxiclav"}, "medicationCodeableConcept"),
        ("allergy-ingredient", {"allergen_text": "sulfa"}, "code"),
        ("allergy-ingredient", {"allergen_text": "peanuts"}, "code"),
    ],
)
def test_uncoded_text_is_preserved_and_requires_review(map_case, name, changes, field):
    stated = next(iter(changes.values()))
    original = BY_NAME[name]["assertion"]["concept_text"]
    # These are positive coding tests: the changed clinical words must be stated.
    quote = f"{original}; {stated} 500 milligrams."
    _, _, outcome = map_case(name, changes=changes, quote=quote)
    assert outcome.resource[field] == {"text": next(iter(changes.values()))}
    assert outcome.mapper_flags == ("uncoded_text_only",)
    assert "needs_coding_review" in outcome.review_reasons
    assert outcome.export_state == "needs_review"


def test_order_synonym_and_absent_dose(map_case):
    _, _, outcome = map_case(
        "new-order",
        changes={"concept_text": "paracetamol", "dose_text": None},
        quote="I will prescribe paracetamol.",
    )
    assert outcome.resource["medicationCodeableConcept"]["coding"][0]["code"] == "161"
    assert outcome.resource["medicationCodeableConcept"]["text"] == "paracetamol"
    assert "dosageInstruction" not in outcome.resource


def test_dose_is_verbatim_without_synthetic_route_or_frequency(map_case):
    dose = "  500 milligrams,\nthree times a day  "
    _, _, outcome = map_case(
        "new-order",
        changes={
            "dose_text": dose,
            "route_text": "by mouth",
            "frequency_text": "three times a day",
        },
    )
    assert outcome.resource["dosageInstruction"] == [{"text": dose}]


def test_multiple_clinician_clusters_refuse_to_choose_intake_prescriber(map_case):
    transcript = copy.deepcopy(BY_NAME["new-order"]["transcript"])
    transcript["segments"].append(
        {
            **transcript["segments"][0],
            "id": "s2",
            "speaker_cluster_id": "another-fictional-clinician",
            "start_seconds": 6,
            "end_seconds": 10,
        }
    )
    _, _, outcome = map_case("new-order", transcript=transcript)
    assert outcome.resource is None and "prescriber_not_identified" in outcome.review_reasons


@pytest.mark.parametrize(
    "name", ["new-order", "clinician_assessed_problem", "clinician_ruled_out_problem"]
)
def test_claimed_clinician_must_be_in_cited_transcript(map_case, name):
    transcript = copy.deepcopy(BY_NAME[name]["transcript"])
    transcript["segments"][0]["role"] = "patient"
    _, _, outcome = map_case(name, transcript=transcript)
    assert outcome.resource is None
    assert "source_role_mismatch" in outcome.review_reasons


def test_allergy_without_manifestation_has_no_reaction_or_type(map_case):
    _, _, outcome = map_case("allergy-ingredient", changes={"reaction_manifestation_text": None})
    assert "reaction" not in outcome.resource and "type" not in outcome.resource


@pytest.mark.parametrize(
    "temporality,resolution", [("past", None), ("resolved", "outgrown"), ("current", "resolved")]
)
def test_historical_and_resolved_allergy_stays_active_unconfirmed_pending_review(
    map_case, temporality, resolution
):
    _, _, outcome = map_case(
        "allergy-ingredient",
        changes={"temporality": temporality, "stated_resolution_text": resolution},
    )
    assert outcome.resource["clinicalStatus"]["coding"][0]["code"] == "active"
    assert outcome.resource["verificationStatus"]["coding"][0]["code"] == "unconfirmed"
    assert outcome.export_state == "needs_review"
    if resolution:
        assert "allergy_resolution_stated" in outcome.review_reasons


def test_negated_allergy_statement_is_never_positive_allergy(map_case):
    _, _, outcome = map_case(
        "allergy-ingredient",
        changes={"polarity": "negated"},
        quote="I am not allergic to amoxicillin.",
    )
    assert outcome.resource is None and outcome.export_state == "ledger_only"
    assert "snomed_not_supplied" in outcome.review_reasons


def test_contradictory_denial_kind_is_flagged(map_case):
    _, _, outcome = map_case("nka", changes={"polarity": "affirmed"})
    assert outcome.resource is None and "polarity_kind_mismatch" in outcome.review_reasons


def test_icd_release_outside_encounter_period_stays_text_only(map_case, intake):
    data = intake.model_dump(mode="json", by_alias=True)
    data["encounter"]["period"] = {"start": "2026-09-30"}
    _, _, outcome = map_case("clinician_assessed_problem", intake=Intake.model_validate(data))
    assert "coding" not in outcome.resource["code"]
    assert "terminology_release_out_of_period" in outcome.review_reasons


@pytest.mark.parametrize(
    "name",
    [
        "heart-rate",
        "clinician_assessed_problem",
        "clinician_ruled_out_problem",
        "patient_reported_condition",
        "new-order",
        "allergy-ingredient",
        "symptom-affirmed",
        "reported-medication-affirmed",
        "nka",
    ],
)
def test_failed_evidence_never_final_exports(map_case, name):
    _, _, outcome = map_case(
        name, changes={"evidence": [{"segment_id": "s1", "quote": "Invented words."}]}
    )
    assert outcome.lanes.evidence == LaneState.FAIL
    assert "evidence_failed" in outcome.review_reasons
    assert outcome.export_state != "final_exportable"


def test_evidence_is_external_and_cannot_be_reused_for_another_assertion(map_case):
    mapper, assertion, outcome = map_case("heart-rate")
    evidence = align_assertion_evidence(assertion, mapper.transcript)
    assert mapper.map_assertion(assertion, 0, evidence) == outcome
    assert "evidence" not in outcome.resource
    _, other, _ = map_case("blood-pressure")
    with pytest.raises(ValueError, match="does not belong"):
        mapper.map_assertion(other, 0, evidence)
    with pytest.raises(ValueError, match="non-negative"):
        mapper.map_assertion(assertion, -1)


def test_material_is_not_the_only_final_export_gate(map_case):
    _, _, outcome = map_case("patient_reported_condition", changes={"temporality": "unclear"})
    assert not outcome.is_material and outcome.review_reasons
    assert outcome.export_state == "needs_review"


def test_deterministic_ids_and_bundle_bytes_and_private_write(map_case, tmp_path):
    mapper, assertion, outcome = map_case("heart-rate")
    assert UUID(outcome.id).version == 5
    assert mapper.map_assertion(assertion, 0).id == outcome.id
    assert mapper.map_assertion(assertion, 1).id != outcome.id
    assert mapper.map_all((assertion,))[0] == outcome
    original_intake = mapper.intake.model_dump()
    original_resource = copy.deepcopy(outcome.resource)
    run = passing_run(mapper.intake, [outcome])
    outcome = apply_validation(outcome, run)
    bundle = build_collection(mapper.intake, [outcome], validation=run)
    assert bundle_bytes(bundle) == bundle_bytes(
        build_collection(mapper.intake, [outcome], validation=run)
    )
    assert mapper.intake.model_dump() == original_intake and outcome.resource == original_resource
    urls = {entry["fullUrl"] for entry in bundle["entry"]}
    assert all(url.startswith("urn:uuid:") for url in urls)
    for entry in bundle["entry"]:
        resource = entry["resource"]
        for key in ("subject", "encounter"):
            if key in resource:
                assert resource[key]["reference"] in urls
    assert len(bundle["entry"]) == 4 and bundle["type"] == "collection"
    path = tmp_path / "private" / "bundle.json"
    write_bundle(path, bundle)
    assert path.read_bytes() == bundle_bytes(bundle)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_changed_transcript_changes_resource_id(map_case):
    _, _, first = map_case("heart-rate")
    _, _, second = map_case("heart-rate", quote="Heart rate is 72.")
    assert first.id != second.id


def test_collection_filters_review_and_ledger_and_future_failed_lanes(map_case):
    mapper, _, vital = map_case("heart-rate")
    _, _, medication = map_case("new-order")
    _, _, denial = map_case("nka")
    with pytest.raises(ValueError, match="material assertion requires review"):
        build_collection(mapper.intake, [vital, medication, denial])
    run = passing_run(mapper.intake, [vital])
    vital = apply_validation(vital, run)
    assert len(build_collection(mapper.intake, [vital, denial], validation=run)["entry"]) == 4
    assert (
        len(build_collection(mapper.intake, [vital, medication, denial], preview=True)["entry"])
        == 5
    )
    failed = vital.model_copy(
        update={"lanes": vital.lanes.model_copy(update={"base_r4": LaneState.FAIL})}
    )
    assert len(build_collection(mapper.intake, [failed], validation=run)["entry"]) == 3
    with pytest.raises(ValueError, match="duplicate"):
        build_collection(mapper.intake, [vital, vital], validation=run)


CONTRASTIVE = json.loads((Path(__file__).parent / "fixtures/contrastive_cases.json").read_text())[
    "cases"
]


@pytest.mark.parametrize("case", CONTRASTIVE, ids=lambda c: c["name"])
def test_step_2a_contrastive_cases_end_to_end(case, terminology, units, intake, request):
    transcript = Transcript.model_validate(case["transcript"])
    assertions = ExtractionResult.model_validate({"assertions": case["assertions"]}).assertions
    outcomes = Mapper(intake, transcript, terminology, units).map_all(assertions)
    for assertion, outcome, material in zip(
        assertions, outcomes, case["expected_material"], strict=True
    ):
        request.node.user_properties.append(("a3_row", assertion.kind))
        assert outcome.is_material == material
        assert outcome.evidence.lane.value == "PASS"
        if outcome.resource:
            check_resource(outcome.resource)
        if material:
            assert outcome.export_state != "final_exportable"
        if (
            assertion.kind in ("allergy_denial", "reported_medication", "symptom")
            or assertion.experiencer != "patient"
        ):
            assert outcome.resource is None
        if outcome.resource and assertion.kind == "explicit_new_order":
            assert (
                outcome.resource["status"] == "draft" and outcome.resource["intent"] == "proposal"
            )
        if outcome.resource and assertion.kind == "clinician_ruled_out_problem":
            assert outcome.resource["verificationStatus"]["coding"][0]["code"] == "refuted"
            assert "clinicalStatus" not in outcome.resource


@pytest.mark.parametrize("polarity", ["affirmed", "negated", None])
@pytest.mark.parametrize(
    "intent", ["new_order", "continuing", "historical", "discussed", "unclear", None]
)
def test_reported_medication_row_never_becomes_an_order(map_case, polarity, intent):
    _, _, outcome = map_case(
        "reported-medication-affirmed", changes={"polarity": polarity, "medication_intent": intent}
    )
    assert outcome.resource is None
    assert outcome.export_state == ("needs_review" if polarity == "negated" else "ledger_only")
    assert outcome.is_material and "medication" in outcome.review_reasons


def test_failed_material_ledger_blocks_final_collection(map_case):
    mapper, _, outcome = map_case(
        "nka", changes={"evidence": [{"segment_id": "missing", "quote": "No allergies."}]}
    )
    with pytest.raises(ValueError, match="material assertion requires review"):
        build_collection(mapper.intake, [outcome])
    assert len(build_collection(mapper.intake, [outcome], preview=True)["entry"]) == 3


def test_resolved_allergy_enum_alone_requires_resolution_review(map_case):
    _, _, outcome = map_case("allergy-ingredient", changes={"temporality": "resolved"})
    assert "allergy_resolution_stated" in outcome.review_reasons
    assert outcome.resource["clinicalStatus"]["coding"][0]["code"] == "active"


def test_numeric_parse_refuses_precision_loss(map_case):
    _, _, outcome = map_case("body-temperature", changes={"value_text": "37.1234567890123456789"})
    assert outcome.resource is None and "value_missing_or_unparseable" in outcome.review_reasons


def test_all_vital_unit_sets_are_checked_at_use_time(map_case, units):
    mapper, assertion, _ = map_case("body-temperature")
    mapper.unit_sets = {**units, "ucum-bodytemp": frozenset({"[degF]"})}
    outcome = mapper.map_assertion(assertion, 0)
    assert outcome.resource is None
    assert "unit_not_in_profile_set" in outcome.review_reasons
