"""Desktop engine: writing a note in the background, review and export.

The pipeline is the real `auris run` code path with the model and validator
replaced by the fictional demo's recorded replay (tests/demo_helpers.py).
"""

import json
import stat
from datetime import date
from pathlib import Path

import pytest
from demo_helpers import patch_cli
from test_app import request

from auris.app.api import AppSession
from auris.app.attention import APPROVAL_REASONS, ROUTINE_REASONS
from auris.app.demo import demo_transcript, seed_demo_day
from auris.app.jobs import INTERRUPTED, NoteJobs, subprocess_runner
from auris.app.store import VisitState, VisitStore
from auris.app.visit_intake import build_intake
from auris.cli import main
from auris.transcript import Transcript

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 11, 2)


def in_process_runner(transcript, intake, out, log):
    """`auris run` in this process (the CLI is patched with the demo replay)."""
    return main(
        ["run", "--transcript", str(transcript), "--intake", str(intake), "--out", str(out)]
    )


@pytest.fixture
def engine(tmp_path, monkeypatch, terminology, units):
    patch_cli(monkeypatch, terminology, units)
    store = VisitStore(tmp_path / "data")
    store.set_clinician("Dr Tobin Hale")
    seed_demo_day(store, DAY)
    jobs = NoteJobs(tmp_path / "data", store, in_process_runner)
    session = AppSession(
        store,
        jobs=jobs,
        demo=True,
        demo_transcript=demo_transcript(ROOT),
        token="t" * 43,
        today=lambda: DAY,
    )
    return session, jobs, store


def mara(store):
    return next(v for v in store.visits_on(DAY) if v.patient == "Mara Quill")


def call(session, method, path, data=None, raw=None):
    status, body, _ = request(session, method, path, data, raw=raw)
    return status, json.loads(body)


def test_intake_is_built_from_the_visit_only(engine):
    _, _, store = engine
    intake = build_intake(mara(store), "Dr Tobin Hale")
    assert intake.patient.name[0].family == "Quill" and intake.patient.name[0].given == ("Mara",)
    assert intake.practitioner.name[0].family == "Hale"
    assert intake.patient.gender == "unknown" and intake.fictional
    assert intake.encounter.type[0].text == "Cough for four days"
    assert intake.encounter.period.start.startswith("2026-11-02T09:00:00")
    assert intake.patient.identifier[0].system == "urn:ietf:rfc:3986"
    assert intake.encounter.subject.reference == f"Patient/{intake.patient.id}"


def test_demo_note_is_written_then_reviewed_and_exported(engine):
    session, jobs, store = engine
    visit = mara(store)
    status, body = call(session, "POST", f"/api/visits/{visit.id}/demo-transcript")
    assert status == 202 and body["job"]["state"] == "queued"
    assert store.get(visit.id).state == VisitState.WRITING
    folder = jobs.folder(visit.id)
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert stat.S_IMODE((folder / "transcript.json").stat().st_mode) == 0o600

    jobs.run_one(visit.id)
    assert store.get(visit.id).state == VisitState.READY
    status, body = call(session, "GET", f"/api/visits/{visit.id}")
    assert body["job"]["state"] == "done"
    assert [s["state"] for s in body["job"]["stages"]] == ["done"] * 4

    status, state = call(session, "GET", f"/api/visits/{visit.id}/review/state")
    assert status == 200 and state["reviewer"]["name"] == "Dr Tobin Hale"
    assert state["items"] and not state["ready"]
    # Routine reasons are notes; only specific doubts ask for a closer look.
    assert "paths" not in state
    assert set(state["attention"]) == set(state["notes"]) == set(state["reason_codes"])
    for item_id, codes in state["reason_codes"].items():
        routine = [c for c in codes if c in ROUTINE_REASONS]
        quiet = [c for c in routine if c not in APPROVAL_REASONS]
        assert len(state["notes"][item_id]) == len(quiet)
        assert len(state["attention"][item_id]) == len(codes) - len(routine)
    assert any(state["attention"].values()) and any(state["notes"].values())

    def listed():
        return next(
            v for v in call(session, "GET", "/api/visits")[1]["visits"] if v["id"] == visit.id
        )

    assert listed()["review_complete"] is False

    # Export is refused until every record is decided (every-record-approved-v1).
    status, body = call(session, "POST", f"/api/visits/{visit.id}/export")
    assert status == 422
    assert store.get(visit.id).state == VisitState.READY

    from auris.review_state import target_digest

    review = session._review_session(visit.id)
    for target_id in state["export"]["blocking_ids"]:
        target = review.run.targets[target_id]
        failed = any(lane["state"] == "FAIL" for lane in state["lanes"].get(target_id, {}).values())
        decision = "dismiss" if failed else "approve"
        status, _ = call(
            session,
            "POST",
            f"/api/visits/{visit.id}/review/action",
            {
                "target_id": target_id,
                "target_sha256": target_digest(target),
                "action": decision,
                "reason": "FICTIONAL test decision" if decision == "dismiss" else "",
            },
        )
        assert status == 200, target_id

    assert listed()["review_complete"] is True
    status, body = call(session, "POST", f"/api/visits/{visit.id}/export")
    assert status == 200, body
    assert set(body["outputs"]) == {"note.md", "bundle.json", "validation.json"}
    assert json.loads(body["outputs"]["bundle.json"])["resourceType"] == "Bundle"
    assert store.get(visit.id).state == VisitState.EXPORTED

    # An exported visit is read-only until reopened.
    status, _ = call(
        session,
        "POST",
        f"/api/visits/{visit.id}/review/action",
        {"target_id": "x", "target_sha256": "0" * 64, "action": "undo"},
    )
    assert status == 409
    assert call(session, "GET", f"/api/visits/{visit.id}/review/state")[0] == 200
    status, body = call(session, "POST", f"/api/visits/{visit.id}/reopen")
    assert status == 200 and body["visit"]["state"] == "ready"


def test_failed_note_reports_the_pipeline_error_and_can_retry(engine, tmp_path):
    session, jobs, store = engine
    visit = mara(store)

    def failing(transcript, intake, out, log):
        log.write_text("loading\nerror: model file missing; run auris setup model\n")
        return 1

    jobs.runner = failing
    call(session, "POST", f"/api/visits/{visit.id}/demo-transcript")
    jobs.run_one(visit.id)
    failed = store.get(visit.id)
    assert failed.state == VisitState.FAILED
    assert failed.failure == "model file missing; run auris setup model"
    status, body = call(session, "GET", f"/api/visits/{visit.id}")
    assert body["job"]["state"] == "failed" and body["job"]["error"] == failed.failure

    jobs.runner = in_process_runner
    status, body = call(session, "POST", f"/api/visits/{visit.id}/retry")
    assert status == 202 and body["job"]["attempt"] == "attempt-2"
    jobs.run_one(visit.id)
    assert store.get(visit.id).state == VisitState.READY


def test_runner_exceptions_never_stop_the_worker(engine):
    session, jobs, store = engine
    visit = mara(store)

    def crashing(*args):
        raise RuntimeError("boom")

    jobs.runner = crashing
    call(session, "POST", f"/api/visits/{visit.id}/demo-transcript")
    jobs.run_one(visit.id)
    assert store.get(visit.id).state == VisitState.FAILED


def test_interrupted_note_is_recovered_on_start(engine):
    session, jobs, store = engine
    visit = mara(store)
    call(session, "POST", f"/api/visits/{visit.id}/demo-transcript")
    # The app closed before the worker ran: the visit is left writing.
    restarted = NoteJobs(jobs.runs.parent, store, in_process_runner)
    restarted.recover()
    assert store.get(visit.id).state == VisitState.FAILED
    assert store.get(visit.id).failure == INTERRUPTED


def test_transcript_and_route_guards(engine):
    session, jobs, store = engine
    visit = mara(store)
    real = store.add_visit("Real Patient", "", DAY, "11:00")
    assert call(session, "POST", f"/api/visits/{real.id}/demo-transcript")[0] == 403
    assert call(session, "GET", f"/api/visits/{visit.id}/review/state")[0] == 409
    assert call(session, "POST", f"/api/visits/{visit.id}/export")[0] == 409
    assert call(session, "POST", f"/api/visits/{visit.id}/retry")[0] == 422
    assert call(session, "GET", "/api/visits/" + "0" * 32)[0] == 404
    assert call(session, "GET", f"/api/visits/{visit.id}/nope")[0] == 404
    assert call(session, "GET", f"/api/visits/{visit.id}/review/action")[0] == 409
    bad = json.dumps({"schema_version": "1", "segments": []}).encode()
    assert call(session, "POST", f"/api/visits/{real.id}/transcript", raw=bad)[0] == 422
    transcript = Transcript.load(demo_transcript(ROOT)).model_dump_json().encode()
    status, body = call(session, "POST", f"/api/visits/{real.id}/transcript", raw=transcript)
    assert status == 202
    assert call(session, "POST", f"/api/visits/{real.id}/transcript", raw=transcript)[0] == 422


def test_subprocess_runner_runs_python_m_auris(tmp_path, monkeypatch):
    calls = []

    class Done:
        returncode = 0

    monkeypatch.setattr(
        "auris.app.jobs.subprocess.run",
        lambda argv, **kwargs: calls.append((argv, kwargs)) or Done(),
    )
    runner = subprocess_runner(tmp_path)
    log = tmp_path / "job.log"
    assert runner(tmp_path / "t.json", tmp_path / "i.json", tmp_path / "out", log) == 0
    argv, kwargs = calls[0]
    assert argv[1:4] == ["-m", "auris", "run"] and "--out" in argv
    assert kwargs["cwd"] == tmp_path and kwargs["check"] is False
