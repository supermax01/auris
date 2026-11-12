"""Evidence alignment tests (PLAN A5, Step 2a). All data is fictional."""

import pytest

from auris.assertions import EvidenceItem, SymptomAssertion
from auris.evidence import (
    EvidenceFailure,
    EvidenceItemResult,
    EvidenceLane,
    align_assertion_evidence,
    align_evidence_item,
)
from auris.transcript import Transcript, TranscriptSegment, WordTiming


def _transcript(*segments: TranscriptSegment) -> Transcript:
    return Transcript(segments=segments)


def _segment(
    text: str,
    *,
    segment_id: str = "s1",
    start: float = 10.0,
    end: float = 20.0,
    words: tuple[WordTiming, ...] | None = None,
) -> TranscriptSegment:
    return TranscriptSegment(
        id=segment_id,
        speaker_cluster_id="spk-a",
        role="patient",
        start_seconds=start,
        end_seconds=end,
        text=text,
        words=words,
    )


def _align(
    text: str,
    quote: str,
    *,
    start: float = 10.0,
    end: float = 20.0,
    words: tuple[WordTiming, ...] | None = None,
) -> EvidenceItemResult:
    transcript = _transcript(_segment(text, start=start, end=end, words=words))
    return align_evidence_item(EvidenceItem(segment_id="s1", quote=quote), transcript)


def test_exact_quote_aligns_to_exact_offsets() -> None:
    result = _align("I have had a cough for three days.", "cough for three days")
    assert result.ok
    assert result.aligned is not None
    aligned = result.aligned
    assert aligned.aligned_text == "cough for three days"
    assert (aligned.char_start, aligned.char_end) == (13, 33)


def test_case_difference_aligns() -> None:
    result = _align("I have NO penicillin allergy.", "no PENICILLIN allergy")
    assert result.ok
    assert result.aligned is not None
    assert result.aligned.aligned_text == "NO penicillin allergy"
    assert (result.aligned.char_start, result.aligned.char_end) == (7, 28)


def test_punctuation_difference_aligns() -> None:
    result = _align("Well, the dose is: 500 milligrams.", "dose is 500 milligrams")
    assert result.ok
    assert result.aligned is not None
    assert result.aligned.aligned_text == "dose is: 500 milligrams"
    assert (result.aligned.char_start, result.aligned.char_end) == (10, 33)


def test_whitespace_difference_aligns() -> None:
    result = _align("I  have   a\tcough today.", "have a cough")
    assert result.ok
    assert result.aligned is not None
    assert result.aligned.aligned_text == "have   a\tcough"
    assert (result.aligned.char_start, result.aligned.char_end) == (3, 17)


def test_hyphen_vs_space_aligns() -> None:
    result = _align("We will follow up next week.", "follow-up next")
    assert result.ok


def test_quote_from_wrong_segment_fails() -> None:
    transcript = _transcript(
        _segment("I have a cough.", segment_id="s1"),
        _segment("Take one tablet daily.", segment_id="s2"),
    )
    result = align_evidence_item(
        EvidenceItem(segment_id="s1", quote="one tablet daily"), transcript
    )
    assert not result.ok
    assert result.failure is EvidenceFailure.QUOTE_NOT_FOUND


def test_missing_segment_fails_with_reason() -> None:
    transcript = _transcript(_segment("I have a cough."))
    result = align_evidence_item(EvidenceItem(segment_id="nope", quote="cough"), transcript)
    assert result.failure is EvidenceFailure.SEGMENT_NOT_FOUND


@pytest.mark.parametrize(
    "text",
    [
        "no penicillin allergy",
        "I have no penicillin allergy.",
        "No, not a penicillin allergy at all.",
        "I don’t have a penicillin allergy.",
        "Penicillin allergy: no.",
        "Penicillin allergy was ruled out.",
    ],
)
def test_dropped_negation_context_does_not_align(text: str) -> None:
    result = _align(text, "penicillin allergy")
    assert not result.ok
    assert result.aligned is None
    assert result.failure is EvidenceFailure.OMITTED_NEGATION_CONTEXT


def test_internal_dropped_negation_does_not_align() -> None:
    result = _align("I have no penicillin allergy.", "I have penicillin allergy")
    assert not result.ok
    assert result.failure is EvidenceFailure.QUOTE_NOT_FOUND


def test_negation_word_itself_must_match() -> None:
    result = _align("I have a penicillin allergy.", "no penicillin allergy")
    assert not result.ok


@pytest.mark.parametrize(
    ("text", "quote"),
    [
        ("150", "15"),
        ("15", "150"),
        ("Take 15 milligrams daily.", "150 milligrams"),
        ("The value is 1.5.", "1"),
        ("The value is 1.5.", "1 5"),
        ("The value is -15.", "15"),
        ("The value is +15.", "15"),
        ("The value is 1,500.", "1 500"),
    ],
)
def test_changed_number_does_not_align(text: str, quote: str) -> None:
    result = _align(text, quote)
    assert not result.ok
    assert result.failure is EvidenceFailure.QUOTE_NOT_FOUND


def test_changed_word_does_not_align() -> None:
    result = _align("I have had a cough for three days.", "cough for four days")
    assert not result.ok


def test_punctuation_only_quote_fails() -> None:
    result = _align("I have a cough.", "?!")
    assert result.failure is EvidenceFailure.QUOTE_HAS_NO_TOKENS


def test_unicode_accented_quote_aligns() -> None:
    result = _align("The café visit is tomorrow.", "café visit")
    assert result.ok
    assert result.aligned is not None
    assert result.aligned.aligned_text == "café visit"
    assert (result.aligned.char_start, result.aligned.char_end) == (4, 14)


def test_unicode_decomposed_accent_aligns_to_composed_quote() -> None:
    decomposed = "The café visit is tomorrow."
    result = _align(decomposed, "café visit")
    assert result.ok
    aligned = result.aligned
    assert aligned is not None
    assert (aligned.char_start, aligned.char_end) == (4, 15)
    assert aligned.aligned_text == "cafe\u0301 visit"


def test_accent_difference_does_not_align() -> None:
    result = _align("The café visit is tomorrow.", "cafe visit")
    assert not result.ok


def test_curly_apostrophe_aligns_to_straight_quote() -> None:
    result = _align("I don’t have a fever.", "don't have a fever")
    assert result.ok
    assert result.aligned is not None
    assert result.aligned.aligned_text == "don’t have a fever"
    assert (result.aligned.char_start, result.aligned.char_end) == (2, 20)


def test_first_occurrence_is_chosen() -> None:
    result = _align("Pain here, and pain there too.", "pain")
    assert result.ok
    assert result.aligned is not None
    assert result.aligned.char_start == 0


def test_audio_offsets_fall_back_to_segment_bounds() -> None:
    result = _align("I have a cough.", "cough", start=42.0, end=47.5)
    aligned = result.aligned
    assert aligned is not None
    assert (aligned.audio_start_seconds, aligned.audio_end_seconds) == (42.0, 47.5)
    assert aligned.audio_approximate is True
    assert aligned.audio_source == "segment_bounds"


def test_audio_offsets_come_from_word_timings() -> None:
    words = (
        WordTiming(word="I", start_seconds=10.0, end_seconds=10.2),
        WordTiming(word="have", start_seconds=10.2, end_seconds=10.6),
        WordTiming(word="a", start_seconds=10.6, end_seconds=10.8),
        WordTiming(word="cough.", start_seconds=10.8, end_seconds=11.5),
    )
    result = _align("I have a cough.", "a cough", words=words)
    aligned = result.aligned
    assert aligned is not None
    assert (aligned.audio_start_seconds, aligned.audio_end_seconds) == (10.6, 11.5)
    assert aligned.audio_source == "word_timings"
    assert aligned.audio_approximate is True  # timings are approximate by default


def test_mismatched_word_list_falls_back_to_segment_bounds() -> None:
    words = (WordTiming(word="Something", start_seconds=10.0, end_seconds=10.5),)
    result = _align("I have a cough.", "cough", words=words)
    aligned = result.aligned
    assert aligned is not None
    assert aligned.audio_source == "segment_bounds"
    assert (aligned.audio_start_seconds, aligned.audio_end_seconds) == (10.0, 20.0)
    assert aligned.audio_approximate is True


def _assertion(quotes: list[tuple[str, str]]) -> SymptomAssertion:
    return SymptomAssertion(
        kind="symptom",
        concept_text="cough",
        polarity="affirmed",
        experiencer="patient",
        temporality="current",
        source_speaker_role="patient",
        evidence=tuple(
            EvidenceItem(segment_id=segment_id, quote=quote) for segment_id, quote in quotes
        ),
    )


def test_assertion_passes_only_when_all_quotes_align() -> None:
    transcript = _transcript(
        _segment("I have a cough.", segment_id="s1"),
        _segment("It started Monday.", segment_id="s2"),
    )
    passing = align_assertion_evidence(
        _assertion([("s1", "have a cough"), ("s2", "started Monday")]), transcript
    )
    assert passing.lane is EvidenceLane.PASS
    assert passing.label == "transcript-supported"

    failing = align_assertion_evidence(
        _assertion([("s1", "have a cough"), ("s2", "started Tuesday")]), transcript
    )
    assert failing.lane is EvidenceLane.FAIL
    assert failing.label is None
    assert failing.failures == (EvidenceFailure.QUOTE_NOT_FOUND,)


@pytest.mark.parametrize("separator", [". ", "? ", "! ", "; "])
def test_negation_in_separate_sentence_does_not_block_quote(separator: str) -> None:
    result = _align(f"No fever{separator}I have a cough.", "have a cough")
    assert result.ok
    assert result.aligned is not None
    assert result.aligned.aligned_text == "have a cough"


def test_first_occurrence_cannot_be_skipped_to_avoid_negation() -> None:
    result = _align(
        "No penicillin allergy. Penicillin allergy was discussed.", "penicillin allergy"
    )
    assert result.failure is EvidenceFailure.OMITTED_NEGATION_CONTEXT


def test_expanding_quote_preserves_negation_context() -> None:
    result = _align("No, not a penicillin allergy at all.", "not a penicillin allergy")
    assert result.ok
    assert result.aligned is not None
    assert result.aligned.aligned_text == "not a penicillin allergy"


def test_apostrophe_inside_word_is_significant() -> None:
    assert not _align("We'll discuss it.", "well discuss it").ok


def test_quotation_marks_are_not_part_of_word_offsets() -> None:
    result = _align("She said ‘pain’.", "pain")
    assert result.ok
    assert result.aligned is not None
    assert (result.aligned.char_start, result.aligned.char_end) == (10, 14)
    assert result.aligned.aligned_text == "pain"


def test_cross_segment_quote_requires_separate_evidence_items() -> None:
    transcript = _transcript(
        _segment("I have a cough", segment_id="s1"),
        _segment("for three days.", segment_id="s2"),
    )
    spanning = align_assertion_evidence(_assertion([("s1", "cough for three days")]), transcript)
    assert spanning.lane is EvidenceLane.FAIL
    assert spanning.failures == (EvidenceFailure.QUOTE_NOT_FOUND,)
    separate = align_assertion_evidence(
        _assertion([("s1", "cough"), ("s2", "for three days")]), transcript
    )
    assert separate.lane is EvidenceLane.PASS
    assert [item.segment_id for item in separate.items] == ["s1", "s2"]


def test_audio_offsets_follow_occurrence_in_segment_not_word_search() -> None:
    words = (
        WordTiming(word="pain", start_seconds=10.0, end_seconds=11.0),
        WordTiming(word="then", start_seconds=12.0, end_seconds=13.0),
        WordTiming(word="pain", start_seconds=14.0, end_seconds=15.0),
        WordTiming(word="again", start_seconds=16.0, end_seconds=17.0),
    )
    result = _align("Pain, then pain again.", "pain again", words=words)
    assert result.aligned is not None
    assert (result.aligned.char_start, result.aligned.char_end) == (11, 21)
    assert (result.aligned.audio_start_seconds, result.aligned.audio_end_seconds) == (14.0, 17.0)


def test_one_timed_word_can_cover_multiple_normalised_tokens() -> None:
    words = (WordTiming(word="follow-up", start_seconds=11.0, end_seconds=12.0),)
    result = _align("follow-up", "follow up", words=words)
    assert result.aligned is not None
    assert result.aligned.audio_source == "word_timings"
    assert (result.aligned.audio_start_seconds, result.aligned.audio_end_seconds) == (11.0, 12.0)
