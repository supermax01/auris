"""Step 9 regressions. All utterances and completion bodies here are fictional."""

import io
import json
from dataclasses import replace
from unittest.mock import Mock
from urllib.error import URLError

import pytest
from extract_helpers import case_input, completion, sse_response

from auris.config import load_frozen_config
from auris.extract.backend import ExtractionError, ExtractionTimeoutError, FakeBackend, HttpBackend
from auris.extract.citations import classify_citation, resolve_citations
from auris.extract.identity import profile_identity
from auris.extract.measurements import repetition_metrics
from auris.extract.pipeline import extract, finish_pipeline
from auris.extract.profiles import profile_contract, sampling_parameters
from auris.extract.prompt import messages, segment_location
from auris.extract.timeout import TimeoutBudget
from auris.fhir import Mapper
from auris.fhir.models import LaneState
from auris.transcript import Transcript


def window_fixture():
    source, assertion = case_input("vital_sign")
    transcript = Transcript(
        segments=tuple(
            source.segments[0].model_copy(
                update={
                    "id": f"doctor-{i:04d}",
                    "start_seconds": float(i * 10),
                    "end_seconds": float(i * 10 + 5),
                }
            )
            for i in range(9)
        )
    )
    return transcript, assertion


@pytest.mark.parametrize(
    "citation,category,resolved",
    [
        ("doctor-0005", "in_window", True),  # Declared overlap is shown and valid.
        ("doctor-0003", "adjacent_or_overlap", False),
        ("doctor-0000", "earlier_window", False),
        ("s999", "malformed_or_nonexistent", False),
        ("00:50.0–00:55.0", "id_format_confusion", True),
        ("00:00.0–00:05.0", "id_format_confusion", False),
    ],
)
def test_window_citation_patterns_fake_backend(
    citation, category, resolved, terminology, units, intake, tmp_path, monkeypatch
):
    from auris.extract import pipeline
    from auris.extract.chunking import Chunk

    transcript, assertion = window_fixture()
    shown = transcript.segments[4:7]
    assert classify_citation(citation, shown, transcript, frozenset({"doctor-0000"})) == category
    evidence = assertion.evidence[0].model_copy(update={"segment_id": citation})
    assertion = assertion.model_copy(update={"evidence": (evidence,)})
    monkeypatch.setattr(pipeline, "plan_chunks", lambda *a, **k: (Chunk(shown, 100, "fictional"),))
    backend = FakeBackend([completion([assertion])])
    config = load_frozen_config().extractor
    run = extract(backend, transcript, config, config.candidate())
    assert run.failure is None
    assert bool(run.citation_failures) is not resolved
    # No unshown transcript text or ids are inserted to accommodate a citation.
    prompt = backend.calls[0]["messages"][1]["content"]
    assert "doctor-0004, doctor-0005, doctor-0006" in prompt
    assert "doctor-0000" not in prompt
    # Map directly; the validator cannot waive a foreign citation.
    mapper = Mapper(intake, transcript, terminology, units)
    if resolved:
        assert run.assertions[0].evidence[0].segment_id == "doctor-0005"
        assert mapper.map_assertion(run.assertions[0], 0).lanes.evidence == LaneState.PASS
    else:
        invalid = run.citation_failures[0]
        outcome = mapper.map_assertion(
            run.assertions[0],
            0,
            allowed_segment_ids=frozenset(s.id for s in transcript.segments) - invalid,
        )
        assert outcome.lanes.evidence == LaneState.FAIL
        assert outcome.export_state == "validation_failed"


def test_location_alias_is_unique_and_quote_checks_still_apply(terminology, units, intake):
    transcript, assertion = window_fixture()
    label = segment_location(transcript.segments[5])
    assertion = assertion.model_copy(
        update={
            "evidence": (
                assertion.evidence[0].model_copy(
                    update={"segment_id": label, "quote": "Fictional absent quote."}
                ),
            )
        }
    )
    resolved, changes = resolve_citations((assertion,), transcript.segments[4:7])
    assert changes and resolved[0].evidence[0].segment_id == "doctor-0005"
    outcome = Mapper(intake, transcript, terminology, units).map_assertion(resolved[0], 0)
    assert outcome.lanes.evidence == LaneState.FAIL
    duplicate = transcript.segments[5].model_copy(update={"id": "another-id"})
    unresolved, changes = resolve_citations((assertion,), (*transcript.segments[4:7], duplicate))
    assert not changes and unresolved == (assertion,)


def test_turn_alias_and_window_prompt_are_explicit():
    transcript, assertion = case_input("vital_sign")
    segment = transcript.segments[0].model_copy(
        update={
            "id": "turn-id",
            "start_seconds": None,
            "end_seconds": None,
            "turn_number": 7,
        }
    )
    assertion = assertion.model_copy(
        update={"evidence": (assertion.evidence[0].model_copy(update={"segment_id": "turn 7"}),)}
    )
    resolved, changes = resolve_citations((assertion,), (segment,))
    assert changes and resolved[0].evidence[0].segment_id == "turn-id"
    assert "FIRST field" in messages((segment,))[1]["content"]


def test_fictional_intake_is_explicit_strict_metadata(intake):
    from pydantic import ValidationError

    from auris.annotation.pilot import fictional_intake
    from auris.intake import Intake

    assert fictional_intake("fictional-dataset", "fictional-visit").fictional is True
    raw = intake.model_dump(mode="json", by_alias=True)
    raw.pop("fictional")
    # Identical example URLs in an unmarked intake do not opt into relaxation.
    assert Intake.model_validate(raw).fictional is False
    raw["fictional"] = True
    marked = Intake.model_validate(raw)
    assert Intake.from_json(marked.to_json()).fictional is True
    raw["fictional"] = "true"
    with pytest.raises(ValidationError):
        Intake.model_validate(raw)


def test_timeout_arithmetic_scales_with_generation_prompt_and_speed():
    budget = TimeoutBudget(4096, 5000, generation_tokens_per_second=25)
    assert budget.seconds == 351  # ceil(1.5 * (4096/25 + 5000/100) + 30)
    assert replace(budget, max_tokens=8192).seconds > budget.seconds
    assert replace(budget, input_tokens=10000).seconds > budget.seconds
    assert replace(budget, generation_tokens_per_second=10).seconds > budget.seconds


@pytest.mark.parametrize("error", [TimeoutError("private"), URLError(TimeoutError("private"))])
def test_timeout_has_distinct_safe_error(error):
    backend = HttpBackend("http://127.0.0.1:8000")
    backend.opener.open = Mock(side_effect=error)
    with pytest.raises(ExtractionTimeoutError, match="first_chunk stream timed out") as caught:
        backend.complete([], {}, temperature=0, max_tokens=4096)
    assert "private" not in str(caught.value)
    backend.opener.open.side_effect = URLError(ConnectionRefusedError("private"))
    with pytest.raises(ExtractionError, match="connection failed") as caught:
        backend.complete([], {}, temperature=0, max_tokens=4096)
    assert not isinstance(caught.value, ExtractionTimeoutError)


def test_timeout_is_passed_to_http_and_recorded():
    backend = HttpBackend("http://127.0.0.1:8000")
    backend.opener.open = Mock(return_value=io.BytesIO(sse_response(completion())))
    backend.complete([], {}, temperature=0, max_tokens=4096, timeout=351)
    assert backend.opener.open.call_args.kwargs["timeout"] == 351
    transcript, _ = case_input("vital_sign")
    config = load_frozen_config().extractor
    fake = FakeBackend([completion()])
    run = extract(fake, transcript, config, config.candidate())
    assert fake.calls[0]["timeout"] == run.calls[0]["timeout"]["seconds"]
    assert run.calls[0]["timeout"]["source"] == "configured"
    fake.complete = Mock(side_effect=ExtractionTimeoutError("local extractor request timed out"))
    run = extract(fake, transcript, config, config.candidate())
    assert run.calls[0]["failure_type"] == "timeout"
    assert "timed out" in run.failure and not run.assertions


@pytest.mark.parametrize(
    "finish,content",
    [
        ("length", "FICTIONAL_DISCARDED_BODY{"),
        ("length", '{"assertions":[]}'),  # Even complete JSON is excluded.
        ("stop", "FICTIONAL_INVALID_BODY"),
        ("tool_calls", "FICTIONAL_UNEXPECTED_FINISH"),
    ],
)
def test_discarded_storage_is_private_and_never_parsed(
    finish, content, terminology, units, intake, tmp_path, monkeypatch
):
    from auris.extract import pipeline

    transcript, _ = case_input("vital_sign")
    parser = Mock(side_effect=AssertionError("discarded body reached schema parser"))
    monkeypatch.setattr(pipeline, "parse_extraction_json", parser)
    config = load_frozen_config().extractor
    run = extract(
        FakeBackend([completion(content=content, finish_reason=finish)]),
        transcript,
        config,
        config.candidate(),
    )
    assert run.failure and not run.assertions
    parser.assert_not_called()
    assert "raw_response" not in run.calls[0]
    assert run.calls[0]["output_discarded"]
    path = tmp_path / "run"
    outcomes, validation = finish_pipeline(
        run,
        Mapper(intake, transcript, terminology, units),
        None,
        path,
    )
    assert outcomes == () and validation is None
    discarded = path / run.calls[0]["discarded_response_file"]
    saved = json.loads(discarded.read_text())
    assert saved["status"] == "discarded" and saved["used_for_assertions"] is False
    assert saved["response"]["choices"][0]["message"]["content"] == content
    assert discarded.stat().st_mode & 0o777 == 0o600
    assert path.stat().st_mode & 0o777 == 0o700
    assert content not in (path / "extraction.json").read_text()
    assert run.calls[0]["repetition"]["version"] == "repetition-v2"


def test_repetition_metrics_cover_pretty_and_compact_output():
    assert repetition_metrics("")["repeated_line_ratio"] == 0
    assert repetition_metrics("alpha beta gamma")["longest_repeated_ngram_run_tokens"] == 0
    assert repetition_metrics("alpha beta " * 5)["longest_repeated_ngram_run_tokens"] == 10
    assert repetition_metrics("alpha\nalpha\nalpha\n")["repeated_line_ratio"] == 2 / 3
    assert repetition_metrics('{"a":1},' * 10)["longest_repeated_ngram_run_tokens"] >= 70


def test_frozen_replay_counts_states_without_inference(terminology, units, intake):
    from validator_helpers import passing_run

    from auris.extract.consultation_replay import replay_consultation
    from auris.validation.report import validation_document

    transcript, symptom = case_input("symptom")
    normalised = symptom.model_copy(update={"concept_text": "fictional normalised symptom"})
    alias = segment_location(transcript.segments[0])
    timestamp = symptom.model_copy(
        update={"evidence": (symptom.evidence[0].model_copy(update={"segment_id": alias}),)}
    )
    mapper = Mapper(intake, transcript, terminology, units)
    outcomes = tuple(mapper.map_assertion(a, i) for i, a in enumerate((normalised, timestamp)))
    validation = validation_document(intake, outcomes, passing_run(intake, outcomes))
    # Freeze the former hard-concept rule without changing any generated fact.
    validation["resources"][3]["export_state"] = "validation_failed"
    validation["resources"][3]["lanes"]["evidence"] = {
        "state": "FAIL",
        "issues": [{"details": {"text": "concept_text_not_in_quote"}}],
    }
    raw = [a.model_dump(mode="json") for a in (normalised, timestamp)]
    document = {
        "metadata": {"transcript_sha256": transcript.content_hash},
        "assertions": raw,
        "calls": [{"segment_ids": [transcript.segments[0].id], "parsed_assertions": raw}],
        "citation_failures": [{"assertion_position": 1, "segment_ids": [alias]}],
    }
    report = replay_consultation(document, validation, mapper)
    assert report["concept_cases"] == report["concept_evidence_to_pass"] == 1
    assert report["concept_state_changes"] == {"validation_failed->needs_review": 1}
    assert report["citation_categories"]["id_format_confusion"] == 1
    assert report["citation_state_changes"] == {"validation_failed->ledger_only": 1}
    assert report["citation_combined_evidence_to_pass"] == 1
    assert symptom.evidence[0].quote not in json.dumps(report)
    document["metadata"]["transcript_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        replay_consultation(document, validation, mapper)


def test_dry_profile_identity_and_wire_fields():
    assert profile_contract("full-v0-dry") == profile_contract("full-v0")
    base, dry = (profile_identity(p) for p in ("full-v0", "full-v0-dry"))
    assert base["prompt_sha256"] == dry["prompt_sha256"]
    assert base["assertion_property_order"] == dry["assertion_property_order"]
    assert "sampling_parameters" not in base
    assert dry["sampling_parameters"] == sampling_parameters("full-v0-dry")
    transcript, _ = case_input("vital_sign")
    config = load_frozen_config().extractor.model_copy(update={"extraction_profile": "full-v0-dry"})
    fake = FakeBackend([completion()])
    run = extract(fake, transcript, config, config.candidate())
    assert not run.failure
    assert fake.calls[0]["temperature"] == 0
    assert fake.calls[0]["sampling"] == dry["sampling_parameters"]
    http = HttpBackend("http://127.0.0.1:8000")
    http.request = Mock(return_value=completion())
    http.complete([], {}, temperature=0, max_tokens=4096, sampling=fake.calls[0]["sampling"])
    payload = http.request.call_args.args[1]
    assert payload["dry_multiplier"] == 1.0 and payload["dry_base"] == 1.75
    assert payload["dry_allowed_length"] == 2 and payload["dry_penalty_last_n"] == 64
    assert payload["dry_sequence_breakers"] == ["\n", ":", '"', "*"]
    assert payload["seed"] == 0
    assert payload["samplers"].index("dry") < payload["samplers"].index("temperature")
