"""Fictional, authored encounters for the offline human-annotation kit."""

import copy
import io
import json
import stat
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import pytest
from dataset_helpers import corpus
from extract_helpers import fixture_transcript_texts
from pydantic import ValidationError
from terminology_helpers import write_archives

from auris.annotation.models import (
    AnnotationDocument,
    AnnotationFact,
    Assignment,
    Selection,
    Timing,
)
from auris.annotation.pilot import create_pilot, fictional_intake, pilot_ids
from auris.annotation.server import BIND_ADDRESS, PAGE, handler_for, serve
from auris.annotation.service import AnnotationSession
from auris.annotation.timing import EncounterClock
from auris.cli import main
from auris.datasets.splits import PublicSplit, SplitEncounter
from auris.eval.gold import convert_annotation, digest
from auris.extract.leakage import check_leakage
from auris.intake import Intake
from auris.terminology.index import build_index
from auris.terminology.text import ICD10CM, RXNORM
from auris.transcript import Transcript

ROOT = Path(__file__).resolve().parents[2]
GUIDELINES = b"Fictional test guideline version."


def prepare(tmp_path, *, split="dev", count=12, dataset_id="primock57"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    cache = tmp_path / "cache"
    manifests = tmp_path / "manifests"
    manifests.mkdir(exist_ok=True)
    transcripts = corpus(count, split="all", dataset=dataset_id)
    entries = []
    folder = cache / "datasets/prepared" / dataset_id
    folder.mkdir(parents=True, mode=0o700)
    for i, (key, transcript) in enumerate(transcripts.items()):
        # Unicode before and within evidence detects UTF-16/UTF-8 offset mistakes.
        raw = transcript.model_dump(mode="json")
        raw["segments"][0]["text"] = f"Fictional {i} 🩺 café e\u0301 feels stiff. " + "word " * i
        transcript = Transcript.model_validate(raw)
        transcripts[key] = transcript
        transcript.save(folder / f"{key}.json")
        entries.append(
            SplitEncounter(
                id=key,
                transcript_sha256=transcript.content_hash,
                text_sha256=digest(str(i).encode()),
                upstream_split="all",
                variant="fictional-fixture",
                human_transcription=True,
                speaker_tags="source roles; not adjudicated",
                word_count=i + 10,
                length_stratum=("short", "medium", "long")[i * 3 // count],
            )
        )
    dataset = PublicSplit(
        id=dataset_id,
        split_id=f"fictional-{split}",
        split=split,
        seed=1729,
        source_manifest_sha256="0" * 64,
        encounters=entries,
    )
    (manifests / f"{dataset_id}-{split}.json").write_text(dataset.model_dump_json())
    assignment = Assignment(
        annotator_id="A",
        dataset=dataset,
        encounter_ids=tuple(transcripts),
        guidelines_sha256=digest(GUIDELINES),
    )
    return cache, manifests, assignment


def session(tmp_path, **kwargs):
    cache, manifests, assignment = prepare(tmp_path, **kwargs)
    return AnnotationSession(
        assignment,
        cache=cache,
        manifests=manifests,
        workdir=tmp_path / "work",
        guidelines=GUIDELINES,
    )


def call(app, method, path, data=None, *, token=None):
    return app.dispatch(
        method,
        path,
        app.token if token is None else token,
        json.dumps(data).encode() if data is not None else b"",
    )


def opened(app):
    key = app.assignment.encounter_ids[0]
    return key, call(app, "GET", "/api/encounter?" + urlencode({"id": key}))[1]


def selection(app, key):
    segment = app.transcripts[key].segments[0]
    start = segment.text.index("café")
    end = segment.text.index(".") + 1
    words = segment.text[start:end]
    return {
        "segment_id": segment.id,
        "char_start": start,
        "char_end": end,
        "text": words,
        "sha256": digest(words.encode()),
    }


def fact(app, key):
    data = selection(app, key)
    span = call(app, "POST", "/api/span", {"encounter_id": key, "selection": data})[1]
    return {
        "id": "fictional-fact",
        "kind": "symptom",
        "concept_text": "feels stiff",
        "polarity": "affirmed",
        "experiencer": "patient",
        "temporality": "current",
        "source_speaker_role": "patient",
        "evidence": [span],
        "gold_concept_id": "auris:fictional-stiffness",
        "acceptable_texts": ["feels stiff"],
        "critical": {},
        "notes": "Fictional annotation, not clinical gold.",
    }


def save_payload(key, opened, facts=(), **changes):
    return {
        "encounter_id": key,
        "status": "in_progress",
        "assertions": list(facts),
        "out_of_scope": [],
        "segment_roles": {s["id"]: s["role"] for s in opened["turns"]},
        "notes": "Fictional test",
        **changes,
    }


@pytest.mark.parametrize("route", ["/api/session", "/api/encounter?id=all-000", "/api/lookup?q=x"])
def test_token_required_on_every_api(tmp_path, route):
    app = session(tmp_path)
    for token in ("", "wrong", app.token[:-1], "é"):
        assert call(app, "GET", route, token=token)[0] == 403
    assert call(app, "GET", "/api/session")[0] == 200
    assert session(tmp_path / "second").token != app.token


def test_unicode_selection_roundtrip_and_tamper(tmp_path):
    app = session(tmp_path)
    key, _ = opened(app)
    chosen = selection(app, key)
    span = Selection.model_validate(chosen).span(app.transcripts[key])
    span.verify(app.transcripts[key])
    assert span.char_start < len(app.transcripts[key].segments[0].text[: span.char_start].encode())
    for change in (
        {"text": "tampered"},
        {"sha256": "0" * 64},
        {"char_start": chosen["char_start"] + 1},
        {"segment_id": "absent"},
        {"char_end": 10000},
        {"char_start": -1},
        {"char_start": 1.5},
        {"char_end": chosen["char_start"]},
    ):
        with pytest.raises(ValueError):
            Selection.model_validate({**chosen, **change}).span(app.transcripts[key])


@pytest.mark.parametrize(
    "change",
    [
        {"dose_text": "unsaid"},
        {"value_text": "4"},
        {"polarity": None},
        {"experiencer": "unknown"},
        {"acceptable_texts": [" "]},
        {"kind": "allergy_denial", "allergen_text": None, "polarity": "negated"},
        {"kind": "clinician_ruled_out_problem", "critical": {"condition_assertion": "absent"}},
    ],
)
def test_kind_and_gold_fields_validate(tmp_path, change):
    app = session(tmp_path)
    key, _ = opened(app)
    with pytest.raises(ValidationError):
        AnnotationFact.model_validate({**fact(app, key), **change})


def test_order_and_denial_critical_fields(tmp_path):
    app = session(tmp_path)
    key, _ = opened(app)
    base = fact(app, key)
    order = {
        **base,
        "kind": "explicit_new_order",
        "source_speaker_role": "clinician",
        "medication_intent": "new_order",
        "critical": {"order_authorization": "explicit"},
    }
    AnnotationFact.model_validate(order)
    for field, value in (
        ("source_speaker_role", "patient"),
        ("polarity", "negated"),
        ("critical", {}),
        ("medication_intent", "discussed"),
    ):
        with pytest.raises(ValueError):
            AnnotationFact.model_validate({**order, field: value})
    denial = {
        **base,
        "kind": "allergy_denial",
        "polarity": "negated",
        "critical": {"allergy_denial": "all"},
    }
    AnnotationFact.model_validate(denial)
    with pytest.raises(ValueError):
        AnnotationFact.model_validate({**denial, "polarity": "affirmed"})


def test_save_resume_private_atomic_and_convert(tmp_path, monkeypatch):
    app = session(tmp_path)
    key, state = opened(app)
    payload = save_payload(key, state, [fact(app, key)], status="done")
    result = call(app, "POST", "/api/save", payload)
    assert result[1]["document"]["status"] == "done"
    path = app.folder / f"{key}.annotation.json"
    original = path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(app.folder.stat().st_mode) == 0o700
    assert stat.S_IMODE(app.folder.parent.stat().st_mode) == 0o700
    saved = AnnotationDocument.model_validate_json(original)
    assert saved.annotator_id == "A" and saved.guidelines_sha256 == digest(GUIDELINES)
    assert '"text"' not in original.decode()  # Stand-off evidence, no stored quotes.
    facts, outside = convert_annotation(path, app.transcripts[key])
    assert facts[0].gold_concept_id == payload["assertions"][0]["gold_concept_id"]
    assert not outside
    resumed = AnnotationSession(
        app.assignment,
        cache=tmp_path / "cache",
        manifests=tmp_path / "manifests",
        workdir=tmp_path / "work",
        guidelines=GUIDELINES,
    )
    assert resumed.documents[key] == saved

    def fail_replace(*_):
        raise OSError("fictional interrupted write")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_replace)
        with pytest.raises(OSError):
            call(app, "POST", "/api/save", {**payload, "notes": "replacement"})
    assert path.read_bytes() == original
    assert sorted(p.name for p in app.folder.iterdir()) == [path.name]
    tampered = copy.deepcopy(payload)
    tampered["assertions"][0]["evidence"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        call(app, "POST", "/api/save", tampered)
    assert path.read_bytes() == original


def test_in_progress_not_gold_and_done_requires_role_decisions(tmp_path):
    app = session(tmp_path)
    key, state = opened(app)
    with pytest.raises(ValueError, match="completed"):
        convert_annotation(app._path(key), app.transcripts[key])
    with pytest.raises(ValueError, match="role"):
        call(app, "POST", "/api/save", save_payload(key, state, status="done", segment_roles={}))
    call(
        app,
        "POST",
        "/api/save",
        save_payload(
            key,
            state,
            out_of_scope=[
                {
                    "id": "fictional-referral",
                    "category": "referral",
                    "reason": "No referral kind",
                    "evidence": fact(app, key)["evidence"],
                    "notes": "Fictional only",
                }
            ],
            status="done",
        ),
    )
    _, outside = convert_annotation(app._path(key), app.transcripts[key])
    assert outside[0].category == "referral"


def test_only_local_index_codes_can_be_selected(tmp_path, monkeypatch):
    app = session(tmp_path)
    cache = tmp_path / "cache/terminology"
    config = write_archives(cache)
    build_index(cache / config.rxnorm.filename, cache / config.icd10cm.filename, app.index_path)
    before = app.index_path.read_bytes()
    from auris.terminology.index import TerminologyIndex

    def no_mapper_candidates(*_args, **_kwargs):
        pytest.fail("independent lookup must not use production mapper candidates")

    monkeypatch.setattr(TerminologyIndex, "candidates", no_mapper_candidates)
    with TerminologyIndex(app.index_path) as index:
        row = index.connection.execute("SELECT * FROM terms WHERE tty='IN' LIMIT 1").fetchone()
        query, code = row["display"], row["code"]
        expected = {r[0] for r in index.connection.execute("SELECT code FROM terms")}
    status, data = call(app, "GET", "/api/lookup?" + urlencode({"q": query, "system": RXNORM}))
    assert status == 200 and data["candidates"]
    assert all(row["code"] in expected for row in data["candidates"])
    key, state = opened(app)
    coded = {
        **fact(app, key),
        "gold_concept_id": f"{RXNORM}:{code}",
        "codes": [{"system": RXNORM, "code": code}],
    }
    call(app, "POST", "/api/save", save_payload(key, state, [coded]))
    from auris.eval.gold import Fact
    from auris.eval.matching import concept_equal

    call(app, "POST", "/api/save", save_payload(key, state, [coded], status="done"))
    converted, _ = convert_annotation(app._path(key), app.transcripts[key])
    prediction = Fact.model_validate(
        {
            field: value
            for field, value in converted[0].model_dump().items()
            if field in Fact.model_fields
        }
    ).model_copy(update={"concept_text": "unlisted fictional surface wording"})
    assert concept_equal(prediction, converted[0])  # Match solely through canonical code ID.
    assert not concept_equal(prediction.model_copy(update={"codes": ()}), converted[0])
    for changes in (
        {"gold_concept_id": f"{RXNORM}:00000000000000"},
        {
            "codes": [{"system": RXNORM, "code": "not-a-code"}],
            "gold_concept_id": f"{RXNORM}:not-a-code",
        },
    ):
        with pytest.raises(ValueError):
            call(app, "POST", "/api/save", save_payload(key, state, [{**coded, **changes}]))
    with pytest.raises(ValueError):
        app.lookup(query, "https://external.invalid")
    assert app.index_path.read_bytes() == before
    assert all(c["term_type"] == "billable" for c in app.lookup("essential", ICD10CM))


def test_no_routes_to_outputs_or_other_annotations(tmp_path):
    app = session(tmp_path)
    marker = tmp_path / "cache/eval-runs"
    marker.mkdir()
    (marker / "predictions.json").write_text('"DO NOT EXPOSE"')
    for route in (
        "/api/output",
        "/api/run",
        "/api/file?path=cache/eval-runs/predictions.json",
        "/../cache/eval-runs/predictions.json",
        "/api/annotation?annotator=B",
    ):
        assert call(app, "GET", route)[0] == 404
    with pytest.raises(ValueError, match="not assigned"):
        call(app, "GET", "/api/encounter?id=../../predictions")
    for key in app.assignment.encounter_ids:
        assert "DO NOT EXPOSE" not in json.dumps(app.open_encounter(key))
    assert b"https://" not in PAGE  # no external assets or requests
    assert b"localStorage" not in PAGE


def test_split_lock_reuses_ledger_and_cannot_be_relabelled(tmp_path):
    cache, manifests, assignment = prepare(tmp_path, split="test")
    kwargs = dict(
        cache=cache, manifests=manifests, workdir=tmp_path / "work", guidelines=GUIDELINES
    )
    with pytest.raises(ValueError, match="locked-test-run"):
        AnnotationSession(assignment, **kwargs)
    ledger = cache / "eval/test-ledger.jsonl"
    assert json.loads(ledger.read_text().splitlines()[-1])["action"] == "run_refused"
    app = AnnotationSession(assignment, **kwargs, locked_run=True)
    assert app.documents == {}
    with pytest.raises(ValueError, match="already used"):
        AnnotationSession(assignment, **kwargs, locked_run=True)
    AnnotationSession(assignment, **kwargs, confirmed=True)
    assert json.loads(ledger.read_text().splitlines()[-1])["confirmed_rerun"] is True
    raw = assignment.model_dump(mode="json")
    raw["dataset"]["split"] = "dev"
    # Trusted dev file is different: copying test labels cannot bypass validation.
    dev = assignment.dataset.model_copy(update={"split": "dev", "split_id": "true-dev"})
    (manifests / "primock57-dev.json").write_text(dev.model_dump_json())
    with pytest.raises(ValueError, match="frozen split"):
        AnnotationSession(Assignment.model_validate(raw), **kwargs)


def test_timing_focus_idle_and_resume():
    clock = EncounterClock(Timing(active_seconds=5, wall_seconds=10), 100)
    assert clock.event(110, focused=True, idle_seconds=0) == Timing(
        active_seconds=5, wall_seconds=20
    )
    assert clock.event(120, focused=True, idle_seconds=10).active_seconds == 15
    # The 80s gap credits only until activity at 110 + idle limit 60.
    assert clock.event(200, focused=False, idle_seconds=90) == Timing(
        active_seconds=65, wall_seconds=110
    )
    assert clock.event(250, focused=True, idle_seconds=0).active_seconds == 65
    assert clock.event(255, focused=False, idle_seconds=5).active_seconds == 70
    assert clock.event(500, focused=False, idle_seconds=250) == Timing(
        active_seconds=70, wall_seconds=410
    )
    with pytest.raises(ValueError):
        clock.event(499, focused=True, idle_seconds=0)


def test_server_owned_timing_and_switching(tmp_path, monkeypatch):
    app = session(tmp_path)
    now = [100.0]
    monkeypatch.setattr("auris.annotation.service.time.monotonic", lambda: now[0])
    key, state = opened(app)
    call(app, "POST", "/api/timing", {"encounter_id": key, "focused": True, "idle_seconds": 0})
    now[0] = 130
    call(app, "POST", "/api/save", save_payload(key, state))
    assert app.documents[key].timing == Timing(active_seconds=30, wall_seconds=30)
    bad = {**save_payload(key, state), "timing": {"active_seconds": 9999}}
    with pytest.raises(ValueError):
        call(app, "POST", "/api/save", bad)
    app.open_encounter(app.assignment.encounter_ids[1])
    now[0] = 150
    app.open_encounter(key)
    assert app.documents[key].timing.wall_seconds == 30
    now[0] = 160
    app.close()
    assert app.documents[key].timing == Timing(active_seconds=30, wall_seconds=40)


def test_bad_permissions_symlinks_version_and_hashes_refused(tmp_path):
    app = session(tmp_path)
    key, _ = opened(app)
    kwargs = dict(
        cache=tmp_path / "cache",
        manifests=tmp_path / "manifests",
        workdir=tmp_path / "work",
        guidelines=GUIDELINES,
    )
    with pytest.raises(ValueError, match="guideline"):
        AnnotationSession(app.assignment, **{**kwargs, "guidelines": b"changed"})
    app._path(key).chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        AnnotationSession(app.assignment, **kwargs)
    app._path(key).chmod(0o600)
    app.folder.chmod(0o755)
    with pytest.raises(ValueError, match="0700"):
        AnnotationSession(app.assignment, **kwargs)
    app.folder.chmod(0o700)
    app._path(key).unlink()
    app._path(key).symlink_to(tmp_path / "target")
    with pytest.raises(ValueError, match="symlink"):
        AnnotationSession(app.assignment, **kwargs)


@pytest.mark.parametrize("dataset_id", ["primock57", "aci-bench"])
def test_pilot_deterministic_stratified_dev_only_and_intakes(tmp_path, dataset_id):
    cache, _, assignment = prepare(tmp_path, dataset_id=dataset_id)
    dataset = assignment.dataset
    ids = pilot_ids(dataset, 8, 42)
    assert ids == pilot_ids(dataset, 8, 42)
    assert ids != pilot_ids(dataset, 8, 43)
    strata = {e.id: e.length_stratum for e in dataset.encounters}
    assert sorted(Counter(strata[k] for k in ids).values()) == [2, 3, 3]
    first = create_pilot(dataset, tmp_path / "pilot1", GUIDELINES, n=8, seed=42, cache=cache)
    second = create_pilot(dataset, tmp_path / "pilot2", GUIDELINES, n=8, seed=42, cache=cache)
    assert first == second
    assert set(first[0].encounter_ids) == set(first[1].encounter_ids) == set(ids)
    assert first[0].encounter_ids != first[1].encounter_ids
    assert first[0].annotator_id != first[1].annotator_id
    for entry in dataset.encounters:
        anchor = tmp_path / f"pilot1/fictional-intakes/{entry.id}.json"
        intake = Intake.model_validate_json(anchor.read_bytes())
        assert intake == fictional_intake(dataset.id, entry.id)
        assert (
            date(2026, 10, 1)
            <= date.fromisoformat(intake.encounter.period.start[:10])
            <= date(2027, 9, 30)
        )
        assert "FICTIONAL" in anchor.read_text()
        assert stat.S_IMODE(anchor.stat().st_mode) == 0o600
    assert (tmp_path / "pilot1/pilot-sheet.md").is_file()
    for n in (0, 4, 11, 100):
        with pytest.raises(ValueError):
            pilot_ids(dataset, n, 42)
    with pytest.raises(ValueError, match="dev split"):
        create_pilot(
            dataset.model_copy(update={"split": "test"}),
            tmp_path / "bad",
            GUIDELINES,
            n=8,
            seed=1,
            cache=cache,
        )
    assert not (tmp_path / "bad").exists()


def test_assignment_integrity(tmp_path):
    _, _, assignment = prepare(tmp_path)
    for ids in (("unknown",), (assignment.encounter_ids[0],) * 2):
        with pytest.raises(ValueError):
            Assignment.model_validate({**assignment.model_dump(), "encounter_ids": ids})


def test_guideline_examples_pass_original_step4b_guard():
    guidelines = ROOT / "docs/annotation/guidelines.md"
    examples = [line[2:] for line in guidelines.read_text().splitlines() if line.startswith("> ")]
    assert len(examples) >= 9
    corpora = list(fixture_transcript_texts())
    for path in (ROOT / "examples").glob("*transcript*.json"):
        corpora.extend(s.text for s in Transcript.load(path).segments)
    for path in (ROOT / "eval/harness-check").glob("*/transcript.json"):
        corpora.extend(s.text for s in Transcript.load(path).segments)
    corpora.extend(p.read_text() for p in (ROOT / "src/auris/extract/prompts").glob("*.md"))
    for example in examples:
        report = check_leakage(example, corpora)
        assert report.passed, report.model_dump_json(indent=2)


def request(app, method, path, headers=None, data=None):
    handler = object.__new__(handler_for(app))
    handler.server = type("FakeServer", (), {"server_port": 12345})()
    handler.command, handler.path = method, path
    body = json.dumps(data).encode() if data is not None else b""
    handler.headers = {
        "Host": "127.0.0.1:12345",
        "Content-Length": str(len(body)),
        **(headers or {}),
    }
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    replies = []
    handler.send_response = lambda status: replies.append(status)
    handler.send_header = lambda *_: None
    handler.end_headers = lambda: None
    handler.respond()
    return replies[0], handler.wfile.getvalue()


def test_http_handlers_without_sockets(tmp_path):
    app = session(tmp_path)
    assert request(app, "GET", "/")[1] == PAGE
    assert request(app, "GET", "/api/session")[0] == 403
    auth = {"X-Auris-Token": app.token}
    assert request(app, "GET", "/api/session", auth)[0] == 200
    assert request(app, "GET", "/api/session", {**auth, "Host": "evil.invalid"})[0] == 403
    assert request(app, "GET", "/api/session", {**auth, "Origin": "https://evil.invalid"})[0] == 403
    assert request(app, "POST", "/api/save", auth, {"broken": True})[0] == 422
    assert request(app, "GET", "/api/file?path=/etc/passwd", auth)[0] == 404
    assert request(app, "POST", "/api/save", {**auth, "Content-Length": "3000000"})[0] == 413


def test_server_binds_only_loopback_random_port(tmp_path, monkeypatch, capsys):
    app = session(tmp_path)
    seen = []

    class FakeServer:
        server_port = 54321

        def __init__(self, address, handler):
            seen.append(address)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def serve_forever(self):
            raise KeyboardInterrupt

    monkeypatch.setattr("auris.annotation.server.HTTPServer", FakeServer)
    serve(app)
    assert seen == [BIND_ADDRESS] == [("127.0.0.1", 0)]
    assert f"http://127.0.0.1:54321/#token={app.token}" in capsys.readouterr().out


def test_cli_pilot(tmp_path, capsys):
    cache, manifests, _ = prepare(tmp_path)
    guide = tmp_path / "guidelines.md"
    guide.write_bytes(GUIDELINES)
    assert (
        main(
            [
                "annotate",
                "pilot",
                "--dataset",
                str(manifests / "primock57-dev.json"),
                "--cache",
                str(cache),
                "--guidelines",
                str(guide),
                "--seed",
                "42",
                "--n",
                "8",
                "--out",
                str(tmp_path / "pilot"),
            ]
        )
        == 0
    )
    assert "independent assignments" in capsys.readouterr().out


def test_done_pauses_timing_edit_resumes(tmp_path, monkeypatch):
    app = session(tmp_path)
    now = [10.0]
    monkeypatch.setattr("auris.annotation.service.time.monotonic", lambda: now[0])
    key, state = opened(app)
    call(app, "POST", "/api/timing", {"encounter_id": key, "focused": True, "idle_seconds": 0})
    now[0] = 25
    payload = save_payload(key, state, status="done")
    call(app, "POST", "/api/save", payload)
    assert app.documents[key].timing == Timing(active_seconds=15, wall_seconds=15)
    now[0] = 1000
    call(app, "POST", "/api/timing", {"encounter_id": key, "focused": True, "idle_seconds": 0})
    assert app.documents[key].timing == Timing(active_seconds=15, wall_seconds=15)
    call(app, "POST", "/api/save", {**payload, "status": "in_progress"})
    call(app, "POST", "/api/timing", {"encounter_id": key, "focused": True, "idle_seconds": 0})
    now[0] = 1010
    app.close()
    assert app.documents[key].timing == Timing(active_seconds=25, wall_seconds=25)


@pytest.mark.parametrize(
    "change",
    [
        {"critical": {"condition_assertion": "present"}},
        {"critical": {"order_authorization": "not_ordered"}},
        {"canonical_value": ["NaN"]},
        {"canonical_value": ["sixty"]},
        {"gold_concept_id": "auris:Not A slug"},
    ],
)
def test_inconsistent_scoring_attributes_refused(tmp_path, change):
    app = session(tmp_path)
    key, _ = opened(app)
    with pytest.raises(ValueError):
        AnnotationFact.model_validate({**fact(app, key), **change})


def test_multispan_delete_and_server_reverification(tmp_path):
    app = session(tmp_path)
    key, state = opened(app)
    assertion = fact(app, key)
    assertion["evidence"].append(copy.deepcopy(assertion["evidence"][0]))
    call(app, "POST", "/api/save", save_payload(key, state, [assertion]))
    assert len(app.documents[key].assertions[0].evidence) == 2
    call(app, "POST", "/api/save", save_payload(key, state))
    assert not app.documents[key].assertions


def test_modified_prepared_transcript_refused_before_display(tmp_path):
    cache, manifests, assignment = prepare(tmp_path)
    key = assignment.encounter_ids[0]
    path = cache / f"datasets/prepared/primock57/{key}.json"
    raw = json.loads(path.read_text())
    raw["segments"][0]["text"] = "Fictional tampering."
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="hash mismatch"):
        AnnotationSession(
            assignment,
            cache=cache,
            manifests=manifests,
            workdir=tmp_path / "work",
            guidelines=GUIDELINES,
        )
    assert not (tmp_path / "work").exists()


def test_converted_facts_load_in_harness_gold_manifest(tmp_path):
    from auris.eval.gold import Dataset, load_dataset

    app = session(tmp_path)
    key, state = opened(app)
    call(app, "POST", "/api/save", save_payload(key, state, [fact(app, key)], status="done"))
    assertions, outside = convert_annotation(app._path(key), app.transcripts[key])
    output = tmp_path / "gold-test"
    output.mkdir(mode=0o700)
    transcript = app.transcripts[key]
    transcript.save(output / "transcript.json")
    fictional_intake("primock57", key).save(output / "intake.json")
    # Authored fictional fixture provenance, never a claim that clinicians worked.
    manifest = Dataset.model_validate(
        {
            "id": "fictional-conversion-test",
            "split_id": "fictional-dev",
            "split": "dev",
            "declared_kinds": ["symptom"],
            "clinical_watchlist": ["fictional"],
            "encounters": [
                {
                    "id": key,
                    "transcript_path": "transcript.json",
                    "transcript_sha256": transcript.content_hash,
                    "intake_path": "intake.json",
                    "intake_sha256": digest((output / "intake.json").read_bytes()),
                    "split": "dev",
                    "source": "authored fictional regression fixture",
                    "license": "Apache-2.0",
                    "fictional": True,
                    "annotation": {
                        "annotator_ids": ["A"],
                        "adjudicator_id": "fictional-author",
                        "status": "harness-authored",
                        "independent": False,
                        "guidelines_version": digest(GUIDELINES),
                    },
                    "assertions": [f.model_dump(mode="json") for f in assertions],
                    "out_of_scope": [f.model_dump(mode="json") for f in outside],
                }
            ],
        }
    )
    path = output / "manifest.json"
    path.write_text(manifest.model_dump_json())
    loaded, transcripts = load_dataset(path)
    assert loaded == manifest and transcripts[key] == transcript


def test_all_api_mutations_require_token_before_processing(tmp_path):
    app = session(tmp_path)
    for route in ("/api/span", "/api/save", "/api/timing"):
        for token in ("", "wrong", "é"):
            assert call(app, "POST", route, {"invalid": "data"}, token=token)[0] == 403
    assert app.documents == {}


def test_full_corpus_anchors_do_not_open_held_out_transcripts(tmp_path):
    cache, _, assignment = prepare(tmp_path)
    held_out = cache / "datasets/prepared/primock57/fictional-held-out.json"
    held_out.write_text("Not a transcript: must never be opened by the development pilot.")
    output = tmp_path / "pilot"
    create_pilot(assignment.dataset, output, GUIDELINES, n=8, seed=42, cache=cache)
    assert Intake.model_validate_json(
        (output / "fictional-intakes/fictional-held-out.json").read_bytes()
    )
    assert "fictional-held-out" not in (output / "assignment-A.json").read_text()
    assert json.loads((output / "pilot.json").read_text())["anchor_count"] == 13
