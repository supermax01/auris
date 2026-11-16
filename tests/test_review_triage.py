"""Review triage: FICTIONAL cases only."""

import pytest
from test_review_page import act, prepare

from auris.review_state import read_events, target_digest
from auris.review_triage import triage


def _triage(outcome, assertion):
    return triage(outcome, assertion.model_dump(mode="json"))


@pytest.mark.parametrize(
    "name",
    [
        "symptom-affirmed",
        "symptom-negated",
        "heart-rate",
        "negated-history",
        "reported-medication-affirmed",
        "plan-item",
        "social-history",
        "clinician_assessed_problem",
    ],
)
def test_routine_facts(map_case, name):
    _, assertion, outcome = map_case(name)
    assert _triage(outcome, assertion) == ("routine", ())


@pytest.mark.parametrize(
    ("name", "changes", "reason"),
    [
        ("new-order", {}, "explicit_new_order"),
        ("allergy-ingredient", {}, "allergy_statement"),
        ("nka", {}, "allergy_denial"),
        ("clinician_ruled_out_problem", {}, "clinician_ruled_out_problem"),
        ("reported-medication-affirmed", {"medication_intent": "discussed"}, None),
        ("symptom-affirmed", {"experiencer": "other"}, "non_patient_experiencer"),
        ("symptom-affirmed", {"source_speaker_role": "clinician"}, "source_role_mismatch"),
    ],
)
def test_attention_facts(map_case, name, changes, reason):
    _, assertion, outcome = map_case(name, changes=changes)
    triaged, reasons = _triage(outcome, assertion)
    assert triaged == "attention"
    if reason:
        assert reason in reasons


def test_family_history_is_routine(map_case):
    _, assertion, outcome = map_case(
        "patient_reported_condition", changes={"experiencer": "family_member"}
    )
    assert {"non_patient_experiencer", "patient_experiencer_required"} <= set(
        outcome.review_reasons
    )
    assert _triage(outcome, assertion) == ("routine", ())


def test_a_diagnosis_record_not_named_in_its_quote_needs_attention(map_case):
    # FICTIONAL: a Condition record whose words are not in the quote.
    _, assertion, outcome = map_case(
        "clinician_assessed_problem", quote="The diabetes I mean, it is getting worse."
    )
    assert outcome.resource is not None and outcome.resource["resourceType"] == "Condition"
    assert "concept_text_not_in_quote" in outcome.review_reasons
    assert _triage(outcome, assertion) == ("attention", ("diagnosis_not_in_quote",))


def test_polarity_flag_is_scope_noise_only_in_a_long_quote_naming_the_concept(map_case):
    # FICTIONAL: a denial in another clause of a long quote is noise ...
    _, assertion, outcome = map_case(
        "symptom-affirmed", quote="I have a cough and it is worse at night, but no fever."
    )
    assert "polarity_conflicts_with_quote" in outcome.review_reasons
    assert _triage(outcome, assertion) == ("routine", ())
    # ... while a short answer that denies is not.
    _, assertion, outcome = map_case("symptom-affirmed", quote="No, not a cough.")
    assert "polarity_conflicts_with_quote" in outcome.review_reasons
    assert "polarity_conflicts_with_quote" in _triage(outcome, assertion)[1]


def test_unknown_flags_need_attention(map_case):
    _, assertion, outcome = map_case("symptom-affirmed")
    flagged = outcome.model_copy(update={"review_reasons": ("FICTIONAL_new_flag",)})
    assert _triage(flagged, assertion) == ("attention", ("FICTIONAL_new_flag",))


def _routine_run(map_case, tmp_path):
    cases = [map_case(name)[2] for name in ("symptom-affirmed", "plan-item", "new-order")]
    mapper = map_case("symptom-affirmed")[0]
    run = prepare(tmp_path / "run", mapper, cases)
    return run, cases


def test_batch_approval_writes_one_labelled_receipt_per_routine_item(map_case, tmp_path):
    run, (symptom, plan, order) = _routine_run(map_case, tmp_path)
    assert run.routine_ids() == (symptom.id, plan.id)
    items = [(i, target_digest(run.targets[i])) for i in run.routine_ids()]
    state = run.approve_routine(items=items, reviewer="FICTIONAL Reviewer", role="clinician")
    events = read_events(run.log_path)
    assert [e.target_id for e in events] == [symptom.id, plan.id]
    assert all(e.action == "approve" for e in events)
    assert len({e.diff["batch_id"] for e in events}) == 1
    assert all(e.diff["batch_size"] == 2 and "batch approval" in e.diff["effect"] for e in events)
    assert state["routine_ids"] == []
    rows = {row["id"]: row for row in state["review"]}
    assert rows[order.id]["triage"] == "attention" and rows[order.id]["state"] == "unreviewed"
    assert order.id in state["export"]["blocking_ids"]  # the order still needs its own decision
    # Each receipt undoes on its own.
    act(run, run.targets[symptom.id], "undo")
    assert run.routine_ids() == (symptom.id,)


def test_batch_approval_refuses_changed_or_attention_items(map_case, tmp_path):
    run, (symptom, plan, order) = _routine_run(map_case, tmp_path)
    reviewer = {"reviewer": "FICTIONAL Reviewer", "role": "clinician"}
    with pytest.raises(ValueError, match="needs individual attention"):
        run.approve_routine(items=[(order.id, target_digest(order))], **reviewer)
    with pytest.raises(ValueError, match="changed since the list was shown"):
        run.approve_routine(items=[(symptom.id, "0" * 64)], **reviewer)
    with pytest.raises(ValueError, match="duplicate"):
        item = (symptom.id, target_digest(symptom))
        run.approve_routine(items=[item, item], **reviewer)
    with pytest.raises(ValueError, match="no routine items"):
        run.approve_routine(items=[], **reviewer)
    assert read_events(run.log_path) == ()  # a refused batch writes nothing


def test_batch_route_needs_a_reviewer(map_case, tmp_path):
    import json

    from auris.review_web.service import ReviewSession

    run, (symptom, *_) = _routine_run(map_case, tmp_path)
    session = ReviewSession(run.directory)
    body = json.dumps(
        {"items": [{"target_id": symptom.id, "target_sha256": target_digest(symptom)}]}
    ).encode()
    status, _ = session.dispatch("POST", "/api/approve-routine", session.token, body)
    assert status == 403
    session.dispatch(
        "POST",
        "/api/reviewer",
        session.token,
        json.dumps({"name": "FICTIONAL Reviewer", "role": "clinician"}).encode(),
    )
    status, state = session.dispatch("POST", "/api/approve-routine", session.token, body)
    assert status == 200
    (event,) = read_events(run.log_path)
    assert event.target_id == symptom.id and event.diff["batch_size"] == 1
