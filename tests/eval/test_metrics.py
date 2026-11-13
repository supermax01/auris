"""Hand-computed metrics, exact bounds, bootstrap and all safety categories."""

import pytest
from eval_helpers import DATASET, one_dataset, prediction

from auris.eval.critical import CRITICAL_ERRORS, critical_events, errors
from auris.eval.metrics import prf, score_model
from auris.eval.statistics import bootstrap, clopper_pearson_upper
from auris.fhir.models import ValidationLanes


def test_hand_computed_prf_and_macro():
    first, second = DATASET.encounters[:2]
    # First: 2 TP, 1 duplicate FP, 1 missed vital. Second: all 3 missed.
    proposed = (
        prediction(first.assertions[0]),
        prediction(first.assertions[1]),
        prediction(first.assertions[1], id="duplicate", lanes=ValidationLanes(evidence="FAIL")),
    )
    report = score_model(
        one_dataset(first, second), {first.id: proposed, second.id: ()}, seed=9, samples=30
    )
    micro = report["lenient"]["micro"]
    assert micro == dict(
        tp=2,
        fp=1,
        fn=4,
        gold_n=6,
        predicted_n=3,
        precision=2 / 3,
        recall=1 / 3,
        f1=4 / 9,
        low_power=True,
    )
    per_class = report["lenient"]["per_class"]
    assert per_class["AllergyIntolerance"] == prf(1, 1, 2)
    assert per_class["ledger:symptom"] == prf(1, 2, 2)
    assert per_class["Condition"] == prf(0, 0, 1)
    assert per_class["Observation"] == prf(0, 0, 1)
    assert report["lenient"]["macro"]["precision"] == (1 + 0.5) / 4
    assert report["lenient"]["macro"]["recall"] == 1 / 4
    assert report["lenient"]["macro"]["f1"] == pytest.approx((2 / 3 + 0.5) / 4)
    assert report["lanes"]["evidence"] == dict(
        PASS=2, FAIL=1, NOT_CHECKED=0, pass_rate_all=2 / 3, pass_rate_checked=2 / 3
    )
    assert report["lanes"]["terminology"]["pass_rate_checked"] is None
    assert report["review_burden"]["per_encounter"] == 1.5
    assert report["coverage"]["declared_fact_coverage"] == 1
    assert report["assertion_strict"]["micro"] == micro
    assert report["lenient"]["by_kind"]["symptom"] == prf(1, 2, 2)


def test_coverage_and_low_power_boundary():
    encounter = DATASET.encounters[11]
    report = score_model(one_dataset(encounter), {encounter.id: ()}, seed=1, samples=10)
    assert report["coverage"]["declared_fact_coverage"] == 2 / 3
    assert report["lenient"]["micro"]["recall"] == 0
    assert prf(0, 0, 24)["low_power"]
    assert not prf(0, 0, 25)["low_power"]
    with pytest.raises(ValueError, match="every encounter"):
        score_model(one_dataset(encounter), {}, seed=1)


@pytest.mark.parametrize(
    "k,n,expected",
    [
        (0, 1, 0.95),
        (0, 10, 0.2588655508930523),
        (0, 100, 0.029513049607039932),
        (1, 10, 0.3941633024365047),
        (5, 10, 0.7775588989918709),
        (10, 10, 1.0),
    ],
)
def test_exact_one_sided_bounds(k, n, expected):
    assert clopper_pearson_upper(k, n) == pytest.approx(expected, abs=1e-12)


def test_bound_edges_and_bootstrap_determinism():
    assert clopper_pearson_upper(0, 0) is None
    with pytest.raises(ValueError):
        clopper_pearson_upper(2, 1)

    def mean(values):
        return sum(values) / len(values)

    one = bootstrap([0, 1, 2, 3, 4], mean, seed=91, samples=100)
    assert one == bootstrap([0, 1, 2, 3, 4], mean, seed=91, samples=100)
    assert one != bootstrap([0, 1, 2, 3, 4], mean, seed=17, samples=100)
    assert bootstrap([7, 7], mean, seed=1)["low"] == 7


def dangerous_predictions():
    denial = DATASET.encounters[0].assertions[0]
    order = DATASET.encounters[2].assertions[1]
    person = DATASET.encounters[4].assertions[0]
    discussion = DATASET.encounters[6].assertions[0]
    ruled_out = DATASET.encounters[8].assertions[0]
    return [
        (
            "allergy_denial_flipped",
            denial,
            prediction(denial, kind="allergy_statement", polarity="affirmed"),
        ),
        ("wrong_drug_or_dose", order, prediction(order, dose_text="999 mg")),
        ("wrong_person", person, prediction(person, experiencer="patient")),
        (
            "order_never_given",
            discussion,
            prediction(discussion, kind="explicit_new_order", medication_intent="new_order"),
        ),
        (
            "denied_condition_present",
            ruled_out,
            prediction(ruled_out, kind="clinician_assessed_problem", polarity="affirmed"),
        ),
    ]


@pytest.mark.parametrize("category,gold,proposed", dangerous_predictions())
@pytest.mark.parametrize(
    "state",
    ["needs_review", "validation_failed", "ledger_only", "pending_validation", "final_exportable"],
)
def test_all_critical_errors_any_state_and_exported_separately(category, gold, proposed, state):
    proposed = proposed.model_copy(update={"export_state": state})
    assert category in errors(proposed, [gold])
    events = critical_events([proposed], [gold])
    assert events["pre_review"][category] == [proposed.id]
    assert bool(events["exported_without_review"][category]) == (state == "final_exportable")
    reviewed = proposed.model_copy(update={"reviewed": True})
    assert not critical_events([reviewed], [gold])["exported_without_review"][category]
    assert critical_events([reviewed], [gold])["pre_review"][category]


def test_correct_gold_has_no_critical_errors():
    for encounter in DATASET.encounters:
        for gold in encounter.assertions:
            assert not errors(prediction(gold), encounter.assertions)
    order = DATASET.encounters[2].assertions[1]
    assert "wrong_drug_or_dose" in errors(prediction(order, concept_text="unrelated drug"), [order])
    assert not errors(prediction(order, dose_text=None), [order])  # Omission, not fabricated dose.
    assert "order_never_given" in errors(prediction(order), [])
    nka = DATASET.encounters[1].assertions[0]
    assert "allergy_denial_flipped" in errors(
        prediction(
            nka, kind="allergy_statement", polarity="affirmed", allergen_text="unlisted allergen"
        ),
        [nka],
    )


def test_critical_counts_use_encounter_denominator():
    category, gold, proposed = dangerous_predictions()[0]
    encounter = DATASET.encounters[0].model_copy(update={"assertions": (gold,)})
    report = score_model(
        one_dataset(encounter),
        {encounter.id: (proposed, proposed.model_copy(update={"id": "duplicate"}))},
        seed=9,
        samples=10,
    )
    critical = report["critical_errors"]["pre_review"][category]
    assert critical["count"] == 2 and critical["affected_encounters"] == 1
    assert critical["encounter_rate"] == critical["upper_95"] == 1
    assert report["critical_errors"]["exported_without_review"][category]["count"] == 0
    for name in CRITICAL_ERRORS:
        assert name in report["critical_errors"]["pre_review"]


def test_stale_review_does_not_hide_unreviewed_error(map_case):
    from auris.eval.predictions import freeze_prediction
    from auris.fhir.models import resource_digest

    _, assertion, outcome = map_case("heart-rate")
    stale = outcome.model_copy(update={"reviewed_resource_digest": "obsolete"})
    assert freeze_prediction(assertion, stale).reviewed is False
    current = outcome.model_copy(
        update={"reviewed_resource_digest": resource_digest(outcome.resource)}
    )
    assert freeze_prediction(assertion, current).reviewed is True


def test_failed_quote_medication_safety_and_cross_kind_person():
    order = DATASET.encounters[2].assertions[1]
    proposed = prediction(
        order,
        concept_text="unrelated medicine",
        evidence=(),
        cited_segment_ids=(order.evidence[0].segment_id,),
        export_state="validation_failed",
    )
    assert "wrong_drug_or_dose" in errors(proposed, [order])
    reported = DATASET.encounters[12].assertions[0]
    assert "wrong_drug_or_dose" in errors(prediction(reported, dose_text="999 mg"), [reported])
    family = reported.model_copy(update={"experiencer": "family_member"})
    proposed = prediction(
        family, kind="explicit_new_order", experiencer="patient", medication_intent="new_order"
    )
    assert {"wrong_person", "order_never_given"} <= errors(proposed, [family])
    denial = DATASET.encounters[0].assertions[0]
    proposed = prediction(
        denial, kind="allergy_statement", polarity="affirmed", experiencer="other"
    )
    assert {"wrong_person", "allergy_denial_flipped"} <= errors(proposed, [denial])
