"""Scorer v2 replay and v3 safety distinctions on labelled fictional gold."""

import pytest
from eval_helpers import DATASET, TRANSCRIPTS, one_dataset, prediction

from auris.eval.critical import errors
from auris.eval.diagnostics import encounter_diagnostics, render_diagnostics
from auris.eval.metrics import score_model
from auris.eval.noncritical import findings
from auris.eval.normalization import dose_amounts


@pytest.mark.parametrize("first", ["patient", "family_member", "other", "unknown", None])
@pytest.mark.parametrize("second", ["patient", "family_member", "other", "unknown", None])
def test_wrong_person_requires_patient_nonpatient_crossing(first, second):
    gold = DATASET.encounters[4].assertions[0].model_copy(update={"experiencer": second})
    proposed = prediction(gold, experiencer=first)
    critical = (first == "patient" and second in ("family_member", "other")) or (
        second == "patient" and first in ("family_member", "other")
    )
    assert ("wrong_person" in errors(proposed, [gold])) == critical
    assert ("attribute_errors" in findings(proposed, [gold])) == (first != second and not critical)
    assert ("wrong_person" in errors(proposed, [gold], scorer_version="2")) == (
        first not in (None, "unknown") and first != second
    )


@pytest.mark.parametrize(
    "left,right,critical,field",
    [
        ("50 mg", "500 mg", True, False),
        ("500.00 milligrams", "500mg", False, False),
        ("once daily", "500 mg", False, True),
        ("null", "500 mg", False, True),
        ("UNKNOWN", None, False, True),
        (None, "500 mg", False, False),
        ("500 mg", None, False, False),
        ("500 mg once daily", "500 mg", False, False),
        ("500 mcg", "500 mg", True, False),
    ],
)
def test_dose_amounts_only_and_field_errors(left, right, critical, field):
    gold = DATASET.encounters[2].assertions[1].model_copy(update={"dose_text": right})
    proposed = prediction(gold, dose_text=left)
    assert ("wrong_drug_or_dose" in errors(proposed, [gold])) == critical
    assert ("field_errors" in findings(proposed, [gold])) == field
    wrong_drug = proposed.model_copy(update={"concept_text": "fictional different drug"})
    assert "wrong_drug_or_dose" in errors(wrong_drug, [gold])


def test_amount_normalization_does_not_infer_or_convert():
    assert dose_amounts("0500.0 milligrams once daily") == (("500", "mg"),)
    assert dose_amounts("2 times daily") == ()
    assert dose_amounts("0.5 g") != dose_amounts("500 mg")


@pytest.mark.parametrize("encounter_id", ["hc-08", "hc-13"])
def test_continuing_and_discussed_medicines_still_are_not_orders(encounter_id):
    encounter = next(e for e in DATASET.encounters if e.id == encounter_id)
    gold = next(g for g in encounter.assertions if g.kind == "reported_medication")
    proposed = prediction(gold, kind="explicit_new_order", medication_intent="new_order")
    assert "order_never_given" in errors(proposed, encounter.assertions)
    assert errors(proposed, encounter.assertions) == errors(
        proposed, encounter.assertions, scorer_version="2"
    )


def test_noncritical_metrics_and_diagnostics_and_v2_replay():
    encounter = DATASET.encounters[5]  # Fictional hc-06 gout family attribution.
    gold = next(g for g in encounter.assertions if g.experiencer == "other")
    proposed = prediction(gold, experiencer="family_member")
    data = one_dataset(encounter)
    old = score_model(data, {encounter.id: (proposed,)}, seed=1, samples=5, scorer_version="2")
    new = score_model(data, {encounter.id: (proposed,)}, seed=1, samples=5)
    assert old["critical_errors"]["pre_review"]["wrong_person"]["count"] == 1
    assert new["critical_errors"]["pre_review"]["wrong_person"]["count"] == 0
    assert "noncritical_errors" not in old
    assert new["noncritical_errors"]["attribute_errors"]["count"] == 1
    assert new["noncritical_errors"]["field_errors"]["count"] == 0
    for view in ("lenient", "assertion_strict", "record_strict"):
        assert old[view] == new[view]
    transcript = TRANSCRIPTS[encounter.id]
    groups = encounter_diagnostics(encounter, [proposed], transcript)
    assert "wrong_person" not in groups
    for category in ("attribute_errors",):
        assert groups[category][0]["prediction"]["id"] == gold.id
        assert groups[category][0]["gold"][0]["id"] == gold.id
        assert category in "\n".join(render_diagnostics(groups))
    old_groups = encounter_diagnostics(encounter, [proposed], transcript, scorer_version="2")
    assert "wrong_person" in old_groups and "attribute_errors" not in old_groups


@pytest.mark.parametrize("index,dose", [(14, "null"), (18, "once daily")])
def test_qwen_dose_field_regressions_in_metrics_and_diagnostics(index, dose):
    encounter = DATASET.encounters[index]
    gold = next(g for g in encounter.assertions if g.kind == "reported_medication")
    proposed = prediction(gold, dose_text=dose)
    report = score_model(one_dataset(encounter), {encounter.id: (proposed,)}, seed=1, samples=5)
    assert report["critical_errors"]["pre_review"]["wrong_drug_or_dose"]["count"] == 0
    assert report["noncritical_errors"]["field_errors"]["count"] == 1
    assert "wrong_drug_or_dose" in errors(proposed, [gold], scorer_version="2")
    groups = encounter_diagnostics(encounter, [proposed], TRANSCRIPTS[encounter.id])
    assert "wrong_drug_or_dose" not in groups
    assert groups["field_errors"][0]["prediction"]["dose_text"] == dose
    assert "non-critical: field_errors" in "\n".join(render_diagnostics(groups))
