"""Review cannot waive FAIL, stale results cannot export, reports label honestly."""

import json

import pytest
from pydantic import ValidationError
from validator_helpers import passing_run

from auris.fhir import build_collection
from auris.fhir.models import LaneState, MappingOutcome, resource_digest
from auris.validation.models import ValidatorIssue
from auris.validation.outcomes import classify
from auris.validation.report import apply_validation, honest_label, write_validation


def test_nonmaterial_waits_for_both_validator_lanes(map_case):
    mapper, _, outcome = map_case("heart-rate")
    assert outcome.export_state == "pending_validation"
    run = passing_run(mapper.intake, [outcome])
    checked = apply_validation(outcome, run)
    assert checked.export_state == "final_exportable"
    assert honest_label(checked.lanes) == "profile-checked, terminology NOT_CHECKED"
    assert len(build_collection(mapper.intake, [checked], validation=run)["entry"]) == 4
    assert len(build_collection(mapper.intake, [outcome], validation=run)["entry"]) == 3


@pytest.mark.parametrize(
    "lane", ["reduced_schema", "evidence", "base_r4", "us_core_profile", "terminology"]
)
def test_fail_is_unwaivable_even_after_review(map_case, lane):
    mapper, _, outcome = map_case("new-order")
    run = passing_run(mapper.intake, [outcome])
    checked = apply_validation(outcome, run)
    approved = checked.model_copy(
        update={"reviewed_resource_digest": resource_digest(checked.resource)}
    )
    assert approved.export_state == "final_exportable"
    failed = approved.model_copy(
        update={"lanes": approved.lanes.model_copy(update={lane: LaneState.FAIL})}
    )
    assert failed.export_state == "validation_failed"
    # A caller cannot override a derived export property, including model_copy.
    assert (
        failed.model_copy(update={"export_state": "final_exportable"}).export_state
        == "validation_failed"
    )
    with pytest.raises(ValueError, match="material assertion"):
        build_collection(mapper.intake, [failed], validation=run)
    assert len(build_collection(mapper.intake, [failed], preview=True)["entry"]) == 4


@pytest.mark.parametrize("lane", ["base_r4", "us_core_profile"])
def test_not_checked_cannot_be_approved_into_final_export(map_case, lane):
    mapper, _, outcome = map_case("new-order")
    checked = apply_validation(outcome, passing_run(mapper.intake, [outcome]))
    pending = checked.model_copy(
        update={
            "reviewed_resource_digest": resource_digest(checked.resource),
            "lanes": checked.lanes.model_copy(update={lane: LaneState.NOT_CHECKED}),
        }
    )
    assert pending.export_state == "pending_validation"


def test_export_state_cannot_be_deserialized_as_an_independent_decision(map_case):
    _, _, outcome = map_case("heart-rate")
    payload = outcome.model_dump()
    payload["export_state"] = "final_exportable"
    with pytest.raises(ValidationError, match="export_state"):
        MappingOutcome.model_validate(payload)


def test_edits_invalidate_validation_and_approval(map_case):
    mapper, _, outcome = map_case("new-order")
    run = passing_run(mapper.intake, [outcome])
    checked = apply_validation(outcome, run).model_copy(
        deep=True, update={"reviewed_resource_digest": resource_digest(outcome.resource)}
    )
    checked.resource["dosageInstruction"][0]["text"] = "Fictional edited dose text"
    assert checked.export_state == "needs_review"
    with pytest.raises(ValueError, match="changed since validation"):
        apply_validation(checked, run)
    rerun = passing_run(mapper.intake, [checked])
    assert apply_validation(checked, rerun).export_state == "needs_review"


def test_final_collection_requires_checked_intake_anchors(map_case):
    mapper, _, outcome = map_case("heart-rate")
    run = passing_run(mapper.intake, [outcome])
    checked = apply_validation(outcome, run)
    with pytest.raises(ValueError, match="intake anchors require validation"):
        build_collection(mapper.intake, [checked])
    failed_anchor = run.resources[0].model_copy(
        update={"base_r4": run.resources[0].base_r4.model_copy(update={"state": LaneState.FAIL})}
    )
    bad = run.model_copy(update={"resources": (failed_anchor, *run.resources[1:])})
    with pytest.raises(ValueError, match="intake anchors must pass"):
        build_collection(mapper.intake, [checked], validation=bad)


def test_report_has_all_lanes_reasons_raw_output_and_private_permissions(map_case, tmp_path):
    mapper, _, vital = map_case("heart-rate")
    _, _, medication = map_case("new-order")
    _, _, ledger = map_case("nka")
    run = passing_run(mapper.intake, [vital, medication])
    run = run.model_copy(update={"raw_output": '{"fictional":"raw output"}'})
    target = tmp_path / "private" / "validation.json"
    write_validation(target, mapper.intake, [vital, medication, ledger], run)
    document = json.loads(target.read_text())
    assert document["tier"] == 0
    assert document["validator"]["raw_output"] == run.raw_output
    assert len(document["resources"]) == 6
    for row in document["resources"]:
        assert set(row["lanes"]) == {
            "reduced_schema",
            "evidence",
            "base_r4",
            "us_core_profile",
            "terminology",
        }
        assert all("state" in lane and "issues" in lane for lane in row["lanes"].values())
        assert row["lanes"]["terminology"]["state"] == "NOT_CHECKED"
        assert row["label"] != "validated"
    assert document["resources"][-2]["export_state"] == "needs_review"
    assert "medication" in document["resources"][-2]["review_reasons"]
    assert document["resources"][-1]["export_state"] == "ledger_only"
    assert target.stat().st_mode & 0o777 == 0o600
    assert target.parent.stat().st_mode & 0o777 == 0o700


def test_failed_report_does_not_claim_profile_checked(map_case, tmp_path):
    mapper, _, outcome = map_case("heart-rate")
    run = passing_run(mapper.intake, [outcome])
    error = ValidatorIssue(severity="error", code="structure", details={"text": "fictional"})
    result = classify(outcome.resource, (error,), (error,))
    run = run.model_copy(update={"resources": (*run.resources[:3], result)})
    path = tmp_path / "private" / "validation.json"
    write_validation(path, mapper.intake, [outcome], run)
    row = json.loads(path.read_text())["resources"][-1]
    assert row["export_state"] == "validation_failed"
    assert row["label"] == "checks failed, terminology NOT_CHECKED"
    assert row["lanes"]["base_r4"]["issues"][0]["details"]["text"] == "fictional"


def test_validated_label_needs_all_five_lanes(map_case):
    mapper, _, outcome = map_case("heart-rate")
    checked = apply_validation(outcome, passing_run(mapper.intake, [outcome]))
    assert (
        honest_label(checked.lanes.model_copy(update={"terminology": LaneState.PASS}))
        == "validated"
    )
    assert (
        honest_label(checked.lanes.model_copy(update={"base_r4": LaneState.NOT_CHECKED}))
        != "validated"
    )
