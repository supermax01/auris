"""Desktop engine: the visit store, the API and the loopback server."""

import io
import json
import os
import stat
from datetime import date
from unittest.mock import Mock

import pytest

from auris.app.api import AppSession
from auris.app.demo import DEMO_DAY, seed_demo_day
from auris.app.server import BIND_ADDRESS, CSP, handler_for, serve
from auris.app.store import TRANSITIONS, VisitState, VisitStore
from auris.cli import main

DAY = date(2026, 11, 2)


@pytest.fixture
def store(tmp_path):
    return VisitStore(tmp_path / "data")


def session_for(store, **kwargs):
    return AppSession(store, token="t" * 43, today=lambda: DAY, **kwargs)


def request(session, method, path, data=None, headers=None, ui_dir=None, raw=None):
    handler = object.__new__(handler_for(session, ui_dir))
    handler.server = type("FakeServer", (), {"server_port": 12345})()
    handler.command, handler.path = method, path
    body = raw if raw is not None else json.dumps(data).encode() if data is not None else b""
    handler.headers = {
        "Host": "127.0.0.1:12345",
        "Origin": "http://127.0.0.1:12345",
        "Content-Length": str(len(body)),
        "X-Auris-Token": session.token,
        **(headers or {}),
    }
    handler.headers = {k: v for k, v in handler.headers.items() if v is not None}
    handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
    statuses, response_headers = [], {}
    handler.send_response = statuses.append
    handler.send_header = response_headers.__setitem__
    handler.end_headers = lambda: None
    handler.respond()
    return statuses[0], handler.wfile.getvalue(), response_headers


# --- Store -----------------------------------------------------------------


def test_store_is_private_and_reopens(tmp_path):
    folder = tmp_path / "data"
    store = VisitStore(folder)
    assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    visit = store.add_visit("  Mara   Quill ", "Cough", DAY, "09:00")
    assert visit.patient == "Mara Quill" and visit.state == VisitState.SCHEDULED
    again = VisitStore(folder)
    assert again.get(visit.id) == visit


def test_store_rejects_bad_input(store):
    for patient, time in (("", "09:00"), ("   ", "09:00"), ("A", "9:00"), ("A", "24:00")):
        with pytest.raises(ValueError):
            store.add_visit(patient, "", DAY, time)
    with pytest.raises(ValueError):
        store.add_visit("x" * 121, "", DAY, "09:00")


def test_visits_ordered_by_time_and_day(store):
    store.add_visit("Late", "", DAY, "10:30")
    store.add_visit("Early", "", DAY, "08:30")
    store.add_visit("Tomorrow", "", date(2026, 11, 3), "07:00")
    assert [v.patient for v in store.visits_on(DAY)] == ["Early", "Late"]


@pytest.mark.parametrize("start", list(VisitState))
def test_state_machine_allows_only_listed_moves(tmp_path, start):
    for target in VisitState:
        store = VisitStore(tmp_path / f"{start}-{target}")
        visit = store.add_visit("P", "", DAY, "09:00")
        path = {
            VisitState.SCHEDULED: [],
            VisitState.LISTENING: [VisitState.LISTENING],
            VisitState.WRITING: [VisitState.WRITING],
            VisitState.FAILED: [VisitState.WRITING, VisitState.FAILED],
            VisitState.READY: [VisitState.WRITING, VisitState.READY],
            VisitState.EXPORTED: [VisitState.WRITING, VisitState.READY, VisitState.EXPORTED],
        }[start]
        for step in path:
            store.move(visit.id, step)
        if target in TRANSITIONS[start]:
            assert store.move(visit.id, target).state == target
        else:
            with pytest.raises(ValueError, match="cannot move"):
                store.move(visit.id, target)


def test_no_visit_reaches_export_without_review(store):
    visit = store.add_visit("P", "", DAY, "09:00")
    for state in (VisitState.EXPORTED, VisitState.READY):
        with pytest.raises(ValueError):
            store.move(visit.id, state)
    with pytest.raises(KeyError):
        store.move("missing", VisitState.WRITING)


def test_failure_message_only_on_failed_visits(store):
    visit = store.add_visit("P", "", DAY, "09:00")
    store.move(visit.id, VisitState.WRITING)
    failed = store.move(visit.id, VisitState.FAILED, failure="model missing")
    assert failed.failure == "model missing"
    assert store.move(visit.id, VisitState.WRITING, failure="ignored").failure is None
    assert [v.id for v in store.in_state(VisitState.WRITING)] == [visit.id]


def test_schema_one_is_migrated(tmp_path):
    folder = tmp_path / "data"
    store = VisitStore(folder)
    with store._db() as db:
        db.execute("DROP TABLE visits")
        db.execute(
            "CREATE TABLE visits (id TEXT PRIMARY KEY, patient TEXT NOT NULL, reason TEXT NOT NULL "
            "DEFAULT '', day TEXT NOT NULL, time TEXT NOT NULL, state TEXT NOT NULL, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO visits VALUES ('a', 'Old Visit', '', '2026-11-02', '09:00', "
            "'scheduled', 'x', 'x')"
        )
        db.execute("PRAGMA user_version = 1")
    visit = VisitStore(folder).get("a")
    assert visit is not None and visit.patient == "Old Visit" and not visit.fictional


def test_newer_schema_is_refused(tmp_path):
    store = VisitStore(tmp_path / "data")
    with store._db() as db:
        db.execute("PRAGMA user_version = 99")
    with pytest.raises(ValueError, match="newer version"):
        VisitStore(tmp_path / "data")


def test_clinician_name(store):
    assert store.clinician() is None
    assert store.set_clinician("  Dr  Tobin Hale ") == "Dr Tobin Hale"
    assert store.clinician() == "Dr Tobin Hale"
    with pytest.raises(ValueError):
        store.set_clinician(" ")


def test_demo_day_fills_only_an_empty_day(store):
    assert seed_demo_day(store, DAY)
    visits = store.visits_on(DAY)
    assert len(visits) == len(DEMO_DAY)
    # Demo visits start scheduled and fictional: review only follows a real note.
    assert all(v.state == VisitState.SCHEDULED and v.fictional for v in visits)
    assert not seed_demo_day(store, DAY)
    assert len(store.visits_on(DAY)) == len(DEMO_DAY)


# --- API and server --------------------------------------------------------


def test_api_requires_the_token_and_loopback_origin(store):
    session = session_for(store)
    assert request(session, "GET", "/api/session")[0] == 200
    for headers in (
        {"X-Auris-Token": "wrong"},
        {"X-Auris-Token": ""},
        {"Host": "evil.invalid"},
        {"Origin": "https://evil.invalid"},
    ):
        assert request(session, "GET", "/api/session", headers=headers)[0] == 403
    assert (
        request(
            session,
            "POST",
            "/api/visits",
            {"patient": "P", "time": "09:00"},
            headers={"Origin": None},
        )[0]
        == 403
    )
    status, _, headers = request(
        session, "POST", "/api/visits", headers={"Content-Length": "2000000"}
    )
    assert status == 413
    assert headers["Content-Security-Policy"] == CSP
    assert "unsafe" not in CSP and "default-src 'none'" in CSP
    assert headers["Cache-Control"] == "no-store"


def test_api_session_visits_and_clinician(store):
    session = session_for(store, demo=True)
    seed_demo_day(store, DAY)
    body = json.loads(request(session, "GET", "/api/session")[1])
    assert body == {"clinician": None, "today": "2026-11-02", "demo": True}
    assert request(session, "POST", "/api/clinician", {"name": " "})[0] == 422
    assert request(session, "POST", "/api/clinician", {"name": "Dr Hale", "x": 1})[0] == 422
    status, body, _ = request(session, "POST", "/api/clinician", {"name": "Dr Tobin Hale"})
    assert status == 200 and json.loads(body)["clinician"] == "Dr Tobin Hale"
    visits = json.loads(request(session, "GET", "/api/visits")[1])
    assert visits["day"] == "2026-11-02" and len(visits["visits"]) == len(DEMO_DAY)
    assert visits["counts"]["scheduled"] == len(DEMO_DAY) and visits["counts"]["exported"] == 0
    assert all(visit["fictional"] for visit in visits["visits"])
    other = json.loads(request(session, "GET", "/api/visits?day=2026-11-03")[1])
    assert other["visits"] == []


def test_api_adds_visits_and_validates(store):
    session = session_for(store)
    status, body, _ = request(
        session,
        "POST",
        "/api/visits",
        {"patient": "Mara Quill", "reason": "Cough", "time": "09:00"},
    )
    assert status == 201
    visit = json.loads(body)["visit"]
    assert visit["day"] == "2026-11-02" and visit["state"] == "scheduled"
    for bad in (
        {"patient": "", "time": "09:00"},
        {"patient": "P", "time": "9am"},
        {"patient": "P", "time": "09:00", "state": "exported"},
    ):
        assert request(session, "POST", "/api/visits", bad)[0] == 422
    assert request(session, "GET", "/api/visits?day=not-a-date")[0] == 422
    assert request(session, "POST", "/api/visits", headers={"Content-Length": "3"})[0] == 422
    assert request(session, "GET", "/api/nope")[0] == 404


def test_static_files_are_allowlisted(store, tmp_path):
    ui = tmp_path / "dist"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<!doctype html><title>Auris</title>")
    (ui / "assets" / "index-abc123.js").write_text("console.log(1)")
    (ui / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("no")
    session = session_for(store)
    status, body, headers = request(session, "GET", "/", ui_dir=ui)
    assert status == 200 and b"<title>Auris" in body
    assert headers["Content-Type"].startswith("text/html")
    status, _, headers = request(session, "GET", "/assets/index-abc123.js", ui_dir=ui)
    assert status == 200 and headers["Content-Type"].startswith("text/javascript")
    assert request(session, "GET", "/favicon.svg", ui_dir=ui)[0] == 200
    for path in (
        "/../secret.txt",
        "/assets/../../secret.txt",
        "/assets/%2e%2e/secret.txt",
        "/secret.txt",
        "/assets/missing.js",
        "/assets/notes.txt",
    ):
        assert request(session, "GET", path, ui_dir=ui)[0] == 404, path
    assert request(session, "POST", "/", ui_dir=ui)[0] == 405


def test_missing_interface_shows_build_hint(store):
    status, body, _ = request(session_for(store), "GET", "/")
    assert status == 200 and b"npm --prefix ui run build" in body


def test_serve_prints_one_json_ready_event(store, monkeypatch, capsys):
    session = session_for(store)
    fake = Mock()
    fake.__enter__ = Mock(return_value=fake)
    fake.__exit__ = Mock()
    fake.server_port = 4321
    fake.serve_forever.side_effect = KeyboardInterrupt
    constructor = Mock(return_value=fake)
    monkeypatch.setattr("auris.app.server.ThreadingHTTPServer", constructor)
    serve(session, None, json_events=True, exit_with_parent=False)
    assert constructor.call_args.args[0] == BIND_ADDRESS == ("127.0.0.1", 0)
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event == {"event": "ready", "url": f"http://127.0.0.1:4321/#token={session.token}"}


def test_cli_app_serve_seeds_demo_into_the_data_folder(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("auris.app.server.serve", lambda *a, **k: calls.append((a, k)))
    assert main(["app", "serve", "--data", str(tmp_path / "d"), "--demo", "--json"]) == 0
    (session, ui_dir), kwargs = calls[0]
    assert session.demo and kwargs == {"json_events": True, "exit_with_parent": False}
    assert len(session.store.visits_on(date.today())) == len(DEMO_DAY)
