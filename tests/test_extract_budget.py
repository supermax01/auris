"""Measured budgets and adaptive splitting with fictional, offline completions."""

import json
import re
from unittest.mock import Mock

import pytest
from extract_helpers import case_input, completion

from auris.assertions import extraction_json_schema
from auris.config import load_frozen_config
from auris.extract.backend import FakeBackend
from auris.extract.chunking import split_segments
from auris.extract.pipeline import extract
from auris.extract.prompt import messages
from auris.extract.tokens import (
    ESTIMATED_INPUT,
    HEADROOM_TOKENS,
    MEASURED_INPUT,
    InputTokenCounter,
    output_budget,
)
from auris.transcript import Transcript


def fictional_transcript(count):
    source, _ = case_input("vital_sign")
    return Transcript(
        segments=tuple(source.segments[0].model_copy(update={"id": f"s{i}"}) for i in range(count))
    )


def measured_completion(tokens, **kwargs):
    response = completion(**kwargs)
    response["usage"]["prompt_tokens"] = tokens
    response["usage"]["total_tokens"] = tokens + response["usage"]["completion_tokens"]
    return response


def test_input_measurement_counts_full_chat_and_schema_once_without_reserving_schema_again():
    transcript = fictional_transcript(2)
    schema = extraction_json_schema()
    tokenizer = Mock(return_value=2080)
    backend = FakeBackend([], input_tokenizer=tokenizer)
    counter = InputTokenCounter(backend, schema)
    count = counter.count(messages(transcript.segments))
    assert count.tokens == 2080 and count.method == MEASURED_INPUT
    assert counter.count(messages(transcript.segments)) == count
    tokenizer.assert_called_once_with(messages(transcript.segments), schema)


def test_raw_text_tokenizer_is_not_mislabelled_as_rendered_input_measurement():
    transcript = fictional_transcript(2)
    schema = extraction_json_schema()
    raw_only = FakeBackend([], tokenizer=lambda _: 1)
    count = InputTokenCounter(raw_only, schema).count(messages(transcript.segments))
    fallback = InputTokenCounter(FakeBackend([]), schema).count(messages(transcript.segments))
    assert count == fallback
    assert count.method == ESTIMATED_INPUT and count.tokens > 1


@pytest.mark.parametrize(
    "input_tokens,expected",
    [(2080, 4096), (3840, 4096), (3841, 4095), (6000, 1936), (6912, 1024)],
)
def test_per_call_budget_ceiling_context_limit_and_exact_floor(input_tokens, expected):
    config = load_frozen_config().extractor
    backend = FakeBackend(
        [measured_completion(input_tokens)], input_tokenizer=lambda *_: input_tokens
    )
    run = extract(backend, fictional_transcript(2), config, config.candidate())
    assert run.failure is None
    assert backend.calls[0]["max_tokens"] == run.calls[0]["max_tokens"] == expected
    assert input_tokens + expected + HEADROOM_TOKENS <= config.n_ctx
    assert run.calls[0]["input_token_comparison"] == {
        "status": "match",
        "tokenizer_prompt_tokens": input_tokens,
        "usage_prompt_tokens": input_tokens,
        "difference": 0,
    }
    metadata = run.document()["metadata"]
    assert metadata["input_token_comparisons"] == [run.calls[0]["input_token_comparison"]]
    assert metadata["decoding"]["max_output_tokens"] == 4096
    assert metadata["decoding"]["min_output_tokens"] == 1024
    assert metadata["decoding"]["headroom_tokens"] == 256


def test_budget_below_floor_is_not_rounded_up():
    assert output_budget(6913, n_ctx=8192, max_output_tokens=4096) == 1023


def test_planner_shrinks_before_call_to_preserve_output_floor():
    config = load_frozen_config().extractor
    transcript = fictional_transcript(6)

    def measured(chat, schema):
        return 3000 + 1000 * len(re.findall(r"^\[s\d+ \|", chat[1]["content"], re.MULTILINE))

    backend = FakeBackend([measured_completion(6000)] * 4, input_tokenizer=measured)
    run = extract(backend, transcript, config, config.candidate())
    assert run.failure is None
    assert [call["segment_ids"] for call in run.calls] == [
        ["s0", "s1", "s2"],
        ["s1", "s2", "s3"],
        ["s2", "s3", "s4"],
        ["s3", "s4", "s5"],
    ]
    assert all(call["max_tokens"] == 1936 for call in backend.calls)
    assert all(call["max_tokens"] >= config.min_output_tokens for call in backend.calls)


def test_unfit_single_segment_fails_without_any_completion():
    config = load_frozen_config().extractor
    backend = FakeBackend([], input_tokenizer=lambda *_: 7000)
    run = extract(backend, fictional_transcript(1), config, config.candidate())
    assert run.failure and not run.assertions and not backend.calls


def test_fallback_budget_is_explicit_and_does_not_claim_tokenizer_verification():
    config = load_frozen_config().extractor
    backend = FakeBackend([completion()])
    run = extract(backend, fictional_transcript(2), config, config.candidate())
    assert run.failure is None
    call = run.calls[0]
    assert call["token_count_method"] == ESTIMATED_INPUT
    assert call["max_tokens"] == min(4096, 8192 - call["reserved_input_tokens"] - 256)
    assert call["input_token_comparison"] == {
        "status": "estimate_only",
        "tokenizer_prompt_tokens": None,
        "usage_prompt_tokens": 111,
        "difference": None,
    }


@pytest.mark.parametrize("usage", [{"prompt_tokens": 112}, {}, None, {"prompt_tokens": True}])
def test_unverifiable_exact_measurement_records_comparison_and_fails_closed(usage):
    config = load_frozen_config().extractor
    response = completion()
    response["usage"] = usage
    backend = FakeBackend([response], input_tokenizer=lambda *_: 111)
    run = extract(backend, fictional_transcript(2), config, config.candidate())
    assert "could not be verified" in run.failure
    assert not run.assertions and len(backend.calls) == 1
    comparison = run.document()["metadata"]["input_token_comparisons"][0]
    assert comparison["status"] == (
        "mismatch" if usage == {"prompt_tokens": 112} else "usage_unavailable"
    )
    assert comparison["difference"] == (1 if comparison["status"] == "mismatch" else None)


@pytest.mark.parametrize("count", range(2, 50))
def test_split_boundaries_coverage_overlap_and_strict_progress(count):
    segments = fictional_transcript(count).segments
    left, right = split_segments(segments)
    overlap = min(2, count - 2)
    assert 0 < len(left) < count and 0 < len(right) < count
    assert abs(len(left) - len(right)) <= 1
    assert {s.id for s in (*left, *right)} == {s.id for s in segments}
    assert len({s.id for s in left} & {s.id for s in right}) == overlap
    if overlap:
        assert left[-overlap:] == right[:overlap]


@pytest.mark.parametrize("valid_json", [False, True])
def test_truncation_split_success_discards_entire_attempt(valid_json):
    transcript = fictional_transcript(6)
    _, assertion = case_input("vital_sign")
    config = load_frozen_config().extractor
    discarded = completion([assertion], finish_reason="length")
    if not valid_json:
        discarded["choices"][0]["message"]["content"] = "FICTIONAL_TRUNCATED_BODY{"
    backend = FakeBackend([discarded, completion(), completion()])
    run = extract(backend, transcript, config, config.candidate())
    assert run.failure is None and not run.assertions
    assert [call["segment_ids"] for call in run.calls] == [
        ["s0", "s1", "s2", "s3", "s4", "s5"],
        ["s0", "s1", "s2", "s3"],
        ["s2", "s3", "s4", "s5"],
    ]
    assert "raw_response" not in run.calls[0] and "parsed_assertions" not in run.calls[0]
    assert run.calls[0]["output_discarded"] is True
    split = run.document()["metadata"]["splits"][0]
    assert split["reason"] == "finish_reason=length" and split["window_id"] == "0"
    assert split["child_window_ids"] == ["0.0", "0.1"]
    assert split["child_segment_ids"] == [call["segment_ids"] for call in run.calls[1:]]
    assert split["overlap_segment_ids"] == ["s2", "s3"]
    assert "FICTIONAL_TRUNCATED_BODY" not in json.dumps(run.document())


def test_successful_split_children_merge_overlapping_assertions():
    transcript = fictional_transcript(4)
    _, assertion = case_input("vital_sign")
    config = load_frozen_config().extractor
    backend = FakeBackend(
        [completion(finish_reason="length"), completion([assertion]), completion([assertion])]
    )
    run = extract(backend, transcript, config, config.candidate())
    assert run.failure is None and run.assertions == (assertion,)


def test_split_children_are_measured_again_and_receive_larger_output_budgets():
    config = load_frozen_config().extractor

    def measured(chat, schema):
        return 6000 if "[s3 |" in chat[1]["content"] and "[s0 |" in chat[1]["content"] else 5000

    backend = FakeBackend(
        [
            measured_completion(6000, finish_reason="length"),
            measured_completion(5000),
            measured_completion(5000),
        ],
        input_tokenizer=measured,
    )
    run = extract(backend, fictional_transcript(4), config, config.candidate())
    assert run.failure is None
    assert [call["reserved_input_tokens"] for call in run.calls] == [6000, 5000, 5000]
    assert [call["max_tokens"] for call in backend.calls] == [1936, 2936, 2936]
    assert all(call["input_token_comparison"]["status"] == "match" for call in run.calls)


def test_recursive_split_is_deterministic_and_never_repeats_identical_inference():
    config = load_frozen_config().extractor
    expected = [
        ["s0", "s1", "s2", "s3"],
        ["s0", "s1", "s2"],
        ["s0", "s1"],
        ["s1", "s2"],
        ["s1", "s2", "s3"],
        ["s2", "s3"],
    ]
    plans = []
    for _ in range(2):
        responses = [
            completion(finish_reason="length" if len(ids) > 2 else "stop") for ids in expected
        ]
        backend = FakeBackend(responses, input_tokenizer=lambda *_: 111)
        run = extract(backend, fictional_transcript(4), config, config.candidate())
        assert run.failure is None
        assert [call["segment_ids"] for call in run.calls] == expected
        assert len({json.dumps(call["messages"]) for call in backend.calls}) == 6
        assert run.window_reuses == [{"window_id": "0.1.0", "source_window_id": "0.0.1"}]
        plans.append((run.splits, run.window_reuses, [call["max_tokens"] for call in run.calls]))
    assert plans[0] == plans[1]


def test_single_segment_truncation_fails_entire_run_after_successful_sibling():
    config = load_frozen_config().extractor
    transcript = fictional_transcript(2)
    _, assertion = case_input("vital_sign")
    assertion = assertion.model_copy(
        update={"evidence": (assertion.evidence[0].model_copy(update={"segment_id": "s0"}),)}
    )
    backend = FakeBackend(
        [
            completion(finish_reason="length"),
            completion([assertion]),
            completion([assertion], finish_reason="length"),
        ]
    )
    run = extract(backend, transcript, config, config.candidate())
    assert "single transcript segment still truncated" in run.failure
    assert not run.assertions and len(backend.calls) == 3
    assert run.calls[1]["parsed_assertions"]  # Diagnostics only, never exported.
    assert all("raw_response" not in run.calls[i] for i in (0, 2))
    assert run.splits[0]["overlap_segment_ids"] == []


def test_truncation_with_tokenizer_mismatch_still_discards_body():
    config = load_frozen_config().extractor
    backend = FakeBackend([completion(finish_reason="length")], input_tokenizer=lambda *_: 112)
    run = extract(backend, fictional_transcript(2), config, config.candidate())
    assert run.failure and not run.splits and not run.assertions
    assert run.calls[0]["output_discarded"] and "raw_response" not in run.calls[0]
    assert run.calls[0]["input_token_comparison"]["status"] == "mismatch"
