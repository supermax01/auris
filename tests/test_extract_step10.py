"""Step 10 offline contracts. All text and model responses here are fictional."""

import copy
import io
import json
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from extract_helpers import case_input, completion, sse_response
from validator_helpers import passing_run

from auris.config import ExtractorConfig, load_frozen_config
from auris.extract.backend import FakeBackend, HttpBackend
from auris.extract.errors import ExtractionError, ExtractionTimeoutError, LoopDetectedError
from auris.extract.identity import profile_identity
from auris.extract.measurements import repetition_metrics
from auris.extract.objects import AssertionScanner
from auris.extract.pipeline import extract, finish_pipeline, parse_completion
from auris.extract.profiles import decoding_parameters, sampling_parameters
from auris.extract.stream import StreamAssembler, read_stream
from auris.extract.timeout import TimeoutBudget
from auris.fhir import Mapper
from auris.fhir.bundle import build_collection
from auris.review import review_quarantined_item, unresolved_quarantines
from auris.run import export_decision, finish_run


@pytest.mark.parametrize(
    "model,temperature,top_p,top_k,presence",
    [
        ("qwen3-8b-q4_k_m", 0.7, 0.8, 20, 1.5),
        ("bonsai-2-9b-q2_0", 0.7, 0.8, 20, 1.5),
        ("bonsai-2-27b-q2_0", 0.7, 0.8, 20, 1.5),
        ("gemma-4-e4b-it-q4_0", 1.0, 0.95, 64, 0),
    ],
)
def test_card_request_and_identity(model, temperature, top_p, top_k, presence):
    config = load_frozen_config().extractor.model_copy(
        update={"extraction_profile": "full-v0-card"}
    )
    candidate = config.candidate(model)
    transcript, _ = case_input("vital_sign")
    backend = FakeBackend([completion()])
    run = extract(backend, transcript, config, candidate)
    assert run.failure is None
    request = backend.calls[0]
    assert request["temperature"] == temperature
    http = HttpBackend("http://127.0.0.1:8000")
    http.request = Mock(return_value=completion())
    http.complete(
        [], {}, temperature=request["temperature"], max_tokens=1024, sampling=request["sampling"]
    )
    payload = http.request.call_args.args[1]
    assert payload["top_p"] == top_p and payload["top_k"] == top_k
    assert payload["presence_penalty"] == presence
    assert {
        k: payload[k]
        for k in (
            "min_p",
            "frequency_penalty",
            "repeat_penalty",
            "repeat_last_n",
            "dry_multiplier",
            "xtc_probability",
            "xtc_threshold",
            "typical_p",
            "top_n_sigma",
            "dynatemp_range",
            "mirostat",
            "adaptive_target",
            "seed",
        )
    } == dict(
        min_p=0,
        frequency_penalty=0,
        repeat_penalty=1,
        repeat_last_n=64,
        dry_multiplier=0,
        xtc_probability=0,
        xtc_threshold=1,
        typical_p=1,
        top_n_sigma=-1,
        dynatemp_range=0,
        mirostat=0,
        adaptive_target=-1,
        seed=0,
    )
    assert payload["samplers"] == ["penalties", "top_k", "top_p", "temperature"]
    assert payload["stream"] and payload["stream_options"] == {"include_usage": True}
    metadata = run.document()["metadata"]
    assert metadata["card_sampling"] == candidate.card_sampling.model_dump(mode="json")
    assert metadata["loop_guard"] == config.loop_guard.model_dump()
    assert metadata["sampling_parameters"] == sampling_parameters("full-v0-card", candidate)
    assert len(metadata["card_sampling"]["readme_sha256"]) == 64
    for key in (
        "schema_version",
        "schema_property_order",
        "prompt_sha256",
        "window_instruction_sha256",
    ):
        assert metadata[key] == profile_identity("full-v0")[key]


def test_seed_override_and_temperature_refusal():
    config = load_frozen_config().extractor.model_copy(
        update={"extraction_profile": "full-v0-card", "seed": 2}
    )
    transcript, _ = case_input("vital_sign")
    backend = FakeBackend([completion()])
    run = extract(backend, transcript, config, config.candidate())
    assert backend.calls[0]["sampling"]["seed"] == 2
    assert run.document()["metadata"]["sampling_parameters"]["seed"] == 2
    assert run.decoding["seed"] == 2
    with pytest.raises(ExtractionError, match="conflicting temperature"):
        extract(
            backend,
            transcript,
            config.model_copy(update={"temperature_override": 0}),
            config.candidate(),
        )
    assert (
        decoding_parameters(
            config.model_copy(update={"temperature_override": 0.7}), config.candidate()
        )[0]
        == 0.7
    )
    for seed in (-1, 4294967296):
        with pytest.raises(ValueError):
            ExtractorConfig.model_validate({**config.model_dump(), "seed": seed})
    assert sampling_parameters("full-v0") == {}
    assert sampling_parameters("full-v0", seed=2) == {"seed": 2}
    assert sampling_parameters("full-v0-dry")["dry_penalty_last_n"] == 64
    from auris.cli import _build_parser

    args = _build_parser().parse_args(
        ["run", "--transcript", "t", "--intake", "i", "--out", "o", "--seed", "2"]
    )
    assert args.seed == 2
    args = _build_parser().parse_args(
        ["eval", "run", "--dataset", "d", "--out", "o", "--extractor-seed", "1"]
    )
    assert args.extractor_seed == 1 and args.seed == 1729


@pytest.mark.parametrize(
    "malformation", ["blank_quote", "wrong_type", "missing_field", "primitive"]
)
def test_invalid_sibling_quarantined(malformation):
    transcript, valid = case_input("vital_sign")
    raw = valid.model_dump(mode="json")
    bad = copy.deepcopy(raw)
    if malformation == "blank_quote":
        bad["evidence"][0]["quote"] = "  "
    elif malformation == "wrong_type":
        bad["concept_text"] = 42
    elif malformation == "missing_field":
        del bad["evidence"]
    else:
        bad = 42
    config = load_frozen_config().extractor
    run = extract(
        FakeBackend([completion(content=json.dumps({"assertions": [raw, bad, raw]}))]),
        transcript,
        config,
        config.candidate(),
    )
    assert run.failure is None
    assert run.assertions == (valid,)
    (item,) = run.quarantined_items
    assert (item.call_index, item.window_id, item.position) == (0, "0", 1)
    assert item.raw_item == bad and item.errors[0].type and item.errors[0].location
    assert item.transcript_sha256 == transcript.content_hash
    assert item.errors[0].location[:2] == ("assertions", 1)
    assert run.measurements["quarantined_item_count"] == 1
    assert len(run.calls[0]["parsed_assertions"]) == 2
    assert item.review_item()["is_material"] and item.review_item()["failing"]


@pytest.mark.parametrize(
    "text",
    [
        "not JSON",
        "[]",
        '{"assertions":{}}',
        '{"assertions":[],"extra":0}',
        '{"assertions":[],"assertions":[]}',
        '{"assertions":[{"kind":"symptom","kind":"symptom"}]}',
        '{"assertions":[NaN]}',
        '{"assertions":[Infinity]}',
        '{"assertions":[1e999]}',
        json.dumps({"assertions": [42] * 64}),
        json.dumps({"assertions": [42] * 65}),
    ],
)
def test_document_errors_still_discard_everything(text):
    quarantines = []
    with pytest.raises(ExtractionError):
        parse_completion(completion(content=text), quarantined=quarantines)
    assert not quarantines


def test_quarantine_export_review_and_note(terminology, units, intake, tmp_path):
    transcript, valid = case_input("vital_sign")
    raw = valid.model_dump(mode="json")
    bad = copy.deepcopy(raw)
    bad["evidence"][0]["quote"] = "  "
    config = load_frozen_config().extractor
    run = extract(
        FakeBackend([completion(content=json.dumps({"assertions": [raw, bad]}))]),
        transcript,
        config,
        config.candidate(),
    )
    mapper = Mapper(intake, transcript, terminology, units)
    validator = Mock()
    validator.validate.side_effect = lambda resources: passing_run(
        intake, [mapper.map_assertion(valid, 0)]
    )
    path = tmp_path / "blocked"
    decision = finish_run(run, mapper, validator, path)
    assert decision.blocking_ids == (run.quarantined_items[0].id,)
    assert not decision.exported_ids
    assert (
        "1 extracted item(s) were malformed and withheld — review required"
        in (path / "note.md").read_text()
    )
    document = json.loads((path / "extraction.json").read_text())
    assert document["quarantined_items"][0]["raw_item"] == bad
    assert len(document["assertions"]) == len(document["mappings"]) == 1
    outcomes = (mapper.map_assertion(valid, 0),)
    validation = passing_run(intake, outcomes)
    from auris.validation.report import apply_validation

    outcomes = tuple(apply_validation(o, validation) for o in outcomes)
    (item,) = run.quarantined_items
    with pytest.raises(ValueError, match="dismissal"):
        build_collection(intake, outcomes, validation=validation, quarantined_items=[item])
    assert build_collection(intake, outcomes, preview=True, quarantined_items=[item])["entry"]
    for action, reason in [("approve", "fictional review"), ("dismiss", "  ")]:
        with pytest.raises(ValueError):
            review_quarantined_item(
                item,
                action=action,
                reason=reason,
                reviewer="Fictional reviewer",
                role="clinician",
                log_path=tmp_path / "review" / "review.jsonl",
            )
    receipt = review_quarantined_item(
        item,
        action="dismiss",
        reason="Fictional malformed duplicate",
        reviewer="Fictional reviewer",
        role="clinician",
        log_path=tmp_path / "review" / "review.jsonl",
    )
    assert unresolved_quarantines([item], [receipt]) == ()
    other_visit = item.model_copy(update={"transcript_sha256": "0" * 64})
    assert unresolved_quarantines([other_visit], [receipt]) == (item.id,)
    assert unresolved_quarantines([item.model_copy(update={"raw_item": 42})], [receipt]) == (
        item.id,
    )
    assert (tmp_path / "review" / "review.jsonl").stat().st_mode & 0o777 == 0o600
    decision = export_decision(
        intake, outcomes, validation, quarantined_items=(item,), quarantine_reviews=(receipt,)
    )
    assert not decision.blocking_ids and len(decision.exported_ids) == 1
    assert build_collection(
        intake,
        outcomes,
        validation=validation,
        quarantined_items=[item],
        quarantine_reviews=[receipt],
    )
    run.quarantine_reviews.append(receipt)
    finish_run(run, mapper, validator, tmp_path / "dismissed")
    assert "reviewed and dismissed with reasons" in (tmp_path / "dismissed" / "note.md").read_text()
    assert run.quarantined_items == [item]  # Still withheld, never made into a fact.


def test_synthetic_sse_fixture_reassembles_exactly():
    fixture = Path(__file__).parent / "fixtures" / "extract" / "fictional.sse"
    expected = json.loads(fixture.with_suffix(".json").read_text())
    result = read_stream(
        io.BytesIO(fixture.read_bytes()),
        started=time.monotonic(),
        timeout=100,
        first_chunk_timeout=30,
        identical_assertion_limit=4,
    )
    assert result == expected


@pytest.mark.parametrize("piece_size", [1, 2, 7, 4096])
def test_loop_fourth_object_only_with_escaped_quotes_and_braces(piece_size):
    _, assertion = case_input("symptom")
    item = assertion.model_dump(mode="json")
    item["concept_text"] = "FICTIONAL symptom"
    item["evidence"][0]["quote"] = 'Fictional: "quote" \\ { braces } [ ]'
    text = json.dumps({"assertions": [item] * 4})
    guard = StreamAssembler()
    prefix = json.dumps({"assertions": [item] * 3})[:-2]
    guard.feed(json.dumps({"choices": [{"delta": {"content": prefix}}]}))
    assert guard.scanner.metrics["max_identical_assertion_repeats"] == 3
    tail = text[len(prefix) :]
    with pytest.raises(LoopDetectedError) as caught:
        for i in range(0, len(tail), piece_size):
            guard.feed(json.dumps({"choices": [{"delta": {"content": tail[i : i + piece_size]}}]}))
    assert caught.value.partial_response["choices"][0]["message"]["content"].startswith(prefix)
    assert guard.scanner.metrics["max_identical_assertion_repeats"] == 4


def test_loop_identity_and_repetition_v2():
    _, assertion = case_input("symptom")
    item = assertion.model_dump(mode="json")
    alternate = copy.deepcopy(item)
    alternate["concept_text"] = "  " + item["concept_text"].upper() + "  "
    alternate["polarity"] = "negated"
    metrics = repetition_metrics(json.dumps({"assertions": [item] * 26 + [alternate] * 4})[:-2])
    assert {
        k: metrics[k]
        for k in (
            "version",
            "complete_assertions_seen",
            "max_identical_assertion_repeats",
            "duplicate_assertions",
            "polarity_flip_pairs",
        )
    } == {
        "version": "repetition-v2",
        "complete_assertions_seen": 30,
        "max_identical_assertion_repeats": 26,
        "duplicate_assertions": 28,
        "polarity_flip_pairs": 1,
    }
    assert metrics["longest_repeated_ngram_run_tokens"] <= 2
    scanner = AssertionScanner()
    # Nested objects, quote text and trailing incomplete assertions are not direct complete objects.
    scanner.feed(json.dumps({"other": {"assertions": [item] * 4}}))
    assert scanner.metrics["complete_assertions_seen"] == 0
    scanner = AssertionScanner()
    scanner.feed('{"assertions":[' + json.dumps(item) + ',{"kind":')
    assert scanner.metrics["complete_assertions_seen"] == 1


def test_loop_stops_close_http_and_split_like_truncation(terminology, units, intake, tmp_path):
    transcript, assertion = case_input("symptom")
    segments = [transcript.segments[0].model_copy(update={"id": f"s{i}"}) for i in range(3)]
    transcript = transcript.model_copy(update={"segments": tuple(segments)})
    repeated = completion([assertion] * 4)
    body = io.BytesIO(sse_response(repeated))
    http = HttpBackend("http://127.0.0.1:8000")
    http.opener.open = Mock(return_value=body)
    with pytest.raises(LoopDetectedError) as caught:
        http.complete([], {}, temperature=0, max_tokens=1024)
    assert body.closed
    config = load_frozen_config().extractor
    backend = FakeBackend([completion(), completion()])
    real_complete = backend.complete
    backend.complete = Mock(side_effect=[caught.value, completion(), completion()])
    run = extract(backend, transcript, config, config.candidate())
    assert run.failure is None and not run.assertions
    assert run.splits[0]["reason"] == "extractor repetition loop"
    assert run.calls[0]["finish_reason"] == "loop_guard"
    assert run.calls[0]["output_token_count_method"] == "received_content_chunks"
    assert run.measurements["loop_stops"] == 1
    mapper = Mapper(intake, transcript, terminology, units)
    validator = Mock()
    validator.validate.return_value = passing_run(intake, ())
    finish_pipeline(run, mapper, validator, tmp_path / "split")
    discarded = tmp_path / "split" / "discarded-call-0000.json"
    assert discarded.stat().st_mode & 0o777 == 0o600
    assert json.loads(discarded.read_text())["used_for_assertions"] is False
    backend.complete = Mock(side_effect=caught.value)
    single = transcript.model_copy(update={"segments": tuple(segments[:1])})
    run = extract(backend, single, config, config.candidate())
    assert "extractor repetition loop" in run.failure and not run.assertions
    backend.complete = real_complete


class TimedStream(io.BytesIO):
    def __init__(self, parts, clock):
        super().__init__()
        self.parts = iter(parts)
        self.clock = clock

    def read1(self, size):
        delay, data = next(self.parts, (0, b""))
        self.clock[0] += delay
        return data


@pytest.mark.parametrize(
    "parts,total,first,phase",
    [
        ([(31, b"data: {}\n\n")], 100, 30, "first_chunk"),
        ([(1, b'data: {"choices":[]}\n\n'), (61, b": waiting\n\n")], 100, 30, "idle"),
        ([(1, b'data: {"choices":[]}\n\n'), (10, b": waiting\n\n")], 10, 30, "total"),
        ([(5, b"d"), (5, b"a"), (5, b"t")], 12, 30, "total"),
    ],
)
def test_stream_absolute_deadlines(parts, total, first, phase, monkeypatch):
    clock = [0]
    monkeypatch.setattr("auris.extract.stream.time.monotonic", lambda: clock[0])
    with pytest.raises(ExtractionTimeoutError) as caught:
        read_stream(
            TimedStream(parts, clock),
            started=0,
            timeout=total,
            first_chunk_timeout=first,
            identical_assertion_limit=4,
        )
    assert caught.value.phase == phase
    assert caught.value.partial_response is not None
    budget = TimeoutBudget(4096, 1000)
    assert budget.first_chunk_seconds == 45 and budget.seconds > 300


def test_polarity_and_evidence_identity_allow_three_each():
    _, assertion = case_input("symptom")
    item = assertion.model_dump(mode="json")
    opposite = {**item, "polarity": "negated"}
    guard = StreamAssembler()
    for current in [item, opposite] * 3:
        prefix = '{"assertions":[' if guard.content_chunks == 0 else ","
        guard.feed(json.dumps({"choices": [{"delta": {"content": prefix + json.dumps(current)}}]}))
    assert guard.scanner.metrics["max_identical_assertion_repeats"] == 3
    with pytest.raises(LoopDetectedError):
        guard.feed(json.dumps({"choices": [{"delta": {"content": "," + json.dumps(item)}}]}))
    scanner = AssertionScanner()
    variant = copy.deepcopy(item)
    variant["evidence"][0]["quote"] += " fictional difference"
    scanner.feed(json.dumps({"assertions": [item] * 3 + [variant] * 3}))
    assert scanner.metrics["max_identical_assertion_repeats"] == 3
    # A multi-object content event stops scanning on the fourth occurrence.
    guard = StreamAssembler()
    with pytest.raises(LoopDetectedError):
        guard.feed(
            json.dumps(
                {"choices": [{"delta": {"content": json.dumps({"assertions": [item] * 5})}}]}
            )
        )
    assert guard.scanner.metrics["max_identical_assertion_repeats"] == 4


@pytest.mark.parametrize("piece_size", [1, 7, 53])
def test_sse_network_reads_can_split_strings_and_event_delimiters(piece_size):
    class Fragmented(io.BytesIO):
        def read1(self, size):
            return super().read1(min(size, piece_size))

    response = completion()
    response["timings"] = {"predicted_n": 3}
    assert (
        read_stream(
            Fragmented(sse_response(response)),
            started=time.monotonic(),
            timeout=10,
            first_chunk_timeout=5,
            identical_assertion_limit=4,
        )
        == response
    )


def test_content_diagnostics_tolerate_invalid_json_strings():
    assert repetition_metrics('{"bad\\q":[]')["complete_assertions_seen"] == 0
    # The diagnostic scanner must not turn malformed text into an uncaught error.
    config = load_frozen_config().extractor
    transcript, _ = case_input("symptom")
    run = extract(
        FakeBackend([completion(content='{"bad\\q":[]')]), transcript, config, config.candidate()
    )
    assert run.failure and not run.assertions


def test_first_connection_timeout_uses_earliest_deadline():
    client = HttpBackend("http://127.0.0.1:8000")
    client.opener.open = Mock(side_effect=TimeoutError("fictional private detail"))
    with pytest.raises(ExtractionTimeoutError) as caught:
        client.complete([], {}, temperature=0, max_tokens=1024, timeout=10, first_chunk_timeout=30)
    assert caught.value.phase == "total"
    assert client.opener.open.call_args.kwargs["timeout"] == 10
    assert "private" not in str(caught.value)
