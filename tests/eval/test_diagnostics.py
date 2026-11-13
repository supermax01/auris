"""Hand-built mistakes on labelled fictional gold, never clinical accuracy evidence."""

import pytest
from eval_helpers import DATASET, TRANSCRIPTS, prediction

from auris.eval.diagnostics import encounter_diagnostics, render_diagnostics


@pytest.mark.parametrize(
    "index,position,category,updates,reason",
    [
        (
            0,
            0,
            "allergy_denial_flipped",
            {"kind": "allergy_statement", "polarity": "affirmed"},
            "contradicts gold allergy denial",
        ),
        (2, 1, "wrong_drug_or_dose", {"dose_text": "999 mg"}, "dose mismatch"),
        (4, 0, "wrong_person", {"experiencer": "patient"}, "experiencer"),
        (
            6,
            0,
            "order_never_given",
            {"kind": "explicit_new_order", "medication_intent": "new_order"},
            "no gold explicit order",
        ),
        (
            8,
            0,
            "denied_condition_present",
            {"kind": "clinician_assessed_problem", "polarity": "affirmed"},
            "contradicts gold denied/refuted condition",
        ),
    ],
)
def test_each_critical_event_has_gold_prediction_quotes_and_stage(
    index, position, category, updates, reason
):
    encounter = DATASET.encounters[index]
    gold = encounter.assertions[position]
    proposed = prediction(
        gold, **updates, export_state="final_exportable", review_reasons=("fictional_flag",)
    )
    transcript = TRANSCRIPTS[encounter.id]
    groups = encounter_diagnostics(encounter, [proposed], transcript)
    records = groups[category]
    assert [r["view"] for r in records] == ["pre_review", "exported_without_review"]
    for record in records:
        assert record["encounter_id"] == encounter.id
        assert record["gold"][0]["id"] == gold.id
        assert reason in record["reasons"][0]
        span = gold.evidence[0]
        expected_quote = transcript.segment_by_id(span.segment_id).text[
            span.char_start : span.char_end
        ]
        assert record["gold"][0]["evidence"][0]["quote"] == expected_quote
        assert record["prediction"]["evidence"][0]["quote"] == expected_quote
        assert record["prediction"]["review_reasons"] == ["fictional_flag"]
        assert record["prediction"]["lanes"]["evidence"] == "PASS"
    markdown = "\n".join(render_diagnostics(groups))
    assert category in markdown and "Gold assertion(s) | Prediction" in markdown
    assert "acceptable_texts" in markdown and "fictional_flag" in markdown
    reviewed = proposed.model_copy(update={"reviewed": True})
    assert len(encounter_diagnostics(encounter, [reviewed], transcript)[category]) == 1


def test_every_unmatched_item_and_lenient_pair_strict_failure():
    encounter = DATASET.encounters[0]
    gold = encounter.assertions[2]
    # One missed allergy, duplicated symptom, and leniently correct/wrong-unit vital.
    predictions = [
        prediction(encounter.assertions[1]),
        prediction(encounter.assertions[1], id="duplicate"),
        prediction(gold, unit_text="inches", value_text="86"),
    ]
    groups = encounter_diagnostics(encounter, predictions, TRANSCRIPTS[encounter.id])
    fn = [r for r in groups["false_negative"] if r["view"] == "lenient"]
    fp = [r for r in groups["false_positive"] if r["view"] == "lenient"]
    assert [r["gold"][0]["id"] for r in fn] == [encounter.assertions[0].id]
    assert fn[0]["prediction"] is None
    assert len(fp) == 1 and fp[0]["prediction"]["id"] == "duplicate"
    assert "consumed by another prediction" in fp[0]["reasons"][0]
    pairs = [r for r in groups["lenient_match_strict_fail"] if r["view"] == "assertion_strict"]
    assert len(pairs) == 1
    assert pairs[0]["gold"][0]["id"] == pairs[0]["prediction"]["id"] == gold.id
    reasons = " ".join(pairs[0]["reasons"])
    assert "strict: unit mismatch" in reasons and "inches" in reasons and "/min" in reasons
    assert "strict: value mismatch" in reasons and "86" in reasons and "68" in reasons
    assert len([r for r in groups["false_negative"] if r["view"] == "assertion_strict"]) == 2


def test_absent_gold_order_and_unaligned_original_quote_are_visible_and_escaped():
    encounter = DATASET.encounters[2]
    gold = encounter.assertions[1]
    empty = encounter.model_copy(update={"assertions": ()})
    proposed = prediction(
        gold,
        concept_text="fictional extra drug",
        evidence=(),
        quoted_evidence=({"segment_id": "s1", "quote": "<fake> | `quote`"},),
    )
    groups = encounter_diagnostics(empty, [proposed], TRANSCRIPTS[encounter.id])
    record = groups["order_never_given"][0]
    assert record["gold"] == []
    assert "no gold explicit order" in record["reasons"][0]
    assert record["prediction"]["quoted_evidence"][0]["quote"] == "<fake> | `quote`"
    assert record["prediction"]["evidence"] == []
    assert len(groups["false_positive"]) == 3
    markdown = "\n".join(render_diagnostics(groups))
    assert "<fake>" not in markdown and "&lt;fake&gt; &#124; &#96;quote&#96;" in markdown
