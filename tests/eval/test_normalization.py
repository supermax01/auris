"""Scorer independence and spelling equivalences on fictional numeric examples."""

import copy

import pytest
from eval_helpers import DATASET, one_dataset, prediction

from auris.eval.critical import errors
from auris.eval.matching import eligible, strict_reasons
from auris.eval.metrics import score_model
from auris.eval.normalization import normalize_dose, vital_unit, vital_value


@pytest.mark.parametrize("text", ["126 over 78", "126/78", "126.00 / 78.0", "126 OVER 78"])
def test_bp_pair(text):
    gold = DATASET.encounters[2].assertions[2]
    proposed = prediction(
        gold,
        value_text=text,
        unit_text="millimetres of mercury",
        canonical_value=None,
        ucum_unit=None,
    )
    assert vital_value(text) == ("126", "78")
    assert eligible(proposed, gold, strict=True)


@pytest.mark.parametrize(
    "text,unit",
    [
        ("beats per minute", "/min"),
        ("breaths per minute", "/min"),
        ("per minute", "/min"),
        ("percent", "%"),
        ("degrees Fahrenheit", "[degF]"),
        ("degrees Celsius", "Cel"),
        ("pounds", "[lb_av]"),
        ("kilograms", "kg"),
        ("inches", "[in_i]"),
        ("centimetres", "cm"),
        ("centimeters", "cm"),
        ("millimetres of mercury", "mm[Hg]"),
        ("millimeters of mercury", "mm[Hg]"),
    ],
)
def test_vital_unit_spellings(text, unit):
    assert vital_unit(text) == vital_unit(unit) == unit


@pytest.mark.parametrize("text", [None, "", "unknown", "126 over", "126/78/90", "NaN", "about 68"])
def test_no_invented_vital_value(text):
    assert vital_value(text) is None


def test_reviewed_vital_scores_extraction_independently_of_record():
    original = DATASET.encounters[0]
    gold = original.assertions[2]
    encounter = original.model_copy(update={"assertions": (gold,)})
    proposed = prediction(
        gold,
        canonical_value=None,
        ucum_unit=None,
        resource=None,
        review_reasons=("measurement_date_not_day_precision",),
    )
    report = score_model(one_dataset(encounter), {encounter.id: (proposed,)}, seed=1, samples=10)
    assert report["assertion_strict"]["micro"]["f1"] == 1
    assert report["record_strict"]["micro"]["f1"] == 0
    assert "no built FHIR resource" in strict_reasons(proposed, gold, record=True)
    # A correct record cannot hide an incorrect extraction, or vice versa.
    built = {
        "resourceType": "Observation",
        "valueQuantity": {"value": 68, "code": "/min", "system": "http://unitsofmeasure.org"},
    }
    wrong_extraction = proposed.model_copy(update={"resource": built, "value_text": "86"})
    assert not eligible(wrong_extraction, gold, strict=True)
    assert eligible(wrong_extraction, gold, strict=True, record=True)
    built["valueQuantity"]["value"] = 86
    wrong_record = proposed.model_copy(update={"resource": built})
    assert eligible(wrong_record, gold, strict=True)
    assert not eligible(wrong_record, gold, strict=True, record=True)


def bp_resource():
    # Partial FHIR projection for scorer tests only; not a validator fixture.
    return {
        "resourceType": "Observation",
        "component": [
            {
                "code": {"coding": [{"system": "http://loinc.org", "code": code}]},
                "valueQuantity": {
                    "value": value,
                    "code": "mm[Hg]",
                    "system": "http://unitsofmeasure.org",
                },
            }
            for code, value in (("8462-4", 78), ("8480-6", 126))
        ],
    }


def test_record_bp_checks_both_components_in_code_order():
    gold = DATASET.encounters[2].assertions[2]
    resource = bp_resource()
    assert eligible(prediction(gold, resource=resource), gold, strict=True, record=True)
    for change in ("value", "unit", "system", "missing", "duplicate"):
        broken = copy.deepcopy(resource)
        diastolic = broken["component"][0]
        if change == "value":
            diastolic["valueQuantity"]["value"] = 79
        elif change == "unit":
            diastolic["valueQuantity"]["code"] = "/min"
        elif change == "system":
            diastolic["valueQuantity"]["system"] = "urn:fictional:wrong-system"
        elif change == "missing":
            del diastolic["valueQuantity"]
        else:
            broken["component"].append(diastolic)
        assert not eligible(prediction(gold, resource=broken), gold, strict=True, record=True)


@pytest.mark.parametrize(
    "left,right,equal",
    [
        ("500 mg", "500 milligrams", True),
        ("500.00 milligram", "0500mg", True),
        ("50 mg", "500 milligrams", False),
        ("500 mg", "500 mcg", False),
        ("0.5 g", "500 mg", False),
        ("2.00 micrograms", "2 mcg", True),
        ("1 gram", "1.0 g", True),
        ("5 millilitres", "5.0 mL", True),
        ("5 milliliters", "5 mL", True),
        ("1 unit", "1.0 units", True),
        ("1 tablet", "1 tablets", False),
        (None, None, True),
        (None, "500 mg", False),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_dose_strict_and_safety_share_independent_normalization(left, right, equal, reverse):
    if reverse:
        left, right = right, left
    gold = DATASET.encounters[2].assertions[1].model_copy(update={"dose_text": right})
    proposed = prediction(gold, dose_text=left)
    assert (normalize_dose(left) == normalize_dose(right)) == equal
    assert eligible(proposed, gold, strict=True) == equal
    assert ("wrong_drug_or_dose" in errors(proposed, [gold], scorer_version="2")) == (
        not equal and left is not None
    )
