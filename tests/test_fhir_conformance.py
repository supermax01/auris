"""Independent checklist walker: conditional parents, choices, and named slices.

This checks the task-zero contract, not FHIRPath invariants or terminology
bindings. The official R4/US Core validator remains a later lane.
"""

import copy
import hashlib

import pytest
from checklist_assertions import PROFILES, check_resource
from fhir_helpers import GOLDENS
from helpers import make_fake_package_bytes
from terminology_helpers import FIXTURES

from auris.fhir.common import build_anchors
from auris.fhir.contracts import load_unit_sets
from auris.intake import Intake


@pytest.mark.parametrize("case", GOLDENS, ids=lambda case: case["name"])
def test_every_built_resource_satisfies_required_fixed_and_pattern_contract(case, map_case):
    mapper, _, outcome = map_case(case["name"])
    anchors = mapper.anchors
    for resource in (anchors.patient, anchors.practitioner, anchors.encounter, outcome.resource):
        if resource:
            check_resource(resource)


@pytest.mark.parametrize(
    "path", ["status", "subject", "category", "effectiveDateTime", "component"]
)
def test_generic_checker_catches_missing_required_elements(map_case, path):
    _, _, outcome = map_case("blood-pressure")
    resource = copy.deepcopy(outcome.resource)
    resource.pop(path)
    with pytest.raises(AssertionError):
        check_resource(resource)


def test_generic_checker_catches_missing_and_wrong_bp_slices(map_case):
    _, _, outcome = map_case("blood-pressure")
    for mutate in (
        lambda r: r["component"].pop(),
        lambda r: r["component"][0]["code"]["coding"][0].update(code="wrong"),
        lambda r: r["component"][1]["valueQuantity"].pop("system"),
    ):
        resource = copy.deepcopy(outcome.resource)
        mutate(resource)
        with pytest.raises(AssertionError):
            check_resource(resource)


def test_profile_change_breaks_generic_conformance(map_case):
    _, _, outcome = map_case("heart-rate")
    profile = copy.deepcopy(PROFILES[outcome.claimed_profile])
    next(e for e in profile["elements"] if e["id"] == "Observation.value[x]:valueQuantity.code")[
        "fixed"
    ]["value"] = "changed"
    with pytest.raises(AssertionError):
        check_resource(outcome.resource, profile)
    profile = copy.deepcopy(PROFILES[outcome.claimed_profile])
    next(e for e in profile["elements"] if e["id"] == "Observation.dataAbsentReason")["min"] = 1
    with pytest.raises(AssertionError):
        check_resource(outcome.resource, profile)


def test_missing_pulse_ox_coding_is_detected(map_case):
    _, _, outcome = map_case("pulse-oximetry")
    resource = copy.deepcopy(outcome.resource)
    resource["code"]["coding"].pop()
    with pytest.raises(AssertionError):
        check_resource(resource)


def test_load_required_unit_sets_from_verified_package(tmp_path, units):
    members = {"package/" + f.name: f.read_bytes() for f in FIXTURES.glob("ValueSet-*.json")}
    data = make_fake_package_bytes({"name": "hl7.fhir.r4.core", "version": "4.0.1"}, members)
    path = tmp_path / "core.tgz"
    path.write_bytes(data)
    assert load_unit_sets(path, hashlib.sha256(data).hexdigest()) == units
    assert units == {
        "ucum-bodytemp": frozenset({"Cel", "[degF]"}),
        "ucum-bodyweight": frozenset({"kg", "g", "[lb_av]"}),
        "ucum-bodylength": frozenset({"cm", "[in_i]"}),
    }
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_unit_sets(path, "0" * 64)


def test_anchor_ids_preserve_supplied_ids_and_generate_missing_ones(intake):
    raw = intake.model_dump(mode="json", by_alias=True)
    for name in ("patient", "practitioner", "encounter"):
        raw[name].pop("id")
    parsed = Intake.model_validate(raw)
    first, second = build_anchors(parsed), build_anchors(parsed)
    assert first == second
    assert first.patient["id"] == "example-patient-1"
    assert first.encounter["subject"]["reference"] == "Patient/example-patient-1"
    assert "diagnosis" not in first.encounter
    raw["patient"]["id"] = "bad/id"
    raw["encounter"]["subject"]["reference"] = "Patient/bad/id"
    with pytest.raises(ValueError, match="R4 id"):
        build_anchors(Intake.model_validate(raw))
