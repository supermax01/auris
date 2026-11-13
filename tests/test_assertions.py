"""Reduced assertion schema tests (PLAN A3/A5, Step 2a).

All data is fictional. The schema-subset walk below is deliberately
independent of ``auris.assertions.check_schema_subset`` so the two
implementations check each other.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from auris.assertions import (
    ASSERTION_MODELS,
    MAX_ASSERTIONS,
    MAX_EVIDENCE_ITEMS,
    AssertionKind,
    ExtractionResult,
    check_schema_subset,
    extraction_json_schema,
    parse_assertion,
    write_extraction_json_schema,
)

_COMMON: dict[str, Any] = {
    "concept_text": "thing",
    "polarity": "affirmed",
    "experiencer": "patient",
    "temporality": "current",
    "source_speaker_role": "patient",
    "evidence": [{"segment_id": "s1", "quote": "thing"}],
}

_KIND_EXTRAS: dict[str, dict[str, Any]] = {
    "vital_sign": {"value_text": "128 over 82", "unit_text": None, "stated_time_text": None},
    "symptom": {},
    "clinician_assessed_problem": {},
    "clinician_ruled_out_problem": {},
    "patient_reported_condition": {},
    "explicit_new_order": {
        "medication_intent": "new_order",
        "dose_text": "500 milligrams",
        "route_text": None,
        "frequency_text": "three times a day",
    },
    "reported_medication": {
        "medication_intent": "continuing",
        "dose_text": None,
        "route_text": None,
        "frequency_text": None,
    },
    "allergy_statement": {
        "allergen_text": "penicillin",
        "reaction_manifestation_text": "rash",
        "stated_resolution_text": None,
    },
    "allergy_denial": {"allergen_text": None},
}


def _assertion_data(kind: str, **overrides: Any) -> dict[str, Any]:
    data = {"kind": kind, **_COMMON, **_KIND_EXTRAS[kind]}
    data.update(overrides)
    return data


def test_nine_kinds_match_the_a3_rows() -> None:
    assert {kind.value for kind in AssertionKind} == set(_KIND_EXTRAS)
    assert len(ASSERTION_MODELS) == 9


@pytest.mark.parametrize("kind", sorted(_KIND_EXTRAS))
def test_every_kind_round_trips(kind: str) -> None:
    assertion = parse_assertion(_assertion_data(kind))
    dumped = assertion.model_dump(mode="json")
    assert parse_assertion(dumped) == assertion
    assert dumped == _assertion_data(kind)
    assert type(assertion) in ASSERTION_MODELS


@pytest.mark.parametrize("kind", sorted(_KIND_EXTRAS))
def test_every_clinical_field_accepts_null(kind: str) -> None:
    data = _assertion_data(kind)
    for key in list(data):
        if key not in ("kind", "evidence"):
            data[key] = None
    assertion = parse_assertion(data)
    assert assertion.model_dump(mode="json") == data


@pytest.mark.parametrize("kind", sorted(_KIND_EXTRAS))
def test_blank_clinical_text_becomes_null(kind: str) -> None:
    assertion = parse_assertion(_assertion_data(kind, concept_text="   "))
    assert assertion.concept_text is None


def test_evidence_requires_at_least_one_item() -> None:
    with pytest.raises(ValidationError):
        parse_assertion(_assertion_data("symptom", evidence=[]))


def test_evidence_is_bounded() -> None:
    too_many = [{"segment_id": "s1", "quote": "thing"}] * (MAX_EVIDENCE_ITEMS + 1)
    with pytest.raises(ValidationError):
        parse_assertion(_assertion_data("symptom", evidence=too_many))


def test_blank_quote_refused() -> None:
    with pytest.raises(ValidationError, match="quote must not be blank"):
        parse_assertion(_assertion_data("symptom", evidence=[{"segment_id": "s1", "quote": "  "}]))


def test_unknown_kind_refused() -> None:
    data = _assertion_data("symptom")
    data["kind"] = "diagnosis"
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        parse_assertion(data)


@pytest.mark.parametrize("kind", sorted(_KIND_EXTRAS))
def test_code_fields_are_refused(kind: str) -> None:
    data = _assertion_data(kind)
    data["code"] = "FICTIONAL-CODE"
    with pytest.raises(ValidationError):
        parse_assertion(data)


def test_evidence_cannot_carry_codes() -> None:
    data = _assertion_data(
        "symptom", evidence=[{"segment_id": "s1", "quote": "thing", "code": "FICTIONAL-CODE"}]
    )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        parse_assertion(data)


def test_no_code_fields_exist_in_any_model() -> None:
    forbidden = ("code", "system", "loinc", "snomed", "rxnorm", "icd")
    for model in ASSERTION_MODELS:
        for field_name in model.model_fields:
            for needle in forbidden:
                assert needle not in field_name.lower(), f"{model.__name__}.{field_name}"


def test_extraction_result_is_bounded_and_may_be_empty() -> None:
    assert ExtractionResult.model_validate({"assertions": []}).assertions == ()
    too_many = [_assertion_data("symptom")] * (MAX_ASSERTIONS + 1)
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate({"assertions": too_many})


def _walk(node: Any) -> Any:
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def test_exported_schema_stays_in_the_constrained_subset() -> None:
    schema = extraction_json_schema()
    json.dumps(schema)  # must be plain JSON data
    for node in _walk(schema):
        if not isinstance(node, dict):
            continue
        assert "$ref" not in node, node
        assert "$defs" not in node, node
        assert "pattern" not in node, node
        assert "const" not in node, node
        assert "discriminator" not in node, node
        if node.get("type") == "array":
            assert type(node.get("maxItems")) is int and node["maxItems"] >= 0, node
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
        if "anyOf" in node:
            branches = node["anyOf"]
            assert len(branches) == 2, node
            assert isinstance(branches[0], dict) and branches[0], node
            assert branches[0].get("type") != "null", node
            assert branches[1] == {"type": "null"}, node
    assert check_schema_subset(schema) == []


def test_exported_schema_names_every_kind_as_a_singleton_enum() -> None:
    schema = extraction_json_schema()
    variants = schema["properties"]["assertions"]["items"]["oneOf"]
    assert len(variants) == 9
    assert {tuple(v["properties"]["kind"]["enum"]) for v in variants} == {
        (kind.value,) for kind in AssertionKind
    }


def test_exported_schema_uses_enums_for_closed_sets() -> None:
    schema = extraction_json_schema()
    enum_values = {
        tuple(node["enum"]) for node in _walk(schema) if isinstance(node, dict) and "enum" in node
    }
    assert ("affirmed", "negated") in enum_values
    assert ("patient", "family_member", "other", "unknown") in enum_values
    assert ("current", "past", "resolved", "future", "unclear") in enum_values
    assert ("clinician", "patient", "companion", "other", "unknown") in enum_values
    assert ("new_order", "continuing", "historical", "discussed", "unclear") in enum_values


def test_check_schema_subset_catches_violations() -> None:
    bad = {
        "type": "object",
        "properties": {
            "a": {"$ref": "#/$defs/X"},
            "b": {"type": "string", "pattern": "^x$"},
            "c": {"type": "array", "items": {"type": "string"}},
            "d": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
            "e": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
    }
    violations = check_schema_subset(bad)
    assert len(violations) == 4  # the nullable anyOf (e) is allowed


@pytest.mark.parametrize("kind", sorted(_KIND_EXTRAS))
def test_nullable_fields_are_still_required(kind: str) -> None:
    data = _assertion_data(kind)
    for field in data:
        missing = {key: value for key, value in data.items() if key != field}
        with pytest.raises(ValidationError):
            parse_assertion(missing)


@pytest.mark.parametrize("field", ["polarity", "experiencer", "temporality", "source_speaker_role"])
def test_closed_sets_reject_unknown_values(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        parse_assertion(_assertion_data("symptom", **{field: "invented"}))


def test_medication_intent_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError, match="medication_intent"):
        parse_assertion(_assertion_data("explicit_new_order", medication_intent="invented"))


@pytest.mark.parametrize("field", ["segment_id", "quote"])
@pytest.mark.parametrize("value", [None, "", "  "])
def test_evidence_fields_cannot_be_empty(field: str, value: object) -> None:
    evidence = {"segment_id": "s1", "quote": "thing", field: value}
    with pytest.raises(ValidationError):
        parse_assertion(_assertion_data("symptom", evidence=[evidence]))


@pytest.mark.parametrize("kind", sorted(_KIND_EXTRAS))
def test_verbatim_text_is_preserved(kind: str) -> None:
    data = _assertion_data(kind)
    for field in data:
        if field.endswith("_text"):
            data[field] = "  Fictional TEXT,\nverbatim.  "
    assert parse_assertion(data).model_dump(mode="json") == data


def test_bounds_accept_the_maximum() -> None:
    data = _assertion_data("symptom", evidence=_COMMON["evidence"] * MAX_EVIDENCE_ITEMS)
    result = ExtractionResult.model_validate({"assertions": [data] * MAX_ASSERTIONS})
    assert len(result.assertions) == MAX_ASSERTIONS
    assert len(result.assertions[0].evidence) == MAX_EVIDENCE_ITEMS
    assert ExtractionResult.model_validate_json(result.model_dump_json()) == result


def test_schema_file_matches_exported_schema(tmp_path: Path) -> None:
    path = tmp_path / "schema.json"
    write_extraction_json_schema(path)
    assert json.loads(path.read_text()) == extraction_json_schema()


@pytest.mark.parametrize("bound", [None, -1, "8", True, 1.5])
def test_schema_checker_rejects_invalid_array_bounds(bound: object) -> None:
    assert check_schema_subset({"type": "array", "maxItems": bound})


@pytest.mark.parametrize(
    "branches",
    [[{}, {"type": "null"}], [False, {"type": "null"}], [{"type": "null"}, {"type": "string"}]],
)
def test_schema_checker_rejects_noncanonical_nullable_forms(branches: object) -> None:
    assert check_schema_subset({"anyOf": branches})
