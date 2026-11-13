"""Wholly fictional human work: agreement math, clinician audit and private gold."""

import io
import json
import shutil
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from demo_helpers import FakeValidator
from extract_helpers import completion
from fhir_helpers import mapped_case
from test_kit import GUIDELINES, call, fact, opened, prepare

from auris._storage import write_private_text
from auris.annotation.adjudication import (
    AdjudicationAssignment,
    AdjudicationSession,
    assignment_hash,
    build_tasks,
    resolved,
)
from auris.annotation.agreement import (
    REQUIRED_KAPPAS,
    agreement,
    attributes,
    categorical,
    freeze_check,
    markdown,
)
from auris.annotation.comparison import align, load_pair
from auris.annotation.export import export_gold, scoring_assertion
from auris.annotation.models import AnnotationDocument, AnnotationFact, ImportantFact, Timing
from auris.annotation.pilot import fictional_intake
from auris.annotation.server import PAGE, handler_for
from auris.annotation.service import AnnotationSession
from auris.assertions import Temporality, parse_assertion
from auris.cli import main
from auris.config import load_frozen_config
from auris.eval.gold import Span, digest, load_dataset
from auris.eval.ledger import register_test_use
from auris.eval.matching import eligible
from auris.eval.metrics import score_model
from auris.eval.predictions import freeze_prediction
from auris.extract.backend import FakeBackend
from auris.extract.pipeline import extract, finish_pipeline
from auris.fhir import Mapper
from auris.transcript import SpeakerRole, Transcript


def independent(tmp_path: Path, *, split: str = "dev") -> tuple:
    cache, manifests, assignment = prepare(tmp_path, count=3, split=split)
    app = AnnotationSession(
        assignment,
        cache=cache,
        manifests=manifests,
        workdir=tmp_path / "original",
        guidelines=GUIDELINES,
        locked_run=split == "test",
    )
    a, b = {}, {}
    now = datetime.now(UTC)
    for i, key in enumerate(assignment.encounter_ids):
        call(app, "GET", f"/api/encounter?id={key}")
        f = AnnotationFact.model_validate(fact(app, key))
        a[key] = AnnotationDocument(
            annotator_id="A",
            encounter_id=key,
            transcript_sha256=app.transcripts[key].content_hash,
            guidelines_sha256=assignment.guidelines_sha256,
            started_at=now,
            updated_at=now,
            status="done",
            assertions=(f,),
            timing=Timing(active_seconds=120 * (i + 1), wall_seconds=120 * (i + 1)),
            segment_roles={s.id: s.role for s in app.transcripts[key].segments},
        )
        b[key] = a[key].model_copy(
            update={
                "annotator_id": "B",
                "timing": Timing(active_seconds=180 * (i + 1), wall_seconds=180 * (i + 1)),
            }
        )
    return cache, manifests, assignment, a, b


def adjudication_fixture(tmp_path: Path, *, split: str = "dev") -> tuple:
    cache, manifests, assignment, a, b = independent(tmp_path, split=split)
    key = assignment.encounter_ids[0]
    base = a[key].assertions[0]
    other = base.model_copy(
        update={
            "id": "agreed",
            "gold_concept_id": "auris:agreed",
            "acceptable_texts": ("other fictional concept",),
        }
    )
    only_a = base.model_copy(
        update={
            "id": "a-only",
            "gold_concept_id": "auris:only-a",
            "acceptable_texts": ("a only fictional concept",),
        }
    )
    only_b = base.model_copy(
        update={
            "id": "b-only",
            "gold_concept_id": "auris:only-b",
            "acceptable_texts": ("b only fictional concept",),
        }
    )
    important = ImportantFact(
        id="outside", category="fictional-context", reason="outside schema", evidence=base.evidence
    )
    a[key] = a[key].model_copy(
        update={"assertions": (base, other, only_a), "out_of_scope": (important,)}
    )
    roles = dict(b[key].segment_roles)
    roles[next(iter(roles))] = SpeakerRole.OTHER
    b[key] = b[key].model_copy(
        update={
            "assertions": (
                base.model_copy(update={"temporality": Temporality.PAST}),
                other,
                only_b,
            ),
            "segment_roles": roles,
            "out_of_scope": (important.model_copy(update={"category": "other-context"}),),
        }
    )
    issued = AdjudicationAssignment(
        assignment=assignment, adjudicator_id="C", clinically_trained=True, a=a, b=b
    )
    return cache, manifests, issued


def launch(tmp_path: Path, *, split: str = "dev") -> tuple:
    cache, manifests, assignment = adjudication_fixture(tmp_path, split=split)
    app = AdjudicationSession(
        assignment,
        cache=cache,
        manifests=manifests,
        workdir=tmp_path / "adjudicator",
        guidelines=GUIDELINES,
        confirmed=split == "test",
    )
    return app, cache, manifests


def decide_all(app: AdjudicationSession) -> None:
    for key in app.assignment.encounter_ids:
        _, response = call(app, "GET", f"/api/encounter?id={key}")
        for task in response["tasks"]:
            call(
                app,
                "POST",
                "/api/decision",
                {
                    "encounter_id": key,
                    "task_id": task["id"],
                    "choice": "a",
                    "reason": "Fictional clinician reviewed the exact span.",
                },
            )
        call(app, "POST", "/api/complete", {"encounter_id": key})


def test_hand_computed_detection_perfect_partial_disjoint(tmp_path):
    _, _, assignment, a, b = independent(tmp_path)
    key = assignment.encounter_ids[0]
    for expected, ids in ((1.0, set(a)), (2 / 3, set(list(a)[:2])), (0.0, set())):
        right = {
            k: d.model_copy(update={"assertions": d.assertions if k in ids else ()})
            for k, d in b.items()
        }
        # Equal-sized disjoint candidate sets keep F1 denominator hand-computable.
        for k in set(a) - ids:
            right[k] = b[k].model_copy(
                update={
                    "assertions": (
                        b[k]
                        .assertions[0]
                        .model_copy(
                            update={
                                "gold_concept_id": "auris:disjoint",
                                "acceptable_texts": ("disjoint",),
                            }
                        ),
                    )
                }
            )
        report = agreement(a, right, assignment)
        count = report["detection"]["overall"]
        assert count["f1"] == pytest.approx(expected)
        assert count["a_n"] == count["b_n"] == 3
        assert count["a_only"] == count["b_only"] == 3 - len(ids)
        assert report["detection"]["per_kind"]["symptom"] == count
        assert "no detection kappa" in report["attribute_caveat"]
    empty = {k: d.model_copy(update={"assertions": ()}) for k, d in a.items()}
    empty_b = {k: d.model_copy(update={"assertions": ()}) for k, d in b.items()}
    assert agreement(empty, empty_b)["detection"]["overall"]["f1"] is None
    assert key in report["detection"]["encounters"]


def test_alignment_concept_resource_person_polarity_and_evidence(tmp_path):
    _, _, _, a, _ = independent(tmp_path)
    f = next(iter(a.values())).assertions[0]
    g = f.model_copy(
        update={"gold_concept_id": "auris:alternate", "acceptable_texts": ("FEELS STIFF!",)}
    )
    assert align([f], [g]) == ((0, 0),)
    for changes in (
        {"acceptable_texts": ("disjoint",)},
        {"experiencer": "other"},
        {"polarity": "negated"},
        {"evidence": ()},
        {"kind": "vital_sign"},
    ):
        assert align([f], [g.model_copy(update=changes)]) == ()
    condition = AnnotationFact.model_validate(
        {
            **f.model_dump(),
            "kind": "patient_reported_condition",
            "critical": {"condition_assertion": "present"},
        }
    )
    clinician = AnnotationFact.model_validate(
        {
            **condition.model_dump(),
            "kind": "clinician_assessed_problem",
            "source_speaker_role": "clinician",
        }
    )
    assert align([condition], [clinician]) == ((0, 0),)
    assert align([condition], [clinician], same_kind=True) == ()
    assert align([f, f], [f]) == ((0, 0),)
    assert align([], [f]) == ()


def test_kappa_known_values_prevalence_missing_and_single_category():
    # Marginals: A yes=3/5, B yes=2/5; observed=.8, expected=.48, kappa=8/13.
    result = categorical(
        [("yes", "yes"), ("yes", "yes"), ("yes", "no"), ("no", "no"), ("no", "no")]
    )
    assert result["raw_agreement"] == 0.8
    assert result["kappa"] == pytest.approx(8 / 13)
    assert result["prevalence"]["a"]['"yes"'] == 0.6
    assert categorical([("yes", "yes")] * 4)["kappa"] is None
    assert categorical([])["kappa"] is None
    assert categorical([("yes", "no"), ("no", "yes")])["kappa"] == -1
    assert categorical([(None, "yes"), ("yes", "yes")])["missing_a"] == 1
    assert categorical([(None, None)])["raw_agreement"] == 1
    assert categorical([(None, None)])["kappa"] is None


def test_attribute_denominators_and_exact_pieces(tmp_path):
    _, _, _, a, _ = independent(tmp_path)
    f = next(iter(a.values())).assertions[0]
    med = AnnotationFact.model_validate(
        {
            **f.model_dump(),
            "kind": "reported_medication",
            "medication_intent": "continuing",
            "dose_text": "fictional dose",
            "critical": {"order_authorization": "not_ordered"},
        }
    )
    other = med.model_copy(update={"dose_text": None, "medication_intent": "discussed"})
    result = attributes([(med, other), (f, f)])
    assert result["dose_text"]["n"] == 1
    assert result["dose_text"]["inapplicable_n"] == 1
    assert result["dose_text"]["raw_agreement"] == 0
    assert result["dose_text"]["missing_b"] == 1
    assert result["medication_intent"]["kappa"] == 0
    assert result["gold_concept_id"]["raw_agreement"] == 1
    assert result["code_system"]["raw_agreement"] == 1


@pytest.mark.parametrize(
    "field,message",
    [("guidelines_sha256", "guideline hashes"), ("transcript_sha256", "transcript hashes")],
)
def test_hash_refusals(tmp_path, field, message):
    _, _, assignment, a, b = independent(tmp_path)
    key = assignment.encounter_ids[0]
    b[key] = b[key].model_copy(update={field: "f" * 64})
    with pytest.raises(ValueError, match=message):
        agreement(a, b, assignment)


def test_refuse_sets_id_completion_assignment_and_read_directories(tmp_path):
    _, _, assignment, a, b = independent(tmp_path)
    with pytest.raises(ValueError, match="encounter sets"):
        agreement(a, dict(list(b.items())[:1]))
    with pytest.raises(ValueError, match="independent annotator"):
        agreement(a, a)
    key = next(iter(a))
    bad = {**b, key: b[key].model_copy(update={"status": "in_progress"})}
    with pytest.raises(ValueError, match="completed"):
        agreement(a, bad)
    with pytest.raises(ValueError, match="assignment"):
        agreement(a, b, assignment.model_copy(update={"encounter_ids": (key,)}))
    for label, docs in (("a", a), ("b", b)):
        for key, doc in docs.items():
            write_private_text(tmp_path / label / f"{key}.annotation.json", doc.model_dump_json())
    assert load_pair(tmp_path / "a", tmp_path / "b", assignment) == (a, b)
    assert (
        main(
            [
                "annotate",
                "agreement",
                "--a",
                str(tmp_path / "a"),
                "--b",
                str(tmp_path / "b"),
                "--out",
                str(tmp_path / "report"),
                "--round",
                "3",
            ]
        )
        == 0
    )
    report = json.loads((tmp_path / "report/agreement.json").read_bytes())
    assert report["freeze"]["status"] == "FAIL"  # Single kind/person/polarity are undefined.
    assert (
        "narrow the schema; never lower the bar" in (tmp_path / "report/agreement.md").read_text()
    )
    assert stat.S_IMODE((tmp_path / "report/agreement.json").stat().st_mode) == 0o600
    assert "unknown" in report["timing"]["by_stratum_and_annotator"]


def test_freeze_boundaries_and_rounds():
    labels = {name: {"kappa": 0.70} for name in REQUIRED_KAPPAS}

    def check(*, round_number=1):
        return freeze_check(
            {"f1": 0.80}, {"symptom": {"f1": 0.80}}, labels, round_number=round_number
        )

    assert check()["status"] == "PASS"
    for name in REQUIRED_KAPPAS:
        labels[name]["kappa"] = 0.699999
        assert check()["status"] == "FAIL"
        assert "narrow the schema; never lower the bar" == check(round_number=3)["instruction"]
        labels[name]["kappa"] = None
        assert check()["status"] == "FAIL"
        labels[name]["kappa"] = 0.70
    assert freeze_check({"f1": 0.79999}, {}, labels, round_number=1)["status"] == "FAIL"
    assert (
        freeze_check({"f1": 0.9}, {"symptom": {"f1": 0.5}}, labels, round_number=1)["status"]
        == "FAIL"
    )


def test_varied_perfect_labels_pass_and_timing_reprices(tmp_path):
    _, _, assignment, a, b = independent(tmp_path)
    for i, key in enumerate(a):
        updates = {
            "polarity": "negated" if i else "affirmed",
            "experiencer": "other" if i else "patient",
            "temporality": "past" if i else "current",
            "kind": "vital_sign" if i else "symptom",
        }
        f = AnnotationFact.model_validate({**a[key].assertions[0].model_dump(), **updates})
        a[key] = a[key].model_copy(update={"assertions": (f,)})
        b[key] = b[key].model_copy(update={"assertions": (f,)})
    report = agreement(a, b, assignment, overhead_hours=10)
    assert report["freeze"]["status"] == "PASS"
    timing = report["timing"]
    assert timing["by_stratum_and_annotator"]["overall"]["A"]["mean_active_minutes"] == 4
    assert timing["by_stratum_and_annotator"]["overall"]["B"]["mean_active_minutes"] == 6
    assert set(timing["by_stratum_and_annotator"]) == {"overall", "short", "medium", "long"}
    budget = timing["budget"]
    assert budget["paired_annotation_hours_per_visit"] == pytest.approx(1 / 6)
    assert budget["full_public_40_dev_100_test_hours"] == pytest.approx(10 + 140 / 6)
    assert budget["reduced_public_40_dev_60_test_hours"] == pytest.approx(10 + 100 / 6)
    assert budget["ci_95"]["low"] <= 1 / 6 <= budget["ci_95"]["high"]
    assert "uncertainty" in markdown(report)


def test_task_construction_every_disagreement_and_agreed_carryover(tmp_path):
    _, _, assignment = adjudication_fixture(tmp_path)
    key = assignment.assignment.encounter_ids[0]
    tasks, facts, outside, roles = build_tasks(assignment.a[key], assignment.b[key])
    assert len(facts) == 1 and facts[0].id == "agreed"
    assert outside == ()
    assert len(tasks) == 5
    assert [t.attributes for t in tasks if t.category == "fact"] == [
        ("temporality",),
        ("unmatched",),
        ("unmatched",),
    ]
    assert next(t for t in tasks if t.category == "outside").attributes == ("category",)
    assert len(roles) == len(assignment.a[key].segment_roles) - 1
    assert assignment.tasks[key] == tasks
    raw = assignment.model_dump(mode="json")
    raw["tasks"][key][0]["attributes"] = ["concealed disagreement"]
    with pytest.raises(ValueError, match="task list differs"):
        AdjudicationAssignment.model_validate(raw)
    with pytest.raises(ValueError, match="third independent"):
        AdjudicationAssignment.model_validate({**assignment.model_dump(), "adjudicator_id": "A"})


def test_accept_a_b_correct_reasons_and_audit_resume(tmp_path):
    app, cache, manifests = launch(tmp_path)
    key, payload = opened(app)
    tasks = payload["tasks"]
    first = tasks[0]
    with pytest.raises(ValueError, match="reason"):
        call(
            app,
            "POST",
            "/api/decision",
            {"encounter_id": key, "task_id": first["id"], "choice": "a", "reason": " "},
        )
    before = app._path(key).read_bytes()
    call(
        app,
        "POST",
        "/api/decision",
        {
            "encounter_id": key,
            "task_id": first["id"],
            "choice": "b",
            "reason": "Fictional accept B.",
        },
    )
    corrected = {**first["a"], "temporality": "future"}
    call(
        app,
        "POST",
        "/api/decision",
        {
            "encounter_id": key,
            "task_id": first["id"],
            "choice": "correct",
            "reason": "Fictional correction.",
            "corrected": corrected,
        },
    )
    for task in tasks[1:]:
        call(
            app,
            "POST",
            "/api/decision",
            {
                "encounter_id": key,
                "task_id": task["id"],
                "choice": "a",
                "reason": "Fictional accept A.",
            },
        )
    call(app, "POST", "/api/complete", {"encounter_id": key})
    record = app.records[key]
    assert record.status == "adjudicated"
    assert record.completed_at is not None
    assert len(record.decisions) == 6
    assert [d.sequence for d in record.decisions] == list(range(1, 7))
    assert all(d.who == "C" and d.when.tzinfo is not None for d in record.decisions)
    assert record.decisions[0].before.a.temporality == "current"
    assert record.decisions[0].after.temporality == "past"
    assert record.decisions[1].after.temporality == "future"
    assert app._path(key).read_bytes() != before
    assert stat.S_IMODE(app._path(key).stat().st_mode) == 0o600
    replay = AdjudicationSession(
        app.adjudication,
        cache=cache,
        manifests=manifests,
        workdir=tmp_path / "adjudicator",
        guidelines=GUIDELINES,
    )
    assert replay.records[key] == record
    wrong = record.model_copy(
        update={"decisions": (record.decisions[0].model_copy(update={"who": "A"}),)}
    )
    with pytest.raises(ValueError, match="audit history"):
        resolved(app.adjudication, wrong)
    # Reopening a decision appends; old events cannot be supplied or overwritten by clients.
    call(
        app,
        "POST",
        "/api/decision",
        {
            "encounter_id": key,
            "task_id": first["id"],
            "choice": "a",
            "reason": "Revised fictional decision.",
        },
    )
    assert app.records[key].decisions[:6] == record.decisions
    assert app.records[key].status == "in_progress"


def test_invalid_corrections_completion_and_http_handlers(tmp_path):
    app, _, _ = launch(tmp_path)
    key, payload = opened(app)
    with pytest.raises(ValueError, match="unresolved"):
        call(app, "POST", "/api/complete", {"encounter_id": key})
    task = payload["tasks"][0]
    original = app._path(key).read_bytes()
    bad = {**task["a"], "evidence": [{**task["a"]["evidence"][0], "sha256": "0" * 64}]}
    with pytest.raises(ValueError, match="evidence hash"):
        call(
            app,
            "POST",
            "/api/decision",
            {
                "encounter_id": key,
                "task_id": task["id"],
                "choice": "correct",
                "reason": "Fictional invalid evidence.",
                "corrected": bad,
            },
        )
    assert app._path(key).read_bytes() == original
    assert call(app, "GET", "/api/session")[1]["mode"] == "adjudication"
    for route in ("/api/save", "/api/file", "/api/predictions", "/api/output"):
        assert call(app, "POST", route, {})[0] == 404
    assert call(app, "GET", "/api/session", token="invalid")[0] == 403
    handler = object.__new__(handler_for(app))
    handler.server = type("Server", (), {"server_port": 12345})()
    handler.headers = {"Host": "127.0.0.1:12345", "X-Auris-Token": app.token}
    handler.command, handler.path = "POST", "/api/decision"
    body = json.dumps(
        {
            "encounter_id": key,
            "task_id": task["id"],
            "choice": "a",
            "reason": "Fictional HTTP decision.",
        }
    ).encode()
    handler.headers["Content-Length"] = str(len(body))
    handler.rfile = io.BytesIO(body)
    replies = []
    handler.reply = lambda *args: replies.append(args)
    handler.respond()
    assert replies[0][0] == 200
    assert json.loads(replies[0][1])["audit"][-1]["reason"] == "Fictional HTTP decision."


def test_export_roundtrip_and_fake_backend_scoring(tmp_path, terminology, units):
    app, cache, manifests = launch(tmp_path)
    # Use equal independent facts for scoring; the disagreement fixture otherwise
    # deliberately has multiple invented labels on the same fictional phrase.
    left = {
        k: d.model_copy(update={"assertions": d.assertions[:1]})
        for k, d in app.adjudication.a.items()
    }
    equal = AdjudicationAssignment.model_validate(
        {
            **app.adjudication.model_dump(),
            "tasks": {},
            "a": left,
            "b": {k: d.model_copy(update={"annotator_id": "B"}) for k, d in left.items()},
        }
    )
    app = AdjudicationSession(
        equal,
        cache=cache,
        manifests=manifests,
        workdir=tmp_path / "equal-adjudication",
        guidelines=GUIDELINES,
    )
    decide_all(app)
    intake_dir = tmp_path / "intakes"
    for key in app.assignment.encounter_ids:
        fictional_intake(app.assignment.dataset.id, key).save(intake_dir / f"{key}.json")
    manifest = export_gold(
        equal, app.folder, intake_dir, tmp_path / "gold", cache=cache, manifests=manifests
    )
    dataset, transcripts = load_dataset(manifest)
    assert dataset.split_id == app.assignment.dataset.split_id
    assert all(
        e.annotation.status == "adjudicated" and e.annotation.independent
        for e in dataset.encounters
    )
    assert all(
        e.annotation.guidelines_sha256 == app.assignment.guidelines_sha256
        for e in dataset.encounters
    )
    assert sum(len(e.out_of_scope) for e in dataset.encounters) == 1
    config = load_frozen_config().extractor
    predictions = {}
    for encounter in dataset.encounters:
        transcript = transcripts[encounter.id]
        assertions = [parse_assertion(f.extraction_dict(transcript)) for f in encounter.assertions]
        backend = FakeBackend([completion(assertions)], input_tokenizer=lambda *_: 111)
        run = extract(backend, transcript, config, config.candidate())
        intake = fictional_intake(app.assignment.dataset.id, encounter.id)
        mapper = Mapper(intake, transcript, terminology, units)
        outcomes, _ = finish_pipeline(
            run, mapper, FakeValidator(), tmp_path / f"run-{encounter.id}"
        )
        predictions[encounter.id] = tuple(
            freeze_prediction(a, m) for a, m in zip(run.assertions, outcomes, strict=True)
        )
    report = score_model(dataset, predictions, seed=1729, samples=10)
    assert report["lenient"]["micro"]["f1"] == 1
    assert all(
        stat.S_IMODE(p.stat().st_mode) == 0o600 for p in manifest.parent.rglob("*") if p.is_file()
    )


def test_export_role_corrections_preserve_source_and_evidence(tmp_path):
    app, cache, manifests = launch(tmp_path)
    decide_all(app)
    key = app.assignment.encounter_ids[0]
    task = next(
        t
        for t in build_tasks(app.adjudication.a[key], app.adjudication.b[key])[0]
        if t.category == "role"
    )
    call(app, "GET", f"/api/encounter?id={key}")
    call(
        app,
        "POST",
        "/api/decision",
        {
            "encounter_id": key,
            "task_id": task.id,
            "choice": "b",
            "reason": "Fictional source role correction.",
        },
    )
    call(app, "POST", "/api/complete", {"encounter_id": key})
    for encounter in app.assignment.encounter_ids:
        fictional_intake(app.assignment.dataset.id, encounter).save(
            tmp_path / "intakes" / f"{encounter}.json"
        )
    manifest = export_gold(
        app.adjudication,
        app.folder,
        tmp_path / "intakes",
        tmp_path / "gold",
        cache=cache,
        manifests=manifests,
    )
    dataset, transcripts = load_dataset(manifest)
    encounter = next(e for e in dataset.encounters if e.id == key)
    assert encounter.source_transcript_sha256 == app.adjudication.a[key].transcript_sha256
    assert encounter.transcript_sha256 != encounter.source_transcript_sha256
    assert transcripts[key].segment_by_id(task.segment_id).role == SpeakerRole.OTHER
    assert (
        Transcript.load(manifest.parent / "original-transcripts" / f"{key}.json").content_hash
        == encounter.source_transcript_sha256
    )


def test_coded_gold_projection_scores_real_fake_backend_predictions(
    tmp_path, terminology, units, intake
):
    # Existing authored fictional mapper fixture and already verified terminology
    # rows. This test checks representation compatibility, not clinical gold.
    mapper, assertion, mapped = mapped_case("new-order", terminology, units, intake)
    segment = mapper.transcript.segments[0]
    code = mapped.resource["medicationCodeableConcept"]["coding"][0]
    full_id = f"{code['system']}:{code['code']}"
    annotated = AnnotationFact.model_validate(
        {
            **assertion.model_dump(mode="json"),
            "id": "fictional-coded-fact",
            "gold_concept_id": full_id,
            "codes": [{"system": code["system"], "code": code["code"]}],
            "acceptable_texts": ("independent fictional alternative wording",),
            "critical": {"order_authorization": "explicit"},
            "evidence": (
                Span(
                    segment_id=segment.id,
                    char_start=0,
                    char_end=len(segment.text),
                    sha256=digest(segment.text.encode()),
                ),
            ),
        }
    )
    config = load_frozen_config().extractor
    run = extract(
        FakeBackend([completion([assertion])], input_tokenizer=lambda *_: 111),
        mapper.transcript,
        config,
        config.candidate(),
    )
    outcomes, _ = finish_pipeline(run, mapper, FakeValidator(), tmp_path / "coded-run")
    prediction = freeze_prediction(run.assertions[0], outcomes[0])
    projected = scoring_assertion(annotated)
    assert projected.gold_concept_id == f"rxnorm:{code['code']}"
    assert annotated.gold_concept_id == full_id  # Original human answer remains untouched.
    assert eligible(prediction, projected, strict=True)  # Code-only concept and code equality.
    assert not eligible(prediction, annotated, strict=True)  # Existing representation mismatch.


def test_export_refuses_incomplete_and_changed_anchor(tmp_path):
    app, cache, manifests = launch(tmp_path)
    key, _ = opened(app)
    record = app.records[key]
    for other in app.assignment.encounter_ids:
        if other != key:
            call(app, "GET", f"/api/encounter?id={other}")
    with pytest.raises(ValueError, match="adjudicated encounters only"):
        export_gold(
            app.adjudication,
            app.folder,
            tmp_path / "intakes",
            tmp_path / "bad",
            cache=cache,
            manifests=manifests,
        )
    assert not (tmp_path / "bad").exists()
    assert record.assignment_sha256 == assignment_hash(app.adjudication)
    decide_all(app)
    for encounter in app.assignment.encounter_ids:
        fictional_intake(app.assignment.dataset.id, encounter).save(
            tmp_path / "intakes" / f"{encounter}.json"
        )
    path = tmp_path / "intakes" / f"{key}.json"
    raw = json.loads(path.read_text())
    raw["patient"]["gender"] = "other"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="fictional anchors"):
        export_gold(
            app.adjudication,
            app.folder,
            tmp_path / "intakes",
            tmp_path / "bad",
            cache=cache,
            manifests=manifests,
        )


def test_test_gold_creation_and_scoring_have_separate_ledger_operations(tmp_path):
    app, cache, manifests = launch(tmp_path, split="test")
    decide_all(app)
    for key in app.assignment.encounter_ids:
        fictional_intake(app.assignment.dataset.id, key).save(tmp_path / "intakes" / f"{key}.json")
    kwargs = dict(cache=cache, manifests=manifests)
    for flags in ({}, {"locked_run": True}):
        with pytest.raises(ValueError, match="already used"):
            export_gold(
                app.adjudication,
                app.folder,
                tmp_path / "intakes",
                tmp_path / "refused",
                **kwargs,
                **flags,
            )
    manifest = export_gold(
        app.adjudication,
        app.folder,
        tmp_path / "intakes",
        tmp_path / "test-gold",
        **kwargs,
        confirmed=True,
    )
    dataset, _ = load_dataset(manifest)  # integrity checking is not model scoring permission
    assert dataset.split == "test"
    ledger = cache / "eval/test-ledger.jsonl"
    with pytest.raises(ValueError, match="no reservation"):
        register_test_use(ledger, dataset, "scoring", "score")
    with pytest.raises(ValueError, match="already used"):
        register_test_use(ledger, dataset, "inference", "run", locked_run=True)
    register_test_use(ledger, dataset, "inference", "run", confirmed=True)
    register_test_use(ledger, dataset, "inference", "score")
    with pytest.raises(ValueError, match="already used"):
        register_test_use(ledger, dataset, "inference", "score")
    assert not (tmp_path / "refused").exists()


def test_test_export_first_use_requires_locked_flag_before_writes(tmp_path):
    app, cache, manifests = launch(tmp_path, split="test")
    decide_all(app)
    for key in app.assignment.encounter_ids:
        fictional_intake(app.assignment.dataset.id, key).save(tmp_path / "intakes" / f"{key}.json")
    # A new isolated cache models a coordinator importing annotation work. It
    # has no prior reservation; the original cache and ledger stay untouched.
    fresh = tmp_path / "import-cache"
    shutil.copytree(cache / "datasets", fresh / "datasets")
    kwargs = dict(cache=fresh, manifests=manifests)
    with pytest.raises(ValueError, match="locked-test-run"):
        export_gold(
            app.adjudication, app.folder, tmp_path / "intakes", tmp_path / "refused", **kwargs
        )
    assert not (tmp_path / "refused").exists()
    ledger = fresh / "eval/test-ledger.jsonl"
    assert json.loads(ledger.read_text().splitlines()[-1])["action"] == "run_refused"
    path = export_gold(
        app.adjudication,
        app.folder,
        tmp_path / "intakes",
        tmp_path / "gold",
        **kwargs,
        locked_run=True,
    )
    assert load_dataset(path)[0].split == "test"


def test_coordinator_cli_prepare_and_export(tmp_path):
    cache, manifests, assignment = adjudication_fixture(tmp_path)
    for side, docs in (("a", assignment.a), ("b", assignment.b)):
        for key, doc in docs.items():
            write_private_text(tmp_path / side / f"{key}.annotation.json", doc.model_dump_json())
    source = tmp_path / "source.json"
    source.write_text(assignment.assignment.model_dump_json())
    assert (
        main(
            [
                "annotate",
                "adjudicate",
                "--a",
                str(tmp_path / "a"),
                "--b",
                str(tmp_path / "b"),
                "--assignment",
                str(source),
                "--adjudicator-id",
                "C",
                "--clinically-trained",
                "--out",
                str(tmp_path / "issued"),
                "--prepare-only",
            ]
        )
        == 0
    )
    path = tmp_path / "issued/adjudication-assignment.json"
    issued = AdjudicationAssignment.model_validate_json(path.read_bytes())
    assert issued == assignment
    app = AdjudicationSession(
        issued, cache=cache, manifests=manifests, workdir=tmp_path / "work", guidelines=GUIDELINES
    )
    decide_all(app)
    for key in issued.assignment.encounter_ids:
        fictional_intake(issued.assignment.dataset.id, key).save(
            tmp_path / "intakes" / f"{key}.json"
        )
    assert (
        main(
            [
                "annotate",
                "export-gold",
                "--assignment",
                str(path),
                "--adjudicated",
                str(app.folder),
                "--intakes",
                str(tmp_path / "intakes"),
                "--out",
                str(tmp_path / "exported"),
                "--cache",
                str(cache),
                "--manifests",
                str(manifests),
            ]
        )
        == 0
    )
    load_dataset(tmp_path / "exported/manifest.json")


def test_editor_focus_scroll_and_unicode_return_state():
    node = shutil.which("node")
    if node is None:
        pytest.skip("existing Node runtime unavailable for inline JavaScript state probe")
    script = PAGE.decode().split("<script>")[1].split("</script>")[0]
    # Run actual functions in a minimal DOM, without browsers, HTTP or dependencies.
    start = script.index("function editorTarget(")
    end = script.index("async function applyFact(", start)
    probe = (
        r"""
const assert=require('node:assert/strict');
let editorReturn=null,decisionTask=null,editorTimer,edit={},editorDirty=true;
const calls=[];
const el={dataset:{segment:'s1'},value:'🩺 café stiff',
scrollIntoView:x=>calls.push(['turn-scroll',x.block]),
focus:x=>calls.push(['turn-focus',x.preventScroll]),
setSelectionRange:(a,b)=>calls.push(['selection',a,b])};
const elements=new Proxy({}, {get:(o,key)=>o[key]??=({scrollTop:10,
classList:{add:x=>calls.push([key,'add',x]),remove:x=>calls.push([key,'remove',x])},
scrollIntoView:x=>calls.push([key,'scroll',x.block]),
focus:x=>calls.push([key,'focus',x.preventScroll]),textContent:'error'})});
const $=id=>elements[id];
const document={body:{classList:{add:x=>calls.push(['body','add',x]),
remove:x=>calls.push(['body','remove',x])}},querySelectorAll:()=>[el]};
const captureSelection=()=>calls.push(['captured']);
"""
        + script[start:end]
        + r"""
const span={segment_id:'s1',char_start:2,char_end:6};
showEditor('fact',[span]);
assert.equal(editorReturn.segmentId,'s1');
assert.deepEqual(calls.slice(0,3),[['body','add','editing'],['editor','scroll','start'],['kind','focus',true]]);
cancel();
assert.equal(edit,null);assert.equal(editorReturn,null);
assert.ok(calls.some(x=>x[0]==='turn-scroll'&&x[1]==='center'));
assert.ok(calls.some(x=>x[0]==='turn-focus'&&x[1]===true));
assert.ok(calls.some(x=>x[0]==='selection'&&x[1]===3&&x[2]===7));
assert.equal(editorTarget('outside',[span]).focusId,'category');
returnToTranscript(null);
"""
    )
    result = subprocess.run([node, "-e", probe], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    syntax = subprocess.run(
        [node, "--check"], input=script, capture_output=True, text=True, check=False
    )
    assert syntax.returncode == 0, syntax.stderr
    assert "position:fixed;bottom:0" in PAGE.decode()


def test_comparison_preserves_all_fields_and_exact_quotes(tmp_path):
    from auris.assertions import ASSERTION_MODELS

    app, _, _ = launch(tmp_path)
    _, response = opened(app)
    # Include the unpaired side, role conflicts and out-of-scope facts too.
    assert {task["category"] for task in response["tasks"]} == {"fact", "role", "outside"}
    node = shutil.which("node")
    if node is None:
        pytest.skip("existing Node runtime unavailable for inline JavaScript DOM probe")
    fixture = {
        "turns": response["turns"],
        "tasks": response["tasks"],
        "fields": {
            model.model_fields["kind"].annotation.__args__[0]: list(model.model_fields)
            for model in ASSERTION_MODELS
        },
    }
    script = PAGE.decode().split("<script>")[1].split("</script>")[0]
    functions = script[
        script.index("const comparisonLabels=") : script.index("function renderTasks()")
    ]
    probe = (Path(__file__).parent / "comparison_probe.js").read_text()
    result = subprocess.run(
        [node],
        input="const fixture = "
        + json.dumps(fixture)
        + ";\n"
        + probe.replace("/* COMPARISON FUNCTIONS */", functions),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    # The fixed-layout table wraps long IDs/hashes at tablet width; no nested
    # horizontal scrolling or focusable JSON pane is needed to read either side.
    assert "width:100%;table-layout:fixed" in PAGE.decode()
    assert "overflow-wrap:anywhere;white-space:pre-wrap" in PAGE.decode()


@pytest.mark.parametrize("folder", ["missing-folder", "empty-folder", "adjudicator"])
def test_export_cli_names_empty_folder_and_record_location(tmp_path, capsys, folder):
    app, cache, manifests = launch(tmp_path)
    supplied = tmp_path / folder
    if folder == "empty-folder":
        supplied.mkdir()
    assignment_path = tmp_path / "adjudication-assignment.json"
    write_private_text(assignment_path, app.adjudication.model_dump_json())
    out = tmp_path / "gold"
    assert (
        main(
            [
                "annotate",
                "export-gold",
                "--assignment",
                str(assignment_path),
                "--adjudicated",
                str(supplied),
                "--intakes",
                str(tmp_path / "intakes"),
                "--out",
                str(out),
                "--cache",
                str(cache),
                "--manifests",
                str(manifests),
            ]
        )
        == 1
    )
    error = capsys.readouterr().err
    assert "No *.adjudication.json files found" in error
    assert str(supplied) in error
    assert "<workdir>/adjudications" in error
    assert "out/annotation/adjudication/work/adjudications" in error
    assert "--adjudicated" in error
    assert not out.exists()


@pytest.mark.parametrize("mismatch", ["missing", "unexpected", "both"])
def test_export_lists_missing_and_unexpected_encounter_ids(tmp_path, mismatch):
    app, cache, manifests = launch(tmp_path)
    decide_all(app)
    missing = app.assignment.encounter_ids[0]
    if mismatch in ("missing", "both"):
        (app.folder / f"{missing}.adjudication.json").unlink()
    if mismatch in ("unexpected", "both"):
        record = app.records[missing].model_copy(update={"encounter_id": "fictional-unexpected"})
        write_private_text(
            app.folder / "fictional-unexpected.adjudication.json", record.model_dump_json()
        )
    out = tmp_path / "gold"
    with pytest.raises(ValueError) as caught:
        export_gold(
            app.adjudication,
            app.folder,
            tmp_path / "intakes",
            out,
            cache=cache,
            manifests=manifests,
        )
    message = str(caught.value)
    assert f"missing encounter ids: {missing if mismatch != 'unexpected' else 'none'};" in message
    assert (
        f"unexpected encounter ids: {'fictional-unexpected' if mismatch != 'missing' else 'none'}"
        in message
    )
    assert "café" not in message  # Only public dataset IDs, never transcript text.
    assert not out.exists()
