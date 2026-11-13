"""Step 12b, entirely FICTIONAL: real mapper/checks with a simulated HL7 validator."""

import json
import stat
import subprocess
import threading

import pytest
from demo_helpers import FakeValidator
from test_review_page import act, prepare, request

from auris._storage import write_private_text
from auris.assertions import parse_assertion
from auris.fhir import Mapper
from auris.fhir.models import LaneState, resource_digest
from auris.review_edits import (
    LOCKED_FIELDS,
    MODELS,
    CorrectionChecker,
    Layer,
    remap,
    validation_resources,
)
from auris.review_state import ReviewRun, read_events, target_digest
from auris.review_web.service import ReviewSession
from auris.transcript import TranscriptSegment

IDENTITY = {"reviewer": "FICTIONAL Reviewer", "role": "fictional clinician"}
CASES = {
    "vital_sign": "heart-rate",
    "symptom": "symptom-affirmed",
    "clinician_assessed_problem": "clinician_assessed_problem",
    "clinician_ruled_out_problem": "clinician_ruled_out_problem",
    "patient_reported_condition": "patient_reported_condition",
    "explicit_new_order": "new-order",
    "reported_medication": "reported-medication-affirmed",
    "allergy_statement": "allergy-ingredient",
    "allergy_denial": "allergy-denial",
}


class FictionalChecker(CorrectionChecker):
    def __init__(self, mapper, *, failure=None, raises=None):
        self.mapper = mapper
        self.validator = FakeValidator(failure=failure)
        self.raises = raises
        self.calls = []
        self.mapped = []

    def map(self, intake, transcript, assertions, originals, extraction):
        self.mapped.append(assertions)
        mapper = Mapper(intake, transcript, self.mapper.terminology, self.mapper.unit_sets)
        return remap(mapper, assertions, originals, extraction)

    def validate(self, intake, directory, resources):
        self.calls.append(resources)
        if self.raises:
            raise self.raises
        return self.validator.validate(resources)


def editable_run(directory, mapper, assertions, outcomes, *, validation=None, **checker_options):
    prepare(directory, mapper, outcomes, validation=validation)
    extraction = json.loads((directory / "extraction.json").read_text())
    extraction["assertions"] = [a.model_dump(mode="json") for a in assertions]
    write_private_text(directory / "extraction.json", json.dumps(extraction))
    return ReviewRun(directory, checker=FictionalChecker(mapper, **checker_options))


def correct(run, identifier, *, fields=None, **kwargs):
    run.load()
    target = run.targets.get(identifier)
    digest = (
        target_digest(target)
        if target is not None
        else resource_digest(run.transcript.segment_by_id(identifier).model_dump(mode="json"))
    )
    return run.correct(
        target_id=identifier, target_sha256=digest, fields=fields, **IDENTITY, **kwargs
    )


EDITABLE_FIELDS = [
    (kind, field)
    for kind, model in MODELS.items()
    for field in model.model_fields.keys() - LOCKED_FIELDS - {"kind"}
]


@pytest.mark.parametrize("kind,field", EDITABLE_FIELDS)
def test_every_editable_field_round_trips_full_check_path(map_case, tmp_path, kind, field):
    mapper, assertion, original = map_case(CASES[kind])
    run = editable_run(tmp_path / "run", mapper, [assertion], [original])
    source = (run.directory / "extraction.json").read_bytes()
    raw = assertion.model_dump(mode="json")
    changes = {
        "polarity": "affirmed" if raw["polarity"] != "affirmed" else "negated",
        "experiencer": "other" if raw["experiencer"] != "other" else "patient",
        "temporality": "past" if raw["temporality"] != "past" else "current",
        "medication_intent": "discussed"
        if raw.get("medication_intent") != "discussed"
        else "continuing",
    }
    value = changes.get(field, "FICTIONAL corrected text" if raw[field] is None else None)
    state = correct(run, original.id, fields={field: value})
    assert run.assertions[original.id].model_dump(mode="json")[field] == value
    assert run.assertions[original.id].evidence == assertion.evidence
    assert run.checker.mapped and run.checker.calls
    resources = run.checker.calls[-1]
    assert [r["resourceType"] for r in resources[:3]] == ["Patient", "Practitioner", "Encounter"]
    assert len(resources) == 3 + bool(run.targets[original.id].resource)
    assert state["review"][0]["check_status"] == "complete"
    (event,) = read_events(run.log_path)
    assert event.action == "edit" and event.diff[field] == {"before": raw[field], "after": value}
    assert event.old_version_sha256 == target_digest(original)
    assert event.new_version_sha256 == target_digest(run.targets[original.id])
    assert event.timestamp and event.reviewer == IDENTITY["reviewer"]
    assert event.role == IDENTITY["role"]
    assert (run.directory / "extraction.json").read_bytes() == source
    assert stat.S_IMODE(run.layer_path.stat().st_mode) == 0o600
    assert Layer.read(run.layer_path).versions


@pytest.mark.parametrize("kind", CASES)
def test_kind_change_uses_existing_schema_and_stable_card_identity(map_case, tmp_path, kind):
    mapper, assertion, original = map_case("heart-rate")
    run = editable_run(tmp_path / "run", mapper, [assertion], [original])
    if kind == assertion.kind:
        fields = {"kind": kind, "value_text": None}
    else:
        fields = {"kind": kind}
    state = correct(run, original.id, fields=fields)
    edited = run.targets[original.id]
    assert edited.kind == kind and edited.id == original.id
    if edited.resource:
        assert edited.resource["id"] == original.id
    assert state["assertions"][original.id]["kind"] == kind
    assert run.assertions[original.id].evidence == assertion.evidence
    parse_assertion(run.assertions[original.id].model_dump(mode="json"))
    assert read_events(run.log_path)[0].diff


def test_unsupported_number_is_hard_fail_cannot_approve_but_can_dismiss(map_case, tmp_path):
    mapper, assertion, original = map_case("heart-rate")
    run = editable_run(tmp_path / "run", mapper, [assertion], [original])
    state = correct(run, original.id, fields={"value_text": "9999"})
    edited = run.targets[original.id]
    assert edited.lanes.evidence == LaneState.FAIL
    assert "value_text_number_not_in_quote" in edited.consistency.failures
    assert not state["ready"] and state["review"][0]["allowed_actions"] == ["dismiss"]
    with pytest.raises(ValueError, match="cannot override any FAIL"):
        act(run, edited, "approve")
    assert act(run, edited, "dismiss", "FICTIONAL unsupported value")["ready"]


def test_edited_concept_uses_same_model_output_review_rule(map_case, tmp_path):
    mapper, assertion, original = map_case("clinician_assessed_problem")
    run = editable_run(tmp_path / "run", mapper, [assertion], [original])
    state = correct(run, original.id, fields={"concept_text": "FICTIONAL alternate concept"})
    edited = run.targets[original.id]
    assert "concept_text_not_in_quote" in edited.review_reasons
    assert edited.lanes.evidence == LaneState.PASS
    assert not state["ready"] and "approve" in run.allowed_actions(edited)


def test_approval_stales_even_when_fhir_unchanged_and_undo_restores_receipt(map_case, tmp_path):
    mapper, assertion, original = map_case("heart-rate")
    # Fixed HR unit is supplied by the mapper whether this field is null or stated.
    assertion = assertion.model_copy(update={"unit_text": None})
    original = mapper.map_assertion(assertion, 0)
    run = editable_run(tmp_path / "run", mapper, [assertion], [original])
    act(run, original, "approve")
    first = run.current(original)
    before = run.log_path.read_bytes()
    state = correct(run, original.id, fields={"unit_text": "/min"})
    edited = run.targets[original.id]
    assert edited.resource == original.resource
    assert target_digest(edited) != target_digest(original)
    assert run.current(edited) is None and state["review"][0]["stale_entries"] == [first.entry_id]
    assert "Earlier review no longer applies" in state["review"][0]["stale_message"]
    assert run.log_path.read_bytes().startswith(before)
    corrected_log = run.log_path.read_bytes()
    state = correct(run, original.id, undo=True)
    assert run.current(run.targets[original.id]) == first
    assert state["review"][0]["state"] == "approve"
    assert state["review"][0]["target_sha256"] == target_digest(original)
    assert run.log_path.read_bytes().startswith(corrected_log)
    approval, edit, revoke = read_events(run.log_path)
    assert approval.action == "approve" and revoke.revokes == edit.entry_id
    assert revoke.diff["unit_text"] == {"before": "/min", "after": None}


def test_manual_revert_is_a_new_checked_version_undo_restores_previous_edits(map_case, tmp_path):
    mapper, assertion, original = map_case("heart-rate")
    run = editable_run(tmp_path / "run", mapper, [assertion], [original])
    act(run, original, "approve")
    correct(run, original.id, fields={"unit_text": "/min"})
    first = run.targets[original.id]
    act(run, first, "approve")
    correct(run, original.id, fields={"unit_text": None})
    second = run.targets[original.id]
    assert second.resource == original.resource
    assert target_digest(second) not in (target_digest(original), target_digest(first))
    assert run.current(second) is None and len(run.checker.calls) == 2
    correct(run, original.id, undo=True)
    assert target_digest(run.targets[original.id]) == target_digest(first)
    assert run.current(run.targets[original.id]).action == "approve"
    correct(run, original.id, undo=True)
    assert target_digest(run.targets[original.id]) == target_digest(original)
    assert run.current(run.targets[original.id]).action == "approve"


def test_indirect_clinician_cluster_dependency_is_rechecked(map_case, tmp_path):
    mapper, assertion, _ = map_case("new-order")
    patient = TranscriptSegment(
        id="fictional-patient-turn",
        speaker_cluster_id="fictional-second-speaker",
        role="patient",
        text="I have a cough.",
        start_seconds=30,
        end_seconds=35,
    )
    transcript = mapper.transcript.model_copy(
        update={"segments": (*mapper.transcript.segments, patient)}
    )
    mapper = Mapper(mapper.intake, transcript, mapper.terminology, mapper.unit_sets)
    symptom = parse_assertion(
        {
            "kind": "symptom",
            "concept_text": "cough",
            "polarity": "affirmed",
            "experiencer": "patient",
            "temporality": "current",
            "source_speaker_role": "patient",
            "evidence": [{"segment_id": patient.id, "quote": patient.text}],
        }
    )
    original, sibling = mapper.map_all((assertion, symptom))
    run = editable_run(tmp_path / "run", mapper, [assertion, symptom], [original, sibling])
    act(run, original, "approve")
    correct(run, patient.id, speaker_role="clinician")
    assert original.id in run.checker.mapped[-1]
    assert run.targets[original.id].resource is None
    assert "prescriber_not_identified" in run.targets[original.id].review_reasons
    assert run.current(run.targets[original.id]) is None


def test_fact_edit_and_speaker_undo_do_not_persist_derived_source_role(map_case, tmp_path):
    mapper, assertion, original = map_case("new-order")
    run = editable_run(tmp_path / "run", mapper, [assertion], [original])
    segment_id = assertion.evidence[0].segment_id
    correct(run, segment_id, speaker_role="patient")
    correct(run, original.id, fields={"dose_text": None})
    correct(run, segment_id, undo=True)
    assert run.assertions[original.id].source_speaker_role == "clinician"
    assert run.assertions[original.id].dose_text is None
    assert run.targets[original.id].resource is not None


def test_edit_preserves_failed_window_boundary_and_merge_conflict(map_case, tmp_path):
    mapper, assertion, _ = map_case("heart-rate")
    original = mapper.map_assertion(assertion, 0, allowed_segment_ids=frozenset())
    original = original.model_copy(
        update={"is_material": True, "review_reasons": (*original.review_reasons, "merge_conflict")}
    )
    run = editable_run(tmp_path / "run", mapper, [assertion], [original])
    extraction = json.loads((run.directory / "extraction.json").read_text())
    extraction["citation_failures"] = [
        {"assertion_position": 0, "segment_ids": [assertion.evidence[0].segment_id]}
    ]
    write_private_text(run.directory / "extraction.json", json.dumps(extraction))
    state = correct(run, original.id, fields={"unit_text": "/min"})
    edited = run.targets[original.id]
    assert edited.lanes.evidence == LaneState.FAIL
    assert "citation_outside_window" in edited.review_reasons
    assert "merge_conflict" in edited.review_reasons
    assert not state["ready"]


def test_unlogged_overlay_is_ignored_after_append_failure(map_case, tmp_path, monkeypatch):
    mapper, assertion, original = map_case("heart-rate")
    run = editable_run(tmp_path / "run", mapper, [assertion], [original])

    def fail(*args):
        raise OSError("FICTIONAL append failure")

    monkeypatch.setattr("auris.review_editor.append_private_jsonl", fail)
    with pytest.raises(OSError, match="append failure"):
        correct(run, original.id, fields={"unit_text": "/min"})
    reopened = ReviewRun(run.directory)
    assert reopened.targets[original.id] == run.originals[original.id]
    assert not reopened.checks and not read_events(run.log_path)


def test_speaker_overlay_rechecks_citing_assertions_and_undo_restores_order(map_case, tmp_path):
    mapper, assertion, original = map_case("new-order")
    second = parse_assertion(
        {
            **assertion.model_dump(mode="json"),
            "kind": "reported_medication",
            "medication_intent": "continuing",
        }
    )
    sibling = mapper.map_assertion(second, 1)
    run = editable_run(tmp_path / "run", mapper, [assertion, second], [original, sibling])
    for target in run.outcomes:
        act(run, target, "approve")
    sources = {
        p.name: p.read_bytes()
        for p in run.directory.iterdir()
        if p.name in ("extraction.json", "review-inputs.json")
    }
    segment_id = assertion.evidence[0].segment_id
    state = correct(run, segment_id, speaker_role="patient")
    assert set(run.checker.mapped[-1]) == {original.id, sibling.id}
    assert run.targets[original.id].resource is None
    assert "prescriber_not_identified" in run.targets[original.id].review_reasons
    assert "kind_role_mismatch" in run.targets[original.id].review_reasons
    assert run.assertions[original.id].source_speaker_role == "patient"
    assert all(row["stale_entries"] for row in state["review"])
    assert run.transcript.segment_by_id(segment_id).role == "patient"
    assert run.inputs.transcript.segment_by_id(segment_id).role == "clinician"
    assert all((run.directory / name).read_bytes() == value for name, value in sources.items())
    state = correct(run, segment_id, undo=True)
    assert state["ready"] and all(row["state"] == "approve" for row in state["review"])
    assert run.targets[original.id].resource == original.resource


@pytest.mark.parametrize(
    "failure,raises",
    [
        ("FICTIONAL validator failure", None),
        (None, subprocess.TimeoutExpired("fictional-validator", 1)),
        (None, RuntimeError("FICTIONAL backend exception")),
    ],
)
def test_failure_timeout_or_exception_is_not_checked_and_blocked(
    map_case, tmp_path, failure, raises
):
    mapper, assertion, original = map_case("heart-rate")
    run = editable_run(
        tmp_path / "run", mapper, [assertion], [original], failure=failure, raises=raises
    )
    state = correct(run, original.id, fields={"unit_text": "/min"})
    edited = run.targets[original.id]
    assert edited.lanes.base_r4 == edited.lanes.us_core_profile == LaneState.NOT_CHECKED
    assert not state["ready"] and original.id in state["export"]["blocking_ids"]
    assert state["review"][0]["check_status"] == "blocked"
    assert "retry checks or dismiss" in state["review"][0]["check_message"]
    assert "approve" not in state["review"][0]["allowed_actions"]
    assert len(json.loads((run.directory / "bundle.json").read_text())["entry"]) == 3
    assert act(run, edited, "dismiss", "FICTIONAL unavailable checker")["ready"]


def test_pending_recheck_is_durable_blocked_and_private_atomic(map_case, tmp_path):
    mapper, assertion, original = map_case("heart-rate")
    run = editable_run(tmp_path / "run", mapper, [assertion], [original])
    run.rewrite()
    keys = run.prepare_correction(
        target_id=original.id,
        target_sha256=target_digest(original),
        fields={"unit_text": "/min"},
        **IDENTITY,
    )
    pending = ReviewRun(run.directory)
    state = pending.rewrite()
    assert not state["ready"] and state["review"][0]["check_status"] == "rechecking"
    assert state["lanes"][original.id]["base_r4"]["state"] == "NOT_CHECKED"
    assert state["review"][0]["allowed_actions"] == ["dismiss"]
    inode = run.layer_path.stat().st_ino
    run.recheck(keys)
    assert run.layer_path.stat().st_ino != inode


def test_successful_recheck_can_replace_original_unchecked_anchor_lanes(map_case, tmp_path):
    mapper, assertion, original = map_case("heart-rate")
    validation = FakeValidator(failure="FICTIONAL original timeout").validate(
        validation_resources(mapper.intake, [original])
    )
    run = editable_run(tmp_path / "run", mapper, [assertion], [original], validation=validation)
    assert not run.rewrite()["ready"]
    state = correct(run, original.id, fields={"unit_text": "/min"})
    assert state["ready"] and state["export"]["anchors_checked"]
    assert run.validation.failure is None


def test_incomplete_validator_result_cannot_reuse_old_pass(map_case, tmp_path):
    mapper, assertion, original = map_case("heart-rate")
    run = editable_run(tmp_path / "run", mapper, [assertion], [original])
    keys = run.prepare_correction(
        target_id=original.id,
        target_sha256=target_digest(original),
        fields={"unit_text": "/min"},
        **IDENTITY,
    )
    result = FakeValidator().validate(run.recheck_resources(keys))
    result = result.model_copy(update={"resources": result.resources[:-1]})
    state = run.finish_recheck(keys, result)
    assert not state["ready"]
    assert run.targets[original.id].lanes.base_r4 == LaneState.NOT_CHECKED


def test_ready_message_counts_excluded_review_and_ledger_items(map_case, tmp_path):
    mapper, _, outcome = map_case("heart-rate", changes={"value_text": None})
    ledger = outcome.model_copy(
        update={
            "id": "fictional-ledger",
            "kind": "symptom",
            "ledger_only": True,
            "review_reasons": (),
        }
    )
    run = prepare(tmp_path / "run", mapper, [outcome, ledger])
    state = run.rewrite()
    assert state["ready"]
    assert (
        state["ready_message"]
        == "Ready to export — 1 item needs review and is not in the bundle; 1 is ledger-only"
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"not_a_field": "fictional"},
        {"evidence": None},
        {"source_speaker_role": "patient"},
        {"kind": "invented_kind"},
        {"polarity": "invalid"},
        {"value_text": "x" * 4001},
        {"value_text": 123},
        {"kind": "symptom", "dose_text": None},
    ],
)
def test_server_refuses_invalid_edits_without_writing(map_case, tmp_path, fields):
    mapper, assertion, original = map_case("heart-rate")
    run = editable_run(tmp_path / "run", mapper, [assertion], [original])
    session = ReviewSession(run.directory, checker=run.checker)
    payload = {"target_id": original.id, "target_sha256": target_digest(original), "fields": fields}
    assert request(session, "POST", "/api/fact", payload)[0] == 403
    request(
        session, "POST", "/api/reviewer", {"name": IDENTITY["reviewer"], "role": IDENTITY["role"]}
    )
    assert request(session, "POST", "/api/fact", payload)[0] == 422
    assert not run.log_path.exists() and not run.layer_path.exists()


def test_async_http_keeps_page_available_and_supports_retry(map_case, tmp_path):
    mapper, assertion, original = map_case("heart-rate")
    run = editable_run(tmp_path / "run", mapper, [assertion], [original])
    session = ReviewSession(run.directory, checker=run.checker)
    request(
        session, "POST", "/api/reviewer", {"name": IDENTITY["reviewer"], "role": IDENTITY["role"]}
    )
    started, finish = threading.Event(), threading.Event()
    validator = run.checker.validate

    def delayed(*args):
        started.set()
        assert finish.wait(5)
        return validator(*args)

    run.checker.validate = delayed
    payload = {
        "target_id": original.id,
        "target_sha256": target_digest(original),
        "fields": {"unit_text": "/min"},
    }
    assert request(session, "POST", "/api/fact", payload)[0] == 202
    assert started.wait(5)
    status, body, _ = request(session, "GET", "/api/state")
    state = json.loads(body)
    assert status == 200 and state["busy"] and not state["ready"]
    assert state["review"][0]["check_status"] == "rechecking"
    assert request(session, "POST", "/api/fact", payload)[0] == 409
    assert request(session, "GET", "/api/outputs")[0] == 200
    finish.set()
    session.worker.join(5)
    assert not session.busy
    state = json.loads(request(session, "GET", "/api/state")[1])
    assert state["review"][0]["check_status"] == "complete"
    assert not state["busy"]


def test_retry_endpoint_recovers_failed_check_without_new_assertion(map_case, tmp_path):
    mapper, assertion, original = map_case("heart-rate")
    run = editable_run(
        tmp_path / "run", mapper, [assertion], [original], failure="FICTIONAL timeout"
    )
    correct(run, original.id, fields={"unit_text": "/min"})
    edited_hash = target_digest(run.targets[original.id])
    log = run.log_path.read_bytes()
    run.checker.validator = FakeValidator()
    session = ReviewSession(run.directory, checker=run.checker)
    request(
        session, "POST", "/api/reviewer", {"name": IDENTITY["reviewer"], "role": IDENTITY["role"]}
    )
    assert (
        request(
            session,
            "POST",
            "/api/recheck",
            {"target_id": original.id, "target_sha256": edited_hash},
        )[0]
        == 202
    )
    session.worker.join(5)
    state = json.loads(request(session, "GET", "/api/state")[1])
    assert state["ready"] and state["review"][0]["check_status"] == "complete"
    assert state["review"][0]["target_sha256"] == edited_hash
    assert run.log_path.read_bytes() == log


def test_overlay_file_refuses_symlink_and_shared_permissions(map_case, tmp_path):
    mapper, assertion, outcome = map_case("heart-rate")
    run = editable_run(tmp_path / "run", mapper, [assertion], [outcome])
    other = tmp_path / "fictional-other.json"
    other.write_text("{}")
    run.layer_path.symlink_to(other)
    with pytest.raises(OSError):
        run.load()
    run.layer_path.unlink()
    run.layer_path.write_text("{}")
    with pytest.raises(ValueError, match="0600"):
        run.load()
