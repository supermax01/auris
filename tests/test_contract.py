"""Contract tests over the committed checklist artefacts (offline)."""

from pathlib import Path

from auris.contract.checklist import (
    CLAIMED_PROFILES,
    BindingInfo,
    coverage_problems,
    find_discrepancies,
    load_checklist_json,
    load_element_sources,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKLIST_JSON = REPO_ROOT / "src" / "auris" / "contract" / "checklist.json"
ELEMENT_SOURCES = REPO_ROOT / "src" / "auris" / "contract" / "element_sources.toml"
CHECKLIST_MD = REPO_ROOT / "docs" / "profile-checklist.md"


def test_committed_checklist_covers_claimed_profiles():
    document = load_checklist_json(CHECKLIST_JSON)
    assert document.claimed_profiles == list(CLAIMED_PROFILES)
    assert [profile.id for profile in document.profiles] == list(CLAIMED_PROFILES)
    assert document.fhir_version == "4.0.1"
    assert document.source_package_id == "hl7.fhir.us.core"
    assert document.source_package_version == "6.1.0"
    # Generated derivatives are committed only because the source is CC0.
    assert document.source_package_license == "CC0-1.0"


def test_every_required_or_fixed_element_has_a_source():
    document = load_checklist_json(CHECKLIST_JSON)
    sources = load_element_sources(ELEMENT_SOURCES)
    assert coverage_problems(document, sources) == []


def test_blood_pressure_facts_match_fhir_reference():
    document = load_checklist_json(CHECKLIST_JSON)
    bp = next(p for p in document.profiles if p.id == "us-core-blood-pressure")
    assert bp.element("Observation.component").cardinality == "2..*"
    systolic_code = bp.element("Observation.component:systolic.code")
    assert systolic_code.pattern is not None
    assert systolic_code.pattern.value["coding"][0]["code"] == "8480-6"
    unit = bp.element("Observation.component:systolic.value[x].code")
    assert unit.fixed is not None
    assert unit.fixed.value == "mm[Hg]"


def test_no_discrepancies_with_fhir_reference():
    document = load_checklist_json(CHECKLIST_JSON)
    assert find_discrepancies(document) == []
    markdown = CHECKLIST_MD.read_text()
    assert "## Discrepancies with PLAN/fhir-reference" in markdown
    assert "None found by the automated checks" in markdown


def test_injected_snapshot_mismatch_is_reported():
    # Guards the corrected expectation: body temperature's unit binding is
    # required (ucum-bodytemp). If a snapshot said extensible, the check
    # must fire instead of passing silently.
    document = load_checklist_json(CHECKLIST_JSON)
    mutated = document.model_copy(deep=True)
    profile = next(p for p in mutated.profiles if p.id == "us-core-body-temperature")
    element_id = "Observation.value[x]:valueQuantity.code"
    index, element = next((i, e) for i, e in enumerate(profile.elements) if e.id == element_id)
    assert element.binding is not None
    profile.elements[index] = element.model_copy(
        update={"binding": BindingInfo(strength="extensible", value_set=element.binding.value_set)}
    )
    problems = find_discrepancies(mutated)
    assert any(element_id in p and "required" in p for p in problems)
    # The committed document itself is untouched by the mutation.
    assert find_discrepancies(document) == []
