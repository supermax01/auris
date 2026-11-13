"""Step 12a: offline review of authored FICTIONAL fixtures; no clinical advice.

Structural validator PASS here is simulated, never validator evidence.
"""

import copy
import io
import json
import shutil
import stat
import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from demo_helpers import FakeValidator, demo_input, replay
from validator_helpers import passing_run

from auris._storage import write_private_text
from auris.config import load_frozen_config
from auris.extract.quarantine import ItemError, QuarantinedItem
from auris.fhir import Mapper
from auris.fhir.models import LaneState, resource_digest
from auris.render.note import NoteMetadata
from auris.review import review_quarantined_item
from auris.review_state import ReviewInputs, ReviewRun, read_events, target_digest
from auris.review_web.server import BIND_ADDRESS, CSP, PAGE, handler_for, serve
from auris.review_web.service import ReviewSession
from auris.run import finish_run


def prepare(directory, mapper, outcomes, *, validation=None, quarantines=()):
    validation = validation or passing_run(mapper.intake, outcomes)
    inputs = ReviewInputs(
        intake=mapper.intake,
        transcript=mapper.transcript,
        validation=validation,
        note_metadata=NoteMetadata(
            model_id="fictional-test",
            prompt_version="fictional-test",
            run_timestamp="2026-11-13T00:00:00+00:00",
            fictional_demo=True,
        ),
    )
    write_private_text(directory / "review-inputs.json", inputs.model_dump_json())
    write_private_text(
        directory / "extraction.json",
        json.dumps(
            {
                "status": "complete",
                "metadata": {"transcript_sha256": mapper.transcript.content_hash},
                "mappings": [o.model_dump(mode="json") for o in outcomes],
                "quarantined_items": [q.model_dump(mode="json") for q in quarantines],
            }
        ),
    )
    return ReviewRun(directory)


def act(run, target, action, reason="", **kwargs):
    return run.act(
        target_id=target.id,
        target_sha256=target_digest(target),
        action=action,
        reviewer="FICTIONAL Reviewer",
        role="fictional clinician",
        reason=reason,
        **kwargs,
    )


@pytest.mark.parametrize(
    "lane", ["reduced_schema", "evidence", "base_r4", "us_core_profile", "terminology"]
)
def test_approve_guard_all_lanes_server_side(map_case, tmp_path, lane):
    mapper, _, outcome = map_case("new-order")
    validation = passing_run(mapper.intake, [outcome])
    if lane in ("reduced_schema", "evidence"):
        outcome = outcome.model_copy(
            update={"lanes": outcome.lanes.model_copy(update={lane: LaneState.FAIL})}
        )
    else:
        rows = list(validation.resources)
        index = next(i for i, row in enumerate(rows) if row.id == outcome.id)
        rows[index] = rows[index].model_copy(
            update={lane: getattr(rows[index], lane).model_copy(update={"state": LaneState.FAIL})}
        )
        validation = validation.model_copy(update={"resources": tuple(rows)})
    run = prepare(tmp_path / "run", mapper, [outcome], validation=validation)
    checked = run.targets[outcome.id]
    assert "approve" not in run.allowed_actions(checked)
    with pytest.raises(ValueError, match="cannot override any FAIL"):
        act(run, checked, "approve")
    assert not run.log_path.exists()
    state = act(run, checked, "dismiss", "FICTIONAL rejected extraction")
    assert state["ready"] and not state["export"]["exported_ids"]


@pytest.mark.parametrize(
    "case,exported",
    [
        ("clinician_ruled_out_problem", True),
        ("nka", False),
        ("allergy-denial", False),
        ("reported-medication-affirmed", False),
        ("allergy-family", True),
    ],
)
def test_a3_approval_preserves_content_and_ledger_rules(map_case, tmp_path, case, exported):
    mapper, _, outcome = map_case(case)
    original = copy.deepcopy(outcome.resource)
    run = prepare(tmp_path / "run", mapper, [outcome])
    assert run.rewrite()["export"]["blocking_ids"] == [outcome.id]
    state = act(run, outcome, "approve")
    assert state["ready"]
    assert state["export"]["exported_ids"] == ([outcome.id] if exported else [])
    assert run.targets[outcome.id].resource == original
    note = (run.directory / "note.md").read_text()
    assert "clinician-approved by FICTIONAL Reviewer, fictional clinician" in note
    assert "validated" not in note.replace("not validated", "")
    if case in ("nka", "allergy-denial"):
        assert "cannot represent this allergy denial" in note
        assert "## Needs review" in note
    if case == "allergy-family":
        assert "coding" not in original["code"]
        assert "text-only coding approved" in note
    if case == "clinician_ruled_out_problem":
        assert original["verificationStatus"]["coding"][0]["code"] == "refuted"
        assert "clinicalStatus" not in original


def test_dismiss_attribution_atomic_private_outputs_and_immutable_sources(
    map_case, tmp_path, monkeypatch
):
    mapper, _, outcome = map_case("allergy-family")
    run = prepare(tmp_path / "run", mapper, [outcome])
    original = {
        name: (run.directory / name).read_bytes()
        for name in ("extraction.json", "review-inputs.json")
    }
    for reason in ("", " \n "):
        with pytest.raises(ValueError, match="requires a reason"):
            act(run, outcome, "dismiss", reason)
    run.rewrite()
    old_inodes = {
        name: (run.directory / name).stat().st_ino
        for name in ("note.md", "bundle.json", "validation.json")
    }
    replacements = []
    original_replace = Path.replace

    def replace(source, target):
        assert source.parent == target.parent == run.directory
        assert stat.S_IMODE(source.stat().st_mode) == 0o600
        replacements.append(target.name)
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", replace)
    state = act(run, outcome, "dismiss", "FICTIONAL duplicate")
    assert state["ready"]
    assert set(replacements) == set(old_inodes)
    for name, inode in old_inodes.items():
        path = run.directory / name
        assert path.stat().st_ino != inode
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    note = (run.directory / "note.md").read_text()
    before, after = note.split("## Dismissed in review")
    assert "Allergy:" not in before and "Allergy:" in after
    assert "FICTIONAL duplicate" in after
    assert all((run.directory / name).read_bytes() == data for name, data in original.items())
    (event,) = read_events(run.log_path)
    assert event.reviewer == "FICTIONAL Reviewer" and event.role == "fictional clinician"
    assert event.target_sha256 == resource_digest(outcome.resource)
    assert event.reason == "FICTIONAL duplicate" and event.diff
    assert datetime.fromisoformat(event.timestamp).utcoffset().total_seconds() == 0
    assert stat.S_IMODE(run.log_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(run.directory.stat().st_mode) == 0o700


def test_undo_is_append_only_revocation_and_reopens_export_gate(map_case, tmp_path):
    mapper, _, outcome = map_case("new-order")
    run = prepare(tmp_path / "run", mapper, [outcome])
    act(run, outcome, "approve")
    first = run.log_path.read_bytes()
    act(run, outcome, "dismiss", "FICTIONAL duplicate")
    second = run.log_path.read_bytes()
    assert second.startswith(first)
    state = act(run, outcome, "undo")
    assert state["ready"] and state["export"]["exported_ids"] == [outcome.id]
    assert run.log_path.read_bytes().startswith(second)
    entries = read_events(run.log_path)
    assert entries[2].action == "undo" and entries[2].revokes == entries[1].entry_id
    state = act(run, outcome, "undo")
    assert not state["ready"] and state["export"]["blocking_ids"] == [outcome.id]
    assert read_events(run.log_path)[-1].revokes == entries[0].entry_id
    with pytest.raises(ValueError):
        act(run, outcome, "undo")


def test_changed_resource_stales_approval_and_dismissal(map_case, tmp_path):
    mapper, _, outcome = map_case("new-order")
    run = prepare(tmp_path / "run", mapper, [outcome])
    act(run, outcome, "approve")
    act(run, outcome, "dismiss", "FICTIONAL duplicate")
    modified = outcome.model_copy(deep=True)
    modified.resource["note"] = [{"text": "FICTIONAL regenerated version"}]
    run = prepare(run.directory, mapper, [modified])
    state = run.rewrite()
    assert not state["ready"] and state["export"]["blocking_ids"] == [outcome.id]
    row = state["review"][0]
    assert row["state"] == "unreviewed" and len(row["stale_entries"]) == 2
    assert "no longer applies" in row["stale_message"]
    assert "undo" not in row["allowed_actions"]
    with pytest.raises(ValueError, match="version changed"):
        act(run, outcome, "approve")
    act(run, modified, "approve")
    assert run.rewrite()["ready"]


def test_approval_cannot_complete_unchecked_resource_lanes(map_case, tmp_path):
    mapper, _, outcome = map_case("new-order")
    run = prepare(
        tmp_path / "pending",
        mapper,
        [outcome],
        validation=FakeValidator(failure="FICTIONAL checker unavailable").validate(
            [
                *[
                    getattr(mapper.anchors, name)
                    for name in ("patient", "practitioner", "encounter")
                ],
                outcome.resource,
            ]
        ),
    )
    assert not act(run, outcome, "approve")["ready"]


@pytest.mark.parametrize("case", ["symptom-affirmed", "new-order"])
def test_duplicate_approval_refused_without_receipt(map_case, tmp_path, case):
    mapper, _, outcome = map_case(case)
    run = prepare(tmp_path / "run", mapper, [outcome])
    state = act(run, outcome, "approve")
    assert state["review"][0]["allowed_actions"] == ["dismiss", "undo"]
    original = run.log_path.read_bytes()
    # Another server/session must reload and refuse this same version too.
    with pytest.raises(ValueError, match="already approved; choose Dismiss or Undo"):
        act(ReviewRun(run.directory), outcome, "approve")
    assert run.log_path.read_bytes() == original
    assert len(read_events(run.log_path)) == 1
    state = act(run, outcome, "undo")
    assert "approve" in state["review"][0]["allowed_actions"]
    act(run, outcome, "approve")
    assert len(read_events(run.log_path)) == 3


@pytest.mark.parametrize(
    "case,changes",
    [
        ("patient_reported_condition", {"experiencer": "family_member"}),
        ("new-order", {"concept_text": None}),
        ("new-order", {"medication_intent": "discussed"}),
        ("new-order", {"temporality": "past"}),
        ("new-order", {"source_speaker_role": None}),
        ("new-order", {"route_text": "not in the quote"}),
        ("clinician_assessed_problem", {"source_speaker_role": None}),
        ("heart-rate", {"polarity": None}),
        ("heart-rate", {"value_text": None}),
        ("blood-pressure", {"value_text": "128"}),
        ("body-temperature", {"unit_text": None}),
        ("body-temperature", {"unit_text": "mmHg"}),
        ("heart-rate", {"temporality": "past"}),
        ("allergy-ingredient", {"allergen_text": None}),
        # Unsafe evidence remains dismiss-only, even for resource-less items.
        ("new-order", {"dose_text": "9999 milligrams"}),
        ("heart-rate", {"value_text": "9999", "temporality": "past"}),
    ],
)
def test_resourceless_review_resolves_only_to_unchanged_ledger(map_case, tmp_path, case, changes):
    mapper, _, outcome = map_case(case, changes=changes)
    assert outcome.resource is None and not outcome.ledger_only
    run = prepare(tmp_path / "run", mapper, [outcome])
    source = (run.directory / "extraction.json").read_bytes()
    ledger = copy.deepcopy(outcome.ledger)
    before = run.rewrite()
    if LaneState.FAIL in outcome.lanes.model_dump().values():
        assert run.allowed_actions(outcome) == ["dismiss"]
        assert "checks failed" in before["items"][0]["label"]
        with pytest.raises(ValueError, match="cannot override any FAIL"):
            act(run, outcome, "approve")
        assert act(run, outcome, "dismiss", "FICTIONAL unsafe extraction")["ready"]
    else:
        if outcome.is_material:
            assert before["export"]["blocking_ids"] == [outcome.id]
        state = act(run, outcome, "approve")
        assert state["ready"] and not state["export"]["blocking_ids"]
        assert state["export"]["exported_ids"] == []
        (item,) = state["items"]
        assert item["export_state"] == "ledger_only" and item["in_ledger"]
        assert not item["reasons"] and "needs clinician review" not in item["label"]
        assert "ledger only" in item["label"] and "clinician-approved" in item["label"]
        assert read_events(run.log_path)[0].target_sha256 == resource_digest(ledger)
        undone = act(run, outcome, "undo")
        assert undone["items"][0]["export_state"] == "needs_review"
        assert undone["export"]["blocking_ids"] == before["export"]["blocking_ids"]
    assert run.targets[outcome.id].resource is None
    assert run.targets[outcome.id].ledger == ledger
    assert (run.directory / "extraction.json").read_bytes() == source
    bundle = json.loads((run.directory / "bundle.json").read_text())
    assert len(bundle["entry"]) == 3  # Unchanged intake anchors only.


def test_changed_ledger_stales_resourceless_approval(map_case, tmp_path):
    mapper, _, outcome = map_case(
        "patient_reported_condition", changes={"experiencer": "family_member"}
    )
    run = prepare(tmp_path / "run", mapper, [outcome])
    assert act(run, outcome, "approve")["ready"]
    changed = outcome.model_copy(deep=True)
    changed.ledger["temporality"] = "past"
    run = prepare(run.directory, mapper, [changed])
    state = run.rewrite()
    assert not state["ready"] and state["export"]["blocking_ids"] == [outcome.id]
    assert state["review"][0]["stale_entries"]
    assert state["items"][0]["export_state"] == "needs_review"
    with pytest.raises(ValueError, match="version changed"):
        act(run, outcome, "approve")
    assert act(run, changed, "approve")["ready"]


def test_quarantine_reuses_step10_receipt_and_undo(map_case, tmp_path):
    mapper, _, outcome = map_case("heart-rate")
    item = QuarantinedItem(
        call_index=0,
        window_id="0",
        position=1,
        transcript_sha256=mapper.transcript.content_hash,
        raw_item={"FICTIONAL malformed": True},
        errors=(ItemError(type="missing", location=("evidence",)),),
    )
    run = prepare(tmp_path / "run", mapper, [outcome], quarantines=[item])
    assert run.allowed_actions(item) == ["dismiss"]
    with pytest.raises(ValueError):
        act(run, item, "approve")
    receipt = review_quarantined_item(
        item,
        action="dismiss",
        reason="FICTIONAL malformed duplicate",
        reviewer="FICTIONAL Reviewer",
        role="fictional clinician",
        log_path=run.log_path,
    )
    run.load()
    assert run.rewrite()["ready"]
    assert run.current(item).target_sha256 == receipt.target_sha256
    before = run.log_path.read_bytes()
    assert not act(run, item, "undo")["ready"]
    assert run.log_path.read_bytes().startswith(before)
    assert act(run, item, "dismiss", "FICTIONAL invalid sibling")["ready"]
    assert "malformed item withheld — dismissed by" in (run.directory / "note.md").read_text()


@pytest.fixture
def fictional_run(terminology, units, tmp_path):
    transcript, intake, assertions = demo_input()
    config = load_frozen_config().extractor
    extraction = replay(transcript, config, config.candidate(), assertions)
    directory = tmp_path / "fictional-run"
    finish_run(
        extraction,
        Mapper(intake, transcript, terminology, units),
        FakeValidator(),
        directory,
        fictional_demo=True,
    )
    return ReviewRun(directory)


def test_full_fictional_run_reviewed_to_completion(fictional_run):
    run = fictional_run
    directory = run.directory
    original = (directory / "extraction.json").read_bytes()
    assert not run.rewrite()["ready"]
    expected = {o.id for o in run.outcomes if o.resource is not None}
    for target in tuple(run.targets.values()):
        assert "approve" in run.allowed_actions(target)
        act(run, target, "approve")
    state = run.rewrite()
    assert state["ready"] and not state["export"]["blocking_ids"]
    bundle = json.loads((directory / "bundle.json").read_text())
    actual = {entry["resource"]["id"] for entry in bundle["entry"][3:]}
    assert actual == expected == set(state["export"]["exported_ids"])
    assert len(actual) == 7
    assert len(read_events(run.log_path)) == len(run.targets) == 13
    family = next(item for item in state["items"] if item["reference"] == "A2")
    assert family["export_state"] == "ledger_only" and not family["reasons"]
    assert "needs clinician review" not in family["label"]
    assert "## Dismissed in review" not in (directory / "note.md").read_text()
    assert (directory / "extraction.json").read_bytes() == original
    assert "validated" not in (directory / "note.md").read_text().replace("not validated", "")


def test_page_blocker_counts_badges_and_default_quotes(fictional_run):
    node = shutil.which("node")
    if node is None:
        pytest.skip("existing Node runtime unavailable for inline JavaScript DOM probe")
    session = ReviewSession(fictional_run.directory)
    request(session, "POST", "/api/reviewer", {"name": "FICTIONAL Reviewer", "role": "clinician"})

    def snapshot():
        return json.loads(request(session, "GET", "/api/state")[1])

    initial = snapshot()
    assert len(initial["export"]["blocking_ids"]) == 7
    assert sum(bool(item["reasons"]) for item in initial["items"]) == 10
    single = None
    for target in tuple(session.run.targets.values()):
        act(session.run, target, "approve")
        if len(session.run.rewrite()["export"]["blocking_ids"]) == 1:
            single = snapshot()
    assert single is not None
    ready = snapshot()
    assert ready["ready"]
    # Also exercise multiple always-visible quotes and the alignment note.
    evidence = initial["evidence"][initial["items"][0]["id"]]
    evidence.append(
        {**evidence[0], "quote": "FICTIONAL second quote <inert>", "alignment": "disfluency-v2"}
    )
    script = PAGE.decode().split("<script>")[1].split("</script>")[0]
    probe = (Path(__file__).parent / "review_page_probe.js").read_text()
    result = subprocess.run(
        [node],
        input="const snapshots = "
        + json.dumps([initial, single, ready])
        + ";\n"
        + probe.replace("/* PAGE SCRIPT */", script.removesuffix("load();\n")),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def request(session, method, path, data=None, headers=None):
    handler = object.__new__(handler_for(session))
    handler.server = type("FakeServer", (), {"server_port": 12345})()
    handler.command, handler.path = method, path
    body = json.dumps(data).encode() if data is not None else b""
    handler.headers = {
        "Host": "127.0.0.1:12345",
        "Origin": "http://127.0.0.1:12345",
        "Content-Length": str(len(body)),
        "X-Auris-Token": session.token,
        **(headers or {}),
    }
    handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
    statuses, response_headers = [], {}
    handler.send_response = statuses.append
    handler.send_header = response_headers.__setitem__
    handler.end_headers = lambda: None
    handler.respond()
    handler.log_message("FICTIONAL transcript must not log")
    return statuses[0], handler.wfile.getvalue(), response_headers


def test_session_http_protections_and_context_without_sockets(map_case, tmp_path, capsys):
    mapper, _, outcome = map_case("new-order")
    run = prepare(tmp_path / "run", mapper, [outcome])
    session = ReviewSession(run.directory)
    status, shell, headers = request(session, "GET", "/")
    assert status == 200 and shell == PAGE
    assert headers["Content-Security-Policy"] == CSP
    assert "unsafe-inline" not in CSP and "sha256-" in CSP and "default-src 'none'" in CSP
    assert headers["Cache-Control"] == "no-store"
    for headers in (
        {"Host": "evil.invalid"},
        {"Origin": "https://evil.invalid"},
        {"X-Auris-Token": "wrong"},
        {"X-Auris-Token": ""},
    ):
        assert request(session, "GET", "/api/state", headers=headers)[0] == 403
    assert request(session, "POST", "/api/reviewer", headers={"Origin": None})[0] == 403
    assert (
        request(session, "POST", "/api/reviewer", headers={"Content-Length": "3000000"})[0] == 413
    )
    for path in (
        "/note.md",
        "/bundle.json",
        "/extraction.json",
        "/api/file?path=/etc/passwd",
        "/../review-inputs.json",
    ):
        assert request(session, "GET", path)[0] == 404
    action = {"target_id": outcome.id, "target_sha256": target_digest(outcome), "action": "approve"}
    assert request(session, "POST", "/api/action", action)[0] == 403
    assert request(session, "POST", "/api/reviewer", {"name": " ", "role": "clinician"})[0] == 422
    assert (
        request(
            session, "POST", "/api/reviewer", {"name": "FICTIONAL Reviewer", "role": "clinician"}
        )[0]
        == 200
    )
    assert (
        request(session, "POST", "/api/reviewer", {"name": "Different", "role": "clinician"})[0]
        == 422
    )
    assert request(session, "POST", "/api/action", {**action, "lanes": {}})[0] == 422
    assert request(session, "POST", "/api/action", action)[0] == 200
    state = json.loads(request(session, "GET", "/api/state")[1])
    assert state["ready"]
    before = session.run.log_path.read_bytes()
    status, error, _ = request(session, "POST", "/api/action", action)
    assert status == 422 and "already approved" in json.loads(error)["error"]
    assert session.run.log_path.read_bytes() == before
    context = state["evidence"][outcome.id][0]
    assert context["quote"] == outcome.evidence.items[0].quote
    assert any(segment["cited"] for segment in context["neighbors"])
    assert len(context["neighbors"]) <= 3
    assert request(session, "GET", "/api/outputs")[0] == 200
    assert capsys.readouterr().out == ""
    assert capsys.readouterr().err == ""


def test_server_uses_loopback_random_port_and_fragment(map_case, tmp_path, monkeypatch, capsys):
    mapper, _, outcome = map_case("heart-rate")
    run = prepare(tmp_path / "run", mapper, [outcome])
    session = ReviewSession(run.directory)
    token = session.token
    fake = Mock()
    fake.__enter__ = Mock(return_value=fake)
    fake.__exit__ = Mock()
    fake.server_port = 12345
    fake.serve_forever.side_effect = KeyboardInterrupt
    constructor = Mock(return_value=fake)
    monkeypatch.setattr("auris.review_web.server.HTTPServer", constructor)
    serve(session)
    assert constructor.call_args.args[0] == BIND_ADDRESS == ("127.0.0.1", 0)
    assert f"http://127.0.0.1:12345/#token={token}" in capsys.readouterr().out
    assert not session.token


def test_log_refuses_symlink_or_shared_permissions(map_case, tmp_path):
    mapper, _, outcome = map_case("new-order")
    run = prepare(tmp_path / "run", mapper, [outcome])
    other = tmp_path / "untouched.jsonl"
    other.write_text("FICTIONAL untouched")
    run.log_path.symlink_to(other)
    with pytest.raises(OSError):
        act(run, outcome, "approve")
    assert other.read_text() == "FICTIONAL untouched"
    run.log_path.unlink()
    run.log_path.write_text("")
    run.log_path.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        act(run, outcome, "approve")


@pytest.mark.parametrize("untimed", [False, True])
def test_quote_context_has_both_neighbors_and_disfluency_receipt(
    terminology, units, intake, tmp_path, untimed
):
    from extract_helpers import case_input

    from auris.assertions import EvidenceItem
    from auris.transcript import Transcript, TranscriptSegment

    _, assertion = case_input("symptom")
    texts = ["FICTIONAL preceding segment.", "I um have a cough.", "FICTIONAL following segment."]
    segments = tuple(
        TranscriptSegment(
            id=f"s{i}",
            speaker_cluster_id="fictional-patient",
            role="patient",
            text=text,
            start_seconds=None if untimed else 10 * i,
            end_seconds=None if untimed else 10 * i + 5,
            turn_number=i if untimed else None,
        )
        for i, text in enumerate(texts, 1)
    )
    transcript = Transcript(
        schema_version="2" if untimed else "1",
        segments=segments,
        timing_mode="untimed" if untimed else "timed",
        timing_source="none" if untimed else "unspecified",
    )
    assertion = assertion.model_copy(
        update={"evidence": (EvidenceItem(segment_id="s2", quote="I have a cough"),)}
    )
    mapper = Mapper(intake, transcript, terminology, units)
    outcome = mapper.map_assertion(assertion, 0)
    run = prepare(tmp_path / "run", mapper, [outcome])
    app = ReviewSession(run.directory)
    status, payload, _ = request(app, "GET", "/api/state")
    assert status == 200
    context = json.loads(payload)["evidence"][outcome.id][0]
    assert context["alignment"] == "disfluency-v2"
    assert [s["id"] for s in context["neighbors"]] == ["s1", "s2", "s3"]
    assert [s["cited"] for s in context["neighbors"]] == [False, True, False]
    assert context["receipt"] == ("[turn 2 · patient]" if untimed else "[00:20 · patient]")
    assert "matched ignoring fillers/repeats" in (run.directory / "note.md").read_text()


def test_review_cli_and_invalid_inputs_do_not_print_private_values(
    map_case, tmp_path, monkeypatch, capsys
):
    from auris.cli import main

    mapper, _, outcome = map_case("heart-rate")
    run = prepare(tmp_path / "run", mapper, [outcome])
    calls = []
    monkeypatch.setattr("auris.review_web.server.serve", calls.append)
    assert main(["review", "serve", "--run", str(run.directory)]) == 0
    assert calls[0].run.directory == run.directory
    write_private_text(
        run.directory / "review-inputs.json", json.dumps({"intake": "FICTIONAL PRIVATE SENTINEL"})
    )
    assert main(["review", "serve", "--run", str(run.directory)]) == 1
    output = capsys.readouterr()
    assert "invalid review artifacts" in output.err
    assert "PRIVATE SENTINEL" not in output.err + output.out


def test_failed_atomic_replacement_preserves_existing_file_and_durable_receipt(
    map_case, tmp_path, monkeypatch
):
    mapper, _, outcome = map_case("new-order")
    run = prepare(tmp_path / "run", mapper, [outcome])
    run.rewrite()
    original = (run.directory / "note.md").read_bytes()
    replace = Path.replace
    with monkeypatch.context() as patch:

        def fail(source, target):
            raise OSError("FICTIONAL disk failure")

        patch.setattr(Path, "replace", fail)
        with pytest.raises(OSError, match="disk failure"):
            act(run, outcome, "approve")
    assert (run.directory / "note.md").read_bytes() == original
    assert len(read_events(run.log_path)) == 1
    assert sorted(p.name for p in run.directory.iterdir()) == [
        "bundle.json",
        "extraction.json",
        "note.md",
        "review-inputs.json",
        "review.jsonl",
        "validation.json",
    ]
    assert Path.replace is replace
    assert ReviewRun(run.directory).rewrite()["ready"]


def test_approval_resolves_ledger_review_flag_without_making_an_order(map_case, tmp_path):
    mapper, _, outcome = map_case(
        "reported-medication-affirmed", changes={"concept_text": "FICTIONAL alternate concept"}
    )
    assert outcome.ledger_only and outcome.export_state == "needs_review"
    run = prepare(tmp_path / "run", mapper, [outcome])
    state = act(run, outcome, "approve")
    assert state["ready"] and state["export"]["exported_ids"] == []
    assert state["items"][0]["export_state"] == "ledger_only"
    assert state["items"][0]["reasons"] == []
    assert run.outcomes[0].reviewed_resource_digest is None
    assert "FICTIONAL alternate concept" in run.outcomes[0].ledger["concept_text"]


def test_regenerated_target_identity_reports_obsolete_review(map_case, tmp_path):
    mapper, _, outcome = map_case("new-order")
    run = prepare(tmp_path / "run", mapper, [outcome])
    act(run, outcome, "approve")
    changed = outcome.model_copy(deep=True, update={"id": "fictional-regenerated-target"})
    changed.resource["id"] = changed.id
    run = prepare(run.directory, mapper, [changed])
    state = run.rewrite()
    assert state["stale_review_count"] == 1
    assert not state["ready"]
    report = json.loads((run.directory / "validation.json").read_text())
    assert len(report["review"]["stale_entries"]) == 1


@pytest.mark.parametrize("transcript_hash", [None, "0" * 64])
def test_quarantine_must_be_bound_to_this_visit(map_case, tmp_path, transcript_hash):
    mapper, _, outcome = map_case("heart-rate")
    item = QuarantinedItem(
        call_index=0,
        window_id="0",
        position=1,
        raw_item="FICTIONAL malformed item",
        errors=(ItemError(type="missing", location=("evidence",)),),
        transcript_sha256=transcript_hash,
    )
    with pytest.raises(ValueError, match="this visit's transcript hash"):
        prepare(tmp_path / "run", mapper, [outcome], quarantines=[item])


def test_failure_wording_support_codes_and_validator_disclosures(map_case, tmp_path):
    from auris.render.reasons import REASON_LABELS

    mapper, _, outcome = map_case("heart-rate", changes={"value_text": "9999"})
    item = QuarantinedItem(
        call_index=0,
        window_id="fictional-window",
        position=1,
        transcript_sha256=mapper.transcript.content_hash,
        raw_item={"kind": "FICTIONAL unsupported kind"},
        errors=(ItemError(type="union_tag_invalid", location=()),),
    )
    run = prepare(tmp_path / "run", mapper, [outcome], quarantines=[item])
    session = ReviewSession(run.directory)
    status, state = session.dispatch("GET", "/api/state", session.token, b"")
    assert status == 200
    assert state["reason_labels"] == REASON_LABELS
    assert "value_text_number_not_in_quote" in state["reason_codes"][outcome.id]
    note = (run.directory / "note.md").read_text()
    assert REASON_LABELS["value_text_number_not_in_quote"] in note
    assert "value_text_number_not_in_quote" not in note
    # The real context path carries an evidence failure as a support code.
    state["evidence"][outcome.id][0]["failure"] = "quote_not_found"
    node = shutil.which("node")
    if node is None:
        pytest.skip("existing Node runtime unavailable for inline JavaScript DOM probe")
    script = PAGE.decode().split("<script>")[1].split("</script>")[0]
    probe = (Path(__file__).parent / "review_polish_probe.js").read_text()
    result = subprocess.run(
        [node],
        input="const snapshot = "
        + json.dumps(state)
        + ";\n"
        + probe.replace("/* PAGE SCRIPT */", script.removesuffix("load();\n")),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
