"""Parse recorded official outputs and test checker scope, not severity routing."""

import json

import pytest
from validator_helpers import RECORDED, broken_resources, golden_resources

from auris.fhir.models import LaneState
from auris.validation.models import ValidatorIssue
from auris.validation.outcomes import classify, parse_issue, parse_outcomes


def test_recorded_golden_outcomes_parse_and_pass():
    parsed = parse_outcomes((RECORDED / "golden-outcomes.json").read_text())
    assert len(parsed) == 32
    for index, resource in enumerate(golden_resources()):
        result = classify(
            resource, parsed[f"{index:04d}-base.json"], parsed[f"{index:04d}-profile.json"]
        )
        assert result.base_r4.state == result.us_core_profile.state == LaneState.PASS
        assert result.terminology.state == LaneState.NOT_CHECKED
        assert result.base_r4.issues  # Best-practice warnings remain visible.
        assert all(i.severity == "information" for i in result.terminology.issues)


def test_real_broken_outcomes_classify_and_preserve_message_ids():
    parsed = parse_outcomes((RECORDED / "broken-outcomes.json").read_text())
    results = [
        classify(resource, parsed[f"{index:04d}-base.json"], parsed[f"{index:04d}-profile.json"])
        for index, resource in enumerate(broken_resources())
    ]
    allergy, bp_root, bp_slice, medication, condition, vital = results
    assert allergy.base_r4.state == LaneState.FAIL
    assert allergy.us_core_profile.state == LaneState.PASS
    invariant = next(i for i in allergy.base_r4.issues if i.severity == "error")
    assert invariant.message_id.endswith("AllergyIntolerance#ait-1")
    assert invariant.code == "invariant" and invariant.expression == ("AllergyIntolerance",)
    assert invariant.text.startswith("Constraint failed: ait-1:")
    assert invariant.source == "InstanceValidator"
    assert medication.base_r4.state == LaneState.FAIL
    assert condition.base_r4.state == LaneState.PASS
    assert condition.us_core_profile.state == LaneState.FAIL
    # Official R4 auto-applies vital profiles by LOINC even without meta.profile.
    for result in (bp_root, bp_slice, vital):
        assert result.base_r4.state == result.us_core_profile.state == LaneState.FAIL


def test_parse_keeps_details_diagnostics_and_location_without_extension():
    raw = {
        "severity": "error",
        "code": "structure",
        "details": {"text": "fictional"},
        "diagnostics": "detail",
        "expression": ["Observation.value"],
        "location": ["Observation"],
    }
    issue = parse_issue(raw)
    assert issue.details == raw["details"] and issue.text == "fictional"
    assert issue.diagnostics == "detail" and issue.location == ("Observation",)
    assert issue.message_id is None


def test_diff_is_multiset_and_keeps_severity_changes_and_new_locations():
    issue = ValidatorIssue(
        severity="warning",
        code="structure",
        details={"text": "fictional"},
        expression=("Observation",),
        message_id="fictional",
    )
    error = issue.model_copy(update={"severity": "error"})
    changed = issue.model_copy(update={"expression": ("Observation.value",)})
    result = classify(golden_resources()[0], (issue,), (issue, issue, error, changed))
    assert result.base_r4.state == LaneState.PASS
    assert result.us_core_profile.state == LaneState.FAIL
    assert result.us_core_profile.issues == (issue, error, changed)


def test_terminology_errors_cannot_pass_or_fail_tier_zero_terminology_lane():
    issue = ValidatorIssue(severity="fatal", code="code-invalid", details={"text": "fictional"})
    result = classify(golden_resources()[0], (issue,), (issue,))
    assert result.base_r4.state == result.us_core_profile.state == LaneState.PASS
    assert result.terminology.state == LaneState.NOT_CHECKED
    assert result.terminology.issues[0].original_severity == "fatal"
    assert result.terminology.issues[0].severity == "information"


@pytest.mark.parametrize(
    "severity,state",
    [("error", "FAIL"), ("fatal", "FAIL"), ("warning", "PASS"), ("information", "PASS")],
)
def test_base_issue_does_not_fail_profile_lane(severity, state):
    issue = ValidatorIssue(severity=severity, code="structure")
    result = classify(golden_resources()[0], (issue,), (issue,))
    assert result.base_r4.state == state
    assert result.us_core_profile.state == LaneState.PASS


def test_no_profile_and_failure_are_not_checked():
    resource = golden_resources()[0]
    resource.pop("meta")
    result = classify(resource, (), ())
    assert result.us_core_profile.state == LaneState.NOT_CHECKED
    result = classify(resource, (), (), failure="Java unavailable")
    assert result.base_r4.state == result.us_core_profile.state == LaneState.NOT_CHECKED
    assert result.base_r4.reason == "Java unavailable"


def test_unresolved_profile_warning_is_not_a_successful_check():
    issue = ValidatorIssue(
        severity="warning", code="not-found", message_id="VALIDATION_VAL_PROFILE_UNKNOWN_NOT_POLICY"
    )
    result = classify(golden_resources()[0], (), (issue,))
    assert result.base_r4.state == LaneState.PASS
    assert result.us_core_profile.state == LaneState.NOT_CHECKED
    assert result.us_core_profile.issues == (issue,)


def test_parser_rejects_missing_duplicate_and_non_outcome_entries():
    raw = json.loads((RECORDED / "golden-outcomes.json").read_text())
    outcome = raw["entry"][0]["resource"]
    assert len(parse_outcomes(json.dumps(outcome))) == 1
    for document in (
        [],
        {"resourceType": "Patient"},
        {"resourceType": "Bundle", "entry": []},
        {"resourceType": "Bundle", "entry": [raw["entry"][0], raw["entry"][0]]},
        {"resourceType": "OperationOutcome", "issue": []},
    ):
        with pytest.raises(ValueError):
            parse_outcomes(json.dumps(document))
