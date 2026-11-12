"""Intake contract tests (PLAN A1, Step 2a). All data is fictional."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from auris.intake import Intake, load_intake

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "intake.json"


def _valid_intake_data() -> dict[str, Any]:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_example_intake_loads() -> None:
    intake = load_intake(EXAMPLE)
    assert intake.patient.gender == "female"
    assert intake.encounter.subject.reference == "Patient/example-patient-1"
    assert intake.encounter.period.start == "2026-11-02T09:00:00-05:00"


def test_example_intake_is_labelled_fictional() -> None:
    text = EXAMPLE.read_text(encoding="utf-8").lower()
    assert "fictional" in text
    assert "example.org" in text


def test_intake_round_trips() -> None:
    intake = load_intake(EXAMPLE)
    assert Intake.from_json(intake.to_json()) == intake


_MISSING_MINIMUM_CASES = [
    ("patient identifier absent", ("patient", "identifier")),
    ("patient name absent", ("patient", "name")),
    ("patient gender absent", ("patient", "gender")),
    ("practitioner identifier absent", ("practitioner", "identifier")),
    ("practitioner name absent", ("practitioner", "name")),
    ("encounter status absent", ("encounter", "status")),
    ("encounter class absent", ("encounter", "class")),
    ("encounter type absent", ("encounter", "type")),
    ("encounter subject absent", ("encounter", "subject")),
    ("encounter period absent", ("encounter", "period")),
    ("patient anchor absent", ("patient",)),
    ("practitioner anchor absent", ("practitioner",)),
    ("encounter anchor absent", ("encounter",)),
    ("patient identifier system absent", ("patient", "identifier", 0, "system")),
    ("patient identifier value absent", ("patient", "identifier", 0, "value")),
    ("practitioner identifier system absent", ("practitioner", "identifier", 0, "system")),
    ("practitioner identifier value absent", ("practitioner", "identifier", 0, "value")),
    ("practitioner family absent", ("practitioner", "name", 0, "family")),
    ("encounter class code absent", ("encounter", "class", "code")),
    ("encounter subject reference absent", ("encounter", "subject", "reference")),
    ("encounter period start absent", ("encounter", "period", "start")),
]


@pytest.mark.parametrize(
    ("label", "path"), _MISSING_MINIMUM_CASES, ids=[c[0] for c in _MISSING_MINIMUM_CASES]
)
def test_missing_minimum_is_refused(label: str, path: tuple[str | int, ...]) -> None:
    data = _valid_intake_data()
    target: Any = data
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    with pytest.raises(ValidationError) as exc:
        Intake.model_validate(data)
    assert any(error["loc"] == path and error["type"] == "missing" for error in exc.value.errors())


def test_identifier_without_value_refused() -> None:
    data = _valid_intake_data()
    del data["patient"]["identifier"][0]["value"]
    with pytest.raises(ValidationError):
        Intake.model_validate(data)


def test_identifier_without_system_refused() -> None:
    data = _valid_intake_data()
    del data["patient"]["identifier"][0]["system"]
    with pytest.raises(ValidationError):
        Intake.model_validate(data)


def test_empty_identifier_list_refused() -> None:
    data = _valid_intake_data()
    data["practitioner"]["identifier"] = []
    with pytest.raises(ValidationError):
        Intake.model_validate(data)


def test_patient_name_needs_family_or_given() -> None:
    data = _valid_intake_data()
    data["patient"]["name"] = [{}]
    with pytest.raises(ValidationError, match="family and/or given"):
        Intake.model_validate(data)


def test_patient_name_given_only_is_accepted() -> None:
    data = _valid_intake_data()
    data["patient"]["name"] = [{"given": ["Fictional"]}]
    Intake.model_validate(data)


def test_patient_name_family_only_is_accepted() -> None:
    data = _valid_intake_data()
    data["patient"]["name"] = [{"family": "Examplepatient"}]
    Intake.model_validate(data)


def test_practitioner_name_needs_family() -> None:
    data = _valid_intake_data()
    data["practitioner"]["name"] = [{"given": ["Fictional"]}]
    with pytest.raises(ValidationError):
        Intake.model_validate(data)


def test_unknown_gender_refused() -> None:
    data = _valid_intake_data()
    data["patient"]["gender"] = "F"
    with pytest.raises(ValidationError):
        Intake.model_validate(data)


def test_empty_encounter_type_refused() -> None:
    data = _valid_intake_data()
    data["encounter"]["type"] = []
    with pytest.raises(ValidationError):
        Intake.model_validate(data)


def test_empty_codeable_concept_refused() -> None:
    data = _valid_intake_data()
    data["encounter"]["type"] = [{}]
    with pytest.raises(ValidationError, match="coding or text"):
        Intake.model_validate(data)


def test_period_without_start_refused() -> None:
    data = _valid_intake_data()
    del data["encounter"]["period"]["start"]
    with pytest.raises(ValidationError):
        Intake.model_validate(data)


def test_period_end_before_start_refused() -> None:
    data = _valid_intake_data()
    data["encounter"]["period"]["end"] = "2026-11-02T08:00:00-05:00"
    with pytest.raises(ValidationError, match="period.end"):
        Intake.model_validate(data)


def test_subject_must_reference_a_patient() -> None:
    data = _valid_intake_data()
    data["encounter"]["subject"] = {"reference": "Practitioner/example-practitioner-1"}
    with pytest.raises(ValidationError, match="Patient"):
        Intake.model_validate(data)


def test_subject_must_match_intake_patient_id() -> None:
    data = _valid_intake_data()
    data["encounter"]["subject"] = {"reference": "Patient/someone-else"}
    with pytest.raises(ValidationError, match="does not reference"):
        Intake.model_validate(data)


def test_unknown_encounter_status_refused() -> None:
    data = _valid_intake_data()
    data["encounter"]["status"] = "done"
    with pytest.raises(ValidationError):
        Intake.model_validate(data)


def test_extra_fields_refused() -> None:
    data = _valid_intake_data()
    data["patient"]["nickname"] = "Fi"
    with pytest.raises(ValidationError):
        Intake.model_validate(data)


@pytest.mark.parametrize(
    "anchor,field",
    [
        ("patient", "identifier"),
        ("patient", "name"),
        ("practitioner", "identifier"),
        ("practitioner", "name"),
        ("encounter", "type"),
    ],
)
@pytest.mark.parametrize("value", [None, []])
def test_required_collections_cannot_be_empty(anchor: str, field: str, value: object) -> None:
    data = _valid_intake_data()
    data[anchor][field] = value
    with pytest.raises(ValidationError) as exc:
        Intake.model_validate(data)
    assert any(error["loc"] == (anchor, field) for error in exc.value.errors())


@pytest.mark.parametrize("anchor", ["patient", "practitioner"])
@pytest.mark.parametrize("field", ["system", "value"])
@pytest.mark.parametrize("value", [None, "", "  "])
def test_required_identifier_parts_cannot_be_blank(anchor: str, field: str, value: object) -> None:
    data = _valid_intake_data()
    data[anchor]["identifier"][0][field] = value
    with pytest.raises(ValidationError):
        Intake.model_validate(data)


@pytest.mark.parametrize("anchor", ["patient", "practitioner"])
@pytest.mark.parametrize(
    "field,value", [("family", "  "), ("given", ["Fictional", " "]), ("given", []), ("given", [""])]
)
def test_supplied_name_parts_cannot_be_blank(anchor: str, field: str, value: object) -> None:
    data = _valid_intake_data()
    data[anchor]["name"][0][field] = value
    with pytest.raises(ValidationError):
        Intake.model_validate(data)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "  ",
        "2026-02-30",
        "2026-01-15T09:00:00",
        "2026-01-15T09:00Z",
        1768485600,
        "1768485600",
    ],
)
def test_period_start_refuses_invalid_dates_without_coercion(value: object) -> None:
    data = _valid_intake_data()
    data["encounter"]["period"]["start"] = value
    with pytest.raises(ValidationError) as exc:
        Intake.model_validate(data)
    assert any(error["loc"] == ("encounter", "period", "start") for error in exc.value.errors())


@pytest.mark.parametrize(
    "value",
    [
        "2026",
        "2026-01",
        "2026-01-15",
        "2026-01-15T14:00:00Z",
        "2026-01-15T09:00:00.123456789-05:00",
    ],
)
def test_period_preserves_supplied_precision_and_timezone(value: str) -> None:
    data = _valid_intake_data()
    data["encounter"]["period"] = {"start": value}
    intake = Intake.model_validate(data)
    assert intake.encounter.period.start == value
    assert json.loads(intake.to_json())["encounter"]["period"]["start"] == value


def test_period_comparison_uses_timezone() -> None:
    data = _valid_intake_data()
    data["encounter"]["period"]["end"] = "2026-11-02T13:59:00Z"
    with pytest.raises(ValidationError, match="period.end"):
        Intake.model_validate(data)


def test_period_mixed_precision_does_not_crash_or_invent_time() -> None:
    data = _valid_intake_data()
    data["encounter"]["period"]["start"] = "2026-01-15"
    intake = Intake.model_validate(data)
    assert intake.encounter.period.start == "2026-01-15"


def test_subject_needs_a_target_even_without_patient_id() -> None:
    data = _valid_intake_data()
    del data["patient"]["id"]
    data["encounter"]["subject"]["reference"] = "Patient/"
    with pytest.raises(ValidationError, match="Patient/<id>"):
        Intake.model_validate(data)


def test_minimal_intake_does_not_require_optional_ids_or_end() -> None:
    data = _valid_intake_data()
    for anchor in ("patient", "practitioner", "encounter"):
        del data[anchor]["id"]
    del data["encounter"]["period"]["end"]
    intake = Intake.model_validate(data)
    assert intake.patient.id is None
    assert intake.encounter.period.end is None


def test_intake_does_not_mutate_inputs_or_rewrite_supplied_values() -> None:
    data = _valid_intake_data()
    original = json.dumps(data, sort_keys=True)
    intake = Intake.model_validate(data)
    assert json.dumps(data, sort_keys=True) == original
    assert json.loads(intake.to_json()) == data


def test_intake_save_is_private_and_round_trips(tmp_path: Path) -> None:
    intake = load_intake(EXAMPLE)
    path = tmp_path / "workdir" / "intake.json"
    intake.save(path)
    assert load_intake(path) == intake
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
