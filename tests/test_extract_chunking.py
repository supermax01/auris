"""Context planning and conflict-preserving merging, entirely offline."""

import json

import pytest
from extract_helpers import case_input

from auris.assertions import parse_assertion
from auris.extract.backend import ExtractionError, FakeBackend
from auris.extract.chunking import HEADROOM_TOKENS, merge_assertions, plan_chunks
from auris.transcript import Transcript


def measured_backend():
    # Fictional tokenizer: count characters to stress context boundaries offline.
    return FakeBackend(
        [],
        tokenizer=len,
        input_tokenizer=lambda messages, schema: (
            len(json.dumps(schema)) + sum(len(message["content"]) for message in messages)
        ),
    )


def test_whole_encounter_and_conservative_fallback():
    transcript, _ = case_input("vital_sign")
    chunks = plan_chunks(transcript, {}, FakeBackend([]), n_ctx=8192, min_output_tokens=1024)
    assert len(chunks) == 1 and chunks[0].segments == transcript.segments
    assert chunks[0].token_count_method == "conservative_utf8_chars"
    assert chunks[0].input_tokens + 1024 + HEADROOM_TOKENS <= 8192


def test_windows_have_two_segment_overlap_and_cover_every_segment():
    source, _ = case_input("vital_sign")
    segments = tuple(
        source.segments[0].model_copy(update={"id": f"s{i}", "text": "x" * 300}) for i in range(13)
    )
    transcript = Transcript(segments=segments)
    chunks = plan_chunks(transcript, {}, measured_backend(), n_ctx=6600, min_output_tokens=1024)
    assert len(chunks) > 1
    for first, second in zip(chunks, chunks[1:], strict=False):
        assert first.segments[-2:] == second.segments[:2]
        assert first.segments[0] != second.segments[0]
    assert {s.id for c in chunks for s in c.segments} == {s.id for s in segments}
    assert all(c.input_tokens + 1024 + HEADROOM_TOKENS <= 6600 for c in chunks)
    assert all(c.token_count_method == "server_apply_template_tokenize" for c in chunks)


@pytest.mark.parametrize("context,reason", [(1000, "no context"), (5600, "one transcript segment")])
def test_oversized_prompt_or_segment_fails_clearly(context, reason):
    transcript, _ = case_input("vital_sign")
    transcript = Transcript(
        segments=(transcript.segments[0].model_copy(update={"text": "x" * 2000}),)
    )
    with pytest.raises(ExtractionError, match=reason):
        plan_chunks(transcript, {}, measured_backend(), n_ctx=context, min_output_tokens=1024)


def test_overlap_without_progress_is_refused():
    source, _ = case_input("vital_sign")
    transcript = Transcript(
        segments=tuple(
            source.segments[0].model_copy(update={"id": f"s{i}", "text": "x" * 500})
            for i in range(4)
        )
    )
    with pytest.raises(ExtractionError, match="2-segment overlap"):
        plan_chunks(transcript, {}, measured_backend(), n_ctx=6400, min_output_tokens=1024)


def test_identical_overlapping_facts_merge_without_dropping_evidence():
    transcript, assertion = case_input("vital_sign")
    short = assertion.model_copy(
        update={
            "evidence": (assertion.evidence[0].model_copy(update={"quote": "heart rate is 72"}),)
        }
    )
    result = merge_assertions([(assertion,), (short,)], transcript)
    assert len(result.assertions) == 1
    assert len(result.assertions[0].evidence) == 2
    assert not result.conflict_positions


@pytest.mark.parametrize(
    "changes",
    [
        {"value_text": "73"},
        {"polarity": "negated"},
        {"experiencer": "family_member"},
        {"temporality": "past"},
    ],
)
def test_merge_disagreements_preserve_both_assertions_for_review(changes):
    transcript, assertion = case_input("vital_sign")
    result = merge_assertions(
        [(assertion,), (parse_assertion({**assertion.model_dump(mode="json"), **changes}),)],
        transcript,
    )
    assert len(result.assertions) == 2
    assert result.conflict_positions == {0, 1}


def test_same_concept_distinct_evidence_is_not_deduplicated():
    source, first = case_input("vital_sign")
    transcript = Transcript(
        segments=(*source.segments, source.segments[0].model_copy(update={"id": "s2"}))
    )
    second = first.model_copy(
        update={"evidence": (first.evidence[0].model_copy(update={"segment_id": "s2"}),)}
    )
    result = merge_assertions([(first, second)], transcript)
    assert len(result.assertions) == 2 and not result.conflict_positions


def test_merged_encounter_can_exceed_per_call_assertion_bound():
    source, first = case_input("vital_sign")
    transcript = Transcript(
        segments=tuple(source.segments[0].model_copy(update={"id": f"s{i}"}) for i in range(70))
    )
    assertions = [
        first.model_copy(
            update={"evidence": (first.evidence[0].model_copy(update={"segment_id": s.id}),)}
        )
        for s in transcript.segments
    ]
    assert len(merge_assertions([assertions[:35], assertions[35:]], transcript).assertions) == 70


def test_kind_disagreement_on_same_concept_and_span_is_reviewed():
    transcript, assertion = case_input("assessed_problem")
    reported = parse_assertion(
        {**assertion.model_dump(mode="json"), "kind": "patient_reported_condition"}
    )
    result = merge_assertions([(assertion,), (reported,)], transcript)
    assert result.conflict_positions == {0, 1}
    assert len(result.assertions) == 2


def test_evidence_union_over_bound_keeps_both_records():
    from auris.assertions import EvidenceItem

    transcript, assertion = case_input("vital_sign")
    # Punctuation-normalized variants all align to the same fictional sentence.
    quotes = [f"Your heart rate is 72{'.' * n}" for n in range(1, 10)]
    first = assertion.model_copy(
        update={"evidence": tuple(EvidenceItem(segment_id="s1", quote=q) for q in quotes[:8])}
    )
    second = assertion.model_copy(
        update={"evidence": (EvidenceItem(segment_id="s1", quote=quotes[8]),)}
    )
    result = merge_assertions([(first,), (second,)], transcript)
    assert len(result.assertions) == 2 and result.conflict_positions == {0, 1}
    assert sum(len(a.evidence) for a in result.assertions) == 9
