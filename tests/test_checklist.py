"""Unit tests for the checklist generator on a fictional fixture."""

import json
from pathlib import Path

import pytest

from auris.contract.checklist import (
    ChecklistDocument,
    ChecklistError,
    ElementSource,
    ElementSources,
    OpenQuestion,
    coverage_problems,
    parse_structure_definition,
)

FIXTURE = Path(__file__).parent / "fixtures" / "fake_structure_definition.json"


@pytest.fixture(scope="module")
def profile():
    return parse_structure_definition(json.loads(FIXTURE.read_text()))


@pytest.fixture(scope="module")
def document(profile):
    return ChecklistDocument(
        fhir_version="4.0.1",
        source_package_id="fictional.package",
        source_package_version="0.0.1",
        source_package_sha256="0" * 64,
        source_package_license="CC0-1.0",
        claimed_profiles=[profile.id],
        profiles=[profile],
    )


def test_includes_required_fixed_slices_bindings_and_must_support(profile):
    ids = {element.id for element in profile.elements}
    assert "Observation.status" in ids  # min >= 1, required binding, MS
    assert "Observation.category" in ids  # slicing definition
    assert "Observation.category:FakeCat" in ids  # slice
    assert "Observation.code" in ids  # pattern + extensible binding
    assert "Observation.value[x]" in ids  # Must Support only
    assert "Observation" in ids  # carries the fake-1 invariant


def test_excludes_plain_optional_and_generic_invariant_only_elements(profile):
    ids = {element.id for element in profile.elements}
    assert "Observation.note" not in ids
    assert "Observation.comment" not in ids  # ele-1 is filtered boilerplate
    root = profile.element("Observation")
    assert [inv.key for inv in root.invariants] == ["fake-1"]


def test_parses_fixed_pattern_slice_binding_and_invariant_details(profile):
    system = profile.element("Observation.category:FakeCat.coding.system")
    assert system.fixed is not None
    assert system.fixed.kind == "fixedUri"
    assert system.fixed.value == "http://example.org/fictional/category-system"
    assert system.cardinality == "1..1"
    assert system.needs_source

    code = profile.element("Observation.code")
    assert code.pattern is not None
    assert code.pattern.kind == "patternCodeableConcept"
    assert code.binding is not None
    assert code.binding.strength == "extensible"

    category = profile.element("Observation.category")
    assert category.slicing is not None
    assert category.slicing.discriminator[0].type == "value"
    assert category.slicing.discriminator[0].path == "coding.code"

    slice_element = profile.element("Observation.category:FakeCat")
    assert slice_element.slice_name == "FakeCat"

    subject = profile.element("Observation.subject")
    assert subject.types[0].target_profile == [
        "http://example.org/fictional/StructureDefinition/fictional-patient"
    ]

    invariants = dict(profile.invariants())
    fake = invariants["Observation"]
    assert (fake.key, fake.severity, fake.expression) == (
        "fake-1",
        "error",
        "status.exists()",
    )


def test_missing_snapshot_is_an_error():
    with pytest.raises(ChecklistError, match="no snapshot"):
        parse_structure_definition({"id": "empty", "url": "x", "type": "Observation"})


def _sources(profile, entries, open_questions=()):
    return ElementSources(
        by_profile={profile.id: entries},
        open_questions=list(open_questions),
    )


def test_coverage_flags_missing_source(document, profile):
    problems = coverage_problems(document, _sources(profile, {}))
    assert any("Observation.status has no element source" in p for p in problems)


def test_coverage_complete_and_unresolved_needs_open_question(document, profile):
    needed = [element for element in profile.elements if element.needs_source]
    entries = {element.id: ElementSource(source="fixed", note="test") for element in needed}
    assert coverage_problems(document, _sources(profile, entries)) == []

    entries["Observation.status"] = ElementSource(source="unresolved")
    problems = coverage_problems(document, _sources(profile, entries))
    assert any("unresolved but not listed" in p for p in problems)

    resolved = coverage_problems(
        document,
        _sources(
            profile,
            entries,
            [OpenQuestion(profile.id, "Observation.status", "decide later")],
        ),
    )
    assert resolved == []

    entries["Observation.unknown"] = ElementSource(source="fixed")
    problems = coverage_problems(document, _sources(profile, entries))
    assert any("unknown element" in p for p in problems)
