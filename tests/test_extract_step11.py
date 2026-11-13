"""Step 11 evidence regressions. All transcript text is fictional/synthetic."""

import json
from pathlib import Path

import pytest
from extract_helpers import case_input, completion

from auris.assertions import EvidenceItem
from auris.config import load_frozen_config
from auris.evidence import (
    DISFLUENCY_FILLERS,
    DISFLUENCY_FILLERS_VERSION,
    EVIDENCE_CHECKER_VERSION,
    EvidenceFailure,
    align_evidence_item,
    has_negation,
)
from auris.extract.backend import FakeBackend
from auris.extract.citations import classify_citation, resolve_citations
from auris.extract.evidence_replay import replay_evidence
from auris.extract.pipeline import extract
from auris.extract.profiles import PROFILES
from auris.extract.prompt import (
    WINDOW_INSTRUCTION,
    WINDOW_PROMPT_VERSION,
    messages,
    prompt_text,
    render_transcript,
    segment_location,
)
from auris.fhir import Mapper
from auris.render.note import note_items
from auris.transcript import Transcript, TranscriptSegment, WordTiming
from auris.validation.report import validation_document


def segment(text, id_="s1"):
    return TranscriptSegment(
        id=id_,
        text=text,
        speaker_cluster_id="fictional",
        role="patient",
        start_seconds=10,
        end_seconds=30,
    )


def align(text, quote, **kwargs):
    return align_evidence_item(
        EvidenceItem(segment_id="s1", quote=quote), Transcript(segments=(segment(text),)), **kwargs
    )


@pytest.mark.parametrize("empty", ["", " \t\n", "...?!", "—"])
def test_empty_segments_hidden_but_retained_in_windows(empty):
    transcript, assertion = case_input("vital_sign")
    hidden = transcript.segments[0].model_copy(
        update={"id": "hidden", "text": empty, "raw_text": "<INAUDIBLE_SPEECH/>"}
    )
    transcript = transcript.model_copy(update={"segments": (*transcript.segments, hidden)})
    assertion = assertion.model_copy(
        update={
            "evidence": (assertion.evidence[0].model_copy(update={"segment_id": hidden.id}),),
        }
    )
    backend = FakeBackend([completion([assertion])])
    config = load_frozen_config().extractor
    run = extract(backend, transcript, config, config.candidate())
    assert run.failure is None
    call = run.calls[0]
    assert call["segment_ids"] == ["s1", "hidden"]
    assert call["allowed_segment_ids"] == ["s1"]
    assert call["hidden_segment_count"] == 1
    assert run.citation_failures == {0: frozenset({"hidden"})}
    assert "hidden" not in backend.calls[0]["messages"][1]["content"]
    assert render_transcript((hidden,)) == ""
    assert messages((hidden,))[1]["content"] == WINDOW_INSTRUCTION.format(ids="none")
    result = align_evidence_item(
        assertion.evidence[0], transcript, allowed_segment_ids=frozenset({"s1"})
    )
    assert result.failure == EvidenceFailure.CITED_EMPTY_SEGMENT
    assert classify_citation("hidden", transcript.segments, transcript) == "cited_empty_segment"
    metadata = run.document()["metadata"]
    assert metadata["window_prompt_version"] == WINDOW_PROMPT_VERSION == "allowed-segment-ids-v2"
    assert metadata["evidence_checker_version"] == EVIDENCE_CHECKER_VERSION == "evidence-v2"
    assert metadata["disfluency_fillers_version"] == DISFLUENCY_FILLERS_VERSION == "fillers-v1"


def test_hidden_location_is_never_resolved_as_a_shown_alias():
    transcript, assertion = case_input("vital_sign")
    hidden = transcript.segments[0].model_copy(update={"text": ""})
    alias = assertion.evidence[0].model_copy(update={"segment_id": segment_location(hidden)})
    assertion = assertion.model_copy(update={"evidence": (alias,)})
    resolved, changes = resolve_citations((assertion,), (hidden,))
    assert resolved == (assertion,) and changes == []


def test_all_nonempty_fixture_and_demo_prompts_are_byte_identical():
    # Independent historical renderer, exercised on every nested transcript in
    # the fixture corpus and the bundled demo, across every extraction profile.
    transcripts = []

    def visit(value):
        if isinstance(value, dict):
            if "segments" in value:
                transcripts.append(Transcript.model_validate(value))
            else:
                for child in value.values():
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    roots = (Path("tests/fixtures"), Path("examples"))
    for root in roots:
        for path in sorted(root.rglob("*.json")):
            visit(json.loads(path.read_text()))
    checked = 0
    for transcript in transcripts:
        if any(not s.text.strip() for s in transcript.segments):
            continue  # Deliberate empty-segment adapter fixtures.
        old_render = "\n".join(
            f"[{s.id} | {s.role} | {segment_location(s)}] "
            + s.text.replace("\r", "\\r").replace("\n", "\\n")
            for s in transcript.segments
        )
        assert render_transcript(transcript.segments).encode() == old_render.encode()
        for profile in PROFILES:
            old_messages = [
                {"role": "system", "content": prompt_text(profile)},
                {
                    "role": "user",
                    "content": WINDOW_INSTRUCTION.format(
                        ids=", ".join(s.id for s in transcript.segments) or "none"
                    )
                    + old_render,
                },
            ]
            assert messages(transcript.segments, profile) == old_messages
        checked += 1
    assert checked >= 38


@pytest.mark.parametrize(
    "text,quote,span",
    [
        ("Well, I um have pain.", "I have pain", "I um have pain"),
        ("Uh and get some rest.", "Um and get some rest", "and get some rest"),
        (
            "Yesterday it didn't, it didn't cut much.",
            "Yesterday it didn't cut much",
            "Yesterday it didn't, it didn't cut much",
        ),
        (
            "The rash is on the, on the hands.",
            "rash is on the hands",
            "rash is on the, on the hands",
        ),
        ("The pain pain pain is here.", "The pain is here", "The pain pain pain is here"),
        (
            "Lately I feel sore I feel sore today.",
            "Lately I feel sore today",
            "Lately I feel sore I feel sore today",
        ),
        (
            "The rash is on um the, on the hands.",
            "rash is on the hands",
            "rash is on um the, on the hands",
        ),
        ("The pain is um severe.", "The the pain is uh severe", "The pain is um severe"),
        ("A cafe\u0301 um visit today.", "café visit", "cafe\u0301 um visit"),
    ],
)
def test_disfluencies_align_to_original_span(text, quote, span):
    assert not align(text, quote, checker_version="evidence-v1").ok
    result = align(text, quote).aligned
    assert result is not None and result.alignment_method == "disfluency-v2"
    assert result.aligned_text == span
    assert (result.char_start, result.char_end) == (text.index(span), text.index(span) + len(span))
    assert result.model_dump()["alignment_method"] == "disfluency-v2"


@pytest.mark.parametrize("filler", sorted(DISFLUENCY_FILLERS))
def test_each_closed_list_filler_is_removed_on_both_sides(filler):
    assert align(f"I {filler} have pain.", "I have pain").ok
    assert align("I have pain.", f"I {filler} have pain").ok


@pytest.mark.parametrize(
    "text,quote",
    [
        ("I um have severe pain.", "I have pain"),
        ("I um have no no pain.", "I have pain"),
        ("I um have pain.", "I have no pain"),
        ("I um do not have pain.", "I do have pain"),
        ("I um don't have pain.", "I do have pain"),
        ("I um have pain.", "I don't have pain"),
        ("The dose is um 1.5 mg.", "The dose is 15 mg"),
        ("Start one two three four one two three four end.", "Start one two three four end"),
        ("um um", "er er"),
    ],
)
def test_content_negation_numbers_and_long_repeats_cannot_be_deleted(text, quote):
    assert not align(text, quote).ok


@pytest.mark.parametrize("token", ["mm-hmm", "mhm", "uh-huh", "uh huh", "yeah", "no", "nope"])
def test_assent_and_denial_never_disappear(token):
    assert not align(f"I {token} um have pain.", "I have pain").ok
    assert not align("I um have pain.", f"I {token} have pain").ok
    assert token not in DISFLUENCY_FILLERS
    assert align(f"I {token} um have pain.", f"I {token} have pain").ok


def test_uh_huh_is_preserved_as_a_whole():
    assert not align("I uh-huh um have pain.", "I huh have pain").ok


def test_negation_collapse_keeps_a_cue_and_original_context():
    result = align("I um have no no pain.", "I have no pain").aligned
    assert result is not None and has_negation(result.aligned_text)
    assert "no no" in result.aligned_text
    assert align("No, pain is um severe.", "pain is severe").failure == (
        EvidenceFailure.OMITTED_NEGATION_CONTEXT
    )
    assert align("No pain no pain.", "Um pain").failure == (
        EvidenceFailure.OMITTED_NEGATION_CONTEXT
    )


def test_exact_is_preferred_even_when_fallback_would_match_an_earlier_span():
    result = align("I um have pain. I have pain.", "I have pain").aligned
    assert result is not None and result.alignment_method == "exact-v1"
    assert result.char_start == 16
    assert result.model_dump()["alignment_method"] == "exact-v1"
    # An unsafe exact occurrence must never fall through to a safer v2 match.
    assert align("No pain is severe. Pain is um severe.", "pain is severe").failure == (
        EvidenceFailure.OMITTED_NEGATION_CONTEXT
    )


def test_word_timing_offsets_include_fillers_and_final_repetition():
    text = "Well pain um here pain here ends."
    words = tuple(
        WordTiming(word=word, start_seconds=10 + i, end_seconds=11 + i)
        for i, word in enumerate(text.split())
    )
    timed = segment(text).model_copy(update={"words": words})
    result = align_evidence_item(
        EvidenceItem(segment_id="s1", quote="Well pain here ends"),
        Transcript(segments=(timed,)),
    ).aligned
    assert result is not None and result.alignment_method == "disfluency-v2"
    assert result.aligned_text == text[:-1]
    assert result.audio_source == "word_timings" and result.audio_approximate is True
    assert (result.audio_start_seconds, result.audio_end_seconds) == (10, 17)
    # A repeat at the endpoint still includes the final original copy.
    end = align_evidence_item(
        EvidenceItem(segment_id="s1", quote="Well pain here"),
        Transcript(segments=(timed,)),
    ).aligned
    assert end is not None and end.aligned_text == "Well pain um here pain here"
    assert (end.audio_start_seconds, end.audio_end_seconds) == (10, 16)


def test_consistency_and_reviewer_display_use_original_span(terminology, units, intake):
    transcript, assertion = case_input("symptom")
    text = "My mother um mother has a cough."
    transcript = Transcript(segments=(segment(text),))
    assertion = assertion.model_copy(
        update={
            "evidence": (EvidenceItem(segment_id="s1", quote="My mother has a cough"),),
        }
    )
    outcome = Mapper(intake, transcript, terminology, units).map_assertion(assertion, 0)
    assert outcome.evidence.label == "transcript-supported"
    assert "experiencer_cue_conflict" in outcome.review_reasons
    item = note_items((outcome,), transcript, None)[0]
    assert "mother um mother" in item.fact
    assert "matched ignoring fillers/repeats" in item.receipts


def test_replay_freezes_items_and_failed_visits_and_checks_baseline(terminology, units, intake):
    from validator_helpers import passing_run

    from auris.extract.consultation_replay import replay_consultation

    _, assertion = case_input("symptom")
    transcript = Transcript(
        segments=(
            segment("I um have a cough."),
            segment("", "hidden"),
            segment("I have a cough.", "exact"),
        )
    )
    assertions = tuple(
        assertion.model_copy(
            update={
                "evidence": (EvidenceItem(segment_id=id_, quote="I have a cough"),),
            }
        )
        for id_ in ("s1", "hidden", "exact")
    )
    before = Mapper(intake, transcript, terminology, units, evidence_checker_version="evidence-v1")
    after = Mapper(intake, transcript, terminology, units)
    mapped = before.map_all(assertions)
    validation = validation_document(intake, mapped, passing_run(intake, mapped))
    document = {
        "metadata": {"transcript_sha256": transcript.content_hash},
        "assertions": [a.model_dump(mode="json") for a in assertions],
    }
    report = replay_evidence(document, validation, before, after)
    assert report["evidence_items"] == 3 and report["v1_aligned_items"] == 1
    assert report["v1_aligned_span_changes"] == 0
    assert report["changed_items"] == {
        "quote_not_found->disfluency-v2": 1,
        "quote_not_found->cited_empty_segment": 1,
    }
    assert report["changed_export_states"] == {"validation_failed->ledger_only": 1}
    assert "I have" not in json.dumps(report)  # The replay output contains no quotes.
    legacy = replay_consultation({**document, "calls": []}, validation, after)
    assert legacy["combined_state_changes"] == {
        "validation_failed->validation_failed": 2,
        "ledger_only->ledger_only": 1,
    }  # Historical Step 9 replay does not silently gain Step 11 recoveries.
    failed = replay_evidence(
        {**document, "failure": "fictional failure"}, validation, before, after
    )
    assert failed["complete"] is False and failed["evidence_items"] == 0
    with pytest.raises(ValueError, match="hash mismatch"):
        replay_evidence(
            {**document, "metadata": {"transcript_sha256": "wrong"}}, validation, before, after
        )
    validation["resources"][3]["export_state"] = "ledger_only"
    with pytest.raises(ValueError, match="baseline differs"):
        replay_evidence(document, validation, before, after)
