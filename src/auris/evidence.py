"""Evidence alignment (PLAN A5): the evidence lane.

Every assertion must quote the transcript. For each evidence item
(``segment_id`` + ``quote``), this module finds the quote in the
named segment by comparing case- and punctuation-normalised token
sequences, and resolves exact character offsets in the segment's
original text. Audio offsets come from word timings when the ASR
provided them; otherwise the segment bounds are used and marked
approximate.

Normalisation applies **only** to the comparison, never to the stored
text: tokens are compared after Unicode NFC normalisation and
casefolding, with curly/straight in-token apostrophes equivalent and
punctuation between tokens ignored. Numeric signs and internal numeric
punctuation stay significant (1.5 cannot match 1 or 1 5). Exact-v1 is tried
first. Only when that sequence is absent, disfluency-v2 removes a versioned
closed filler list and collapses immediate exact repetitions of 1–3 tokens
on both sides. No other edits or fuzzy scores are allowed. Assent, denial,
negation and content tokens remain significant. Stored spans retain the
original text, including fillers/repeats between the matched endpoints.

An exact substring can still omit a negation cue. Conservatively refuse
a quote containing no negation cue when its surrounding sentence contains
one. Sentence boundaries here are '.', '?', '!', and ';' outside tokens.
This is a lexical guard, not a clinical negation/scope detector: ambiguous
cases require a wider quote. It does not establish that an assertion's
clinical fields follow from the quote. The first exact occurrence is used;
we do not skip an unsafe occurrence to find a more convenient one later.

Quotes are aligned within a single segment only. A5 defines a span as
one segment id plus a quote, so support that spans speaker turns must
be given as one evidence item per segment, not as one quote straddling
segments.

An assertion whose quotes all align is labelled "transcript-supported"
(A5) — never "verified", because speech-to-text can itself have
written words nobody said (PLAN section 4, principle 4).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from auris.assertions import AssertionBase, EvidenceItem
from auris.transcript import Transcript, TranscriptSegment

TRANSCRIPT_SUPPORTED = "transcript-supported"
EvidenceVersion = Literal["evidence-v1", "evidence-v2"]
EVIDENCE_CHECKER_VERSION: EvidenceVersion = "evidence-v2"
DISFLUENCY_FILLERS_VERSION = "fillers-v1"
# Closed list: assent, denial and negation can change clinical meaning. In
# particular mm-hmm, mhm, uh-huh, yeah, no and nope must survive comparison.
DISFLUENCY_FILLERS = frozenset({"um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah"})

_APOSTROPHES = frozenset("'’‘ʼ")
_NEGATION_CUES = frozenset(
    {
        "no",
        "not",
        "never",
        "without",
        "deny",
        "denies",
        "denied",
        "neither",
        "nor",
        "none",
        "nil",
        "negative",
        "nothing",
        "absent",
    }
)
_SENTENCE_BOUNDARIES = frozenset(".?!;")


class EvidenceLane(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class EvidenceFailure(StrEnum):
    CITED_EMPTY_SEGMENT = "cited_empty_segment"
    CITATION_OUTSIDE_WINDOW = "citation_outside_window"
    SEGMENT_NOT_FOUND = "segment_not_found"
    QUOTE_HAS_NO_TOKENS = "quote_has_no_tokens"
    QUOTE_NOT_FOUND = "quote_not_found"
    OMITTED_NEGATION_CONTEXT = "omitted_negation_context"


class _EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AlignedEvidence(_EvidenceModel):
    """A quote located in its segment, with exact offsets."""

    segment_id: str
    quote: str
    aligned_text: str
    char_start: int
    char_end: int
    audio_start_seconds: float | None
    audio_end_seconds: float | None
    audio_approximate: bool | None
    audio_source: Literal["word_timings", "segment_bounds", "human_utterance_bounds", "untimed"]
    alignment_method: Literal["exact-v1", "disfluency-v2"] = "exact-v1"
    turn_number: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _timing(self) -> AlignedEvidence:
        if self.audio_source == "untimed":
            if self.turn_number is None or any(
                value is not None
                for value in (
                    self.audio_start_seconds,
                    self.audio_end_seconds,
                    self.audio_approximate,
                )
            ):
                raise ValueError("untimed evidence requires a turn number and no audio extent")
        elif any(
            value is None
            for value in (
                self.audio_start_seconds,
                self.audio_end_seconds,
                self.audio_approximate,
            )
        ):
            raise ValueError("timed evidence requires audio bounds and approximation status")
        return self

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> dict:
        data = handler(self)
        if self.turn_number is None:
            data.pop("turn_number", None)  # Preserve existing timed evidence/golden artifacts.
        return data


class EvidenceItemResult(_EvidenceModel):
    """Alignment outcome for one evidence item: offsets, or the reason."""

    segment_id: str
    quote: str
    aligned: AlignedEvidence | None
    failure: EvidenceFailure | None

    @property
    def ok(self) -> bool:
        return self.aligned is not None


class AssertionEvidenceResult(_EvidenceModel):
    """The evidence-lane outcome for one assertion."""

    lane: EvidenceLane
    label: str | None
    items: tuple[EvidenceItemResult, ...]
    checker_version: EvidenceVersion = EVIDENCE_CHECKER_VERSION

    @property
    def failures(self) -> tuple[EvidenceFailure, ...]:
        return tuple(item.failure for item in self.items if item.failure is not None)


@dataclass(frozen=True)
class _Token:
    """A token in original text: normalised form plus char span."""

    normalised: str
    char_start: int
    char_end: int
    audio_start_seconds: float | None = None
    audio_end_seconds: float | None = None
    audio_approximate: bool = True


def _is_word_char(char: str) -> bool:
    return char.isalnum() or unicodedata.category(char).startswith("M")


def _is_token_char(text: str, index: int) -> bool:
    char = text[index]
    if _is_word_char(char):
        return True
    before = text[index - 1] if index else " "
    after = text[index + 1] if index + 1 < len(text) else " "
    if char in _APOSTROPHES:
        return _is_word_char(before) and _is_word_char(after)
    if char in ".,/:" and before.isdigit() and after.isdigit():
        return True
    if char == "." and after.isdigit() and (not _is_word_char(before)):
        return True
    return (
        char in "+-−"
        and not _is_word_char(before)
        and (after.isdigit() or (after == "." and text[index + 2 : index + 3].isdigit()))
    )


def _normalise_token(raw: str) -> str:
    punctuation = "".join("'" if char in _APOSTROPHES else char for char in raw)
    return unicodedata.normalize("NFC", punctuation.casefold())


def _tokenise(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    start: int | None = None
    for index in range(len(text)):
        if _is_token_char(text, index):
            if start is None:
                start = index
        elif start is not None:
            tokens.append(_Token(_normalise_token(text[start:index]), start, index))
            start = None
    if start is not None:
        tokens.append(_Token(_normalise_token(text[start:]), start, len(text)))
    return [token for token in tokens if any(c.isalnum() for c in token.normalised)]


def _has_negation(tokens: list[_Token]) -> bool:
    return any(
        token.normalised in _NEGATION_CUES or token.normalised.endswith("n't") for token in tokens
    ) or any(
        first.normalised == "ruled" and second.normalised == "out"
        for first, second in zip(tokens, tokens[1:], strict=False)
    )


def normalised_tokens(text: str) -> tuple[str, ...]:
    """Shared lexical policy for alignment, consistency and chunk merging."""
    return tuple(token.normalised for token in _tokenise(text))


def has_negation(text: str) -> bool:
    """Shared conservative cue detector; not a clinical scope classifier."""
    return _has_negation(_tokenise(text))


def _has_sentence_boundary(text: str, left: _Token, right: _Token) -> bool:
    return any(char in _SENTENCE_BOUNDARIES for char in text[left.char_end : right.char_start])


def sentence_texts(text: str) -> tuple[str, ...]:
    """Sentence token spans using the alignment guard's boundaries.

    Preserve original wording and internal punctuation, including decimals;
    omit punctuation outside the first and last token of each sentence.
    """
    tokens = _tokenise(text)
    if not tokens:
        return ()
    starts = [0]
    starts.extend(
        index
        for index in range(1, len(tokens))
        if _has_sentence_boundary(text, tokens[index - 1], tokens[index])
    )
    stops = [*starts[1:], len(tokens)]
    return tuple(
        text[tokens[start].char_start : tokens[stop - 1].char_end]
        for start, stop in zip(starts, stops, strict=True)
    )


def _omits_negation_context(text: str, tokens: list[_Token], start: int, stop: int) -> bool:
    if _has_negation(tokens[start:stop]):
        return False
    left, right = start, stop
    while left > 0:
        if _has_sentence_boundary(text, tokens[left - 1], tokens[left]):
            break
        left -= 1
    while right < len(tokens):
        if _has_sentence_boundary(text, tokens[right - 1], tokens[right]):
            break
        right += 1
    return _has_negation(tokens[left:right])


def _find_sequence(haystack: list[str], needle: list[str]) -> int | None:
    """Index of the first exact occurrence of ``needle``, or None."""
    if not needle or len(needle) > len(haystack):
        return None
    for start in range(len(haystack) - len(needle) + 1):
        if haystack[start : start + len(needle)] == needle:
            return start
    return None


def _disfluency_tokens(tokens: list[_Token]) -> list[_Token]:
    """Remove closed-list fillers, then collapse adjacent equal 1–3-grams.

    Scan left to right, shortest repeat first, restarting after each collapse
    until stable. Each retained token covers *all* original copies, including
    the last copy's character/audio end. Intervening fillers remain in that
    original span. The exact-v1 tokenizer splits hyphens: protect the assent
    pair ``uh huh`` (also spelled ``uh-huh``) rather than dropping its ``uh``.
    """
    kept = [
        token
        for i, token in enumerate(tokens)
        if token.normalised not in DISFLUENCY_FILLERS
        or (token.normalised == "uh" and i + 1 < len(tokens) and tokens[i + 1].normalised == "huh")
    ]
    start = 0
    while start < len(kept):
        for width in range(1, 4):
            first, second = kept[start : start + width], kept[start + width : start + 2 * width]
            if len(second) != width or [t.normalised for t in first] != [
                t.normalised for t in second
            ]:
                continue
            kept[start : start + 2 * width] = [
                replace(
                    left,
                    char_end=right.char_end,
                    audio_end_seconds=right.audio_end_seconds,
                    audio_approximate=left.audio_approximate or right.audio_approximate,
                )
                for left, right in zip(first, second, strict=True)
            ]
            start = 0
            break
        else:
            start += 1
    return kept


def _disfluency_span(tokens: list[_Token], quote: list[_Token]) -> tuple[int, int] | None:
    normalised = _disfluency_tokens(tokens)
    needle = _disfluency_tokens(quote)
    start = _find_sequence([t.normalised for t in normalised], [t.normalised for t in needle])
    if start is None:
        return None
    first, last = normalised[start], normalised[start + len(needle) - 1]
    # Recover the original token range for timing and the existing context guard.
    return (
        next(i for i, token in enumerate(tokens) if token.char_start == first.char_start),
        next(i + 1 for i, token in enumerate(tokens) if token.char_end == last.char_end),
    )


def _segment_tokens(segment: TranscriptSegment) -> list[_Token]:
    """Segment tokens, with per-token audio times when word timings
    line up exactly with the segment text (same normalised tokens).
    If the ASR word list disagrees with the text, tokens carry no
    audio times and callers fall back to the segment bounds."""
    tokens = _tokenise(segment.text)
    if segment.words is None:
        return tokens
    word_tokens: list[tuple[str, float, float, bool]] = []
    for word in segment.words:
        for token in _tokenise(word.word):
            word_tokens.append(
                (token.normalised, word.start_seconds, word.end_seconds, word.approximate)
            )
    if [entry[0] for entry in word_tokens] != [token.normalised for token in tokens]:
        return tokens
    return [
        _Token(
            normalised=token.normalised,
            char_start=token.char_start,
            char_end=token.char_end,
            audio_start_seconds=entry[1],
            audio_end_seconds=entry[2],
            audio_approximate=entry[3],
        )
        for token, entry in zip(tokens, word_tokens, strict=True)
    ]


def align_evidence_item(
    item: EvidenceItem,
    transcript: Transcript,
    *,
    allowed_segment_ids: frozenset[str] | None = None,
    checker_version: EvidenceVersion = EVIDENCE_CHECKER_VERSION,
) -> EvidenceItemResult:
    """Align one evidence quote to its segment. Misalignment is
    returned as data (a FAIL reason), never raised."""
    segment = transcript.segment_by_id(item.segment_id)
    if checker_version == "evidence-v2" and segment is not None and not _tokenise(segment.text):
        return EvidenceItemResult(
            segment_id=item.segment_id,
            quote=item.quote,
            aligned=None,
            failure=EvidenceFailure.CITED_EMPTY_SEGMENT,
        )
    if allowed_segment_ids is not None and item.segment_id not in allowed_segment_ids:
        return EvidenceItemResult(
            segment_id=item.segment_id,
            quote=item.quote,
            aligned=None,
            failure=EvidenceFailure.CITATION_OUTSIDE_WINDOW,
        )
    if segment is None:
        return EvidenceItemResult(
            segment_id=item.segment_id,
            quote=item.quote,
            aligned=None,
            failure=EvidenceFailure.SEGMENT_NOT_FOUND,
        )
    quote_tokens = _tokenise(item.quote)
    if not quote_tokens:
        return EvidenceItemResult(
            segment_id=item.segment_id,
            quote=item.quote,
            aligned=None,
            failure=EvidenceFailure.QUOTE_HAS_NO_TOKENS,
        )
    tokens = _segment_tokens(segment)
    start = _find_sequence(
        [token.normalised for token in tokens],
        [token.normalised for token in quote_tokens],
    )
    span = (start, start + len(quote_tokens)) if start is not None else None
    method: Literal["exact-v1", "disfluency-v2"] = "exact-v1"
    if span is None and checker_version == "evidence-v2":
        span = _disfluency_span(tokens, quote_tokens)
        method = "disfluency-v2"
    if span is None:
        return EvidenceItemResult(
            segment_id=item.segment_id,
            quote=item.quote,
            aligned=None,
            failure=EvidenceFailure.QUOTE_NOT_FOUND,
        )
    start, stop = span
    # A collapsed multi-token repeat can have overlapping original ranges:
    # e.g. the canonical "pain" in "no pain no pain" spans "pain no pain".
    # Its convex original span must not introduce a cue absent from the quote.
    if _omits_negation_context(segment.text, tokens, start, stop) or (
        method == "disfluency-v2"
        and _has_negation(tokens[start:stop])
        and not _has_negation(quote_tokens)
    ):
        return EvidenceItemResult(
            segment_id=item.segment_id,
            quote=item.quote,
            aligned=None,
            failure=EvidenceFailure.OMITTED_NEGATION_CONTEXT,
        )
    matched = tokens[start:stop]
    char_start = matched[0].char_start
    char_end = matched[-1].char_end
    if all(token.audio_start_seconds is not None for token in matched):
        audio_start = matched[0].audio_start_seconds
        audio_end = matched[-1].audio_end_seconds
        assert audio_start is not None and audio_end is not None
        audio_approximate = any(token.audio_approximate for token in matched)
        audio_source = "word_timings"
    else:
        audio_start = segment.start_seconds
        audio_end = segment.end_seconds
        audio_approximate = True
        audio_source = "segment_bounds"
        if transcript.timing_source == "human_utterance_bounds":
            audio_source = "human_utterance_bounds"
        elif transcript.timing_mode == "untimed":
            audio_source = "untimed"
            audio_approximate = None
    return EvidenceItemResult(
        segment_id=item.segment_id,
        quote=item.quote,
        aligned=AlignedEvidence(
            segment_id=segment.id,
            quote=item.quote,
            aligned_text=segment.text[char_start:char_end],
            char_start=char_start,
            char_end=char_end,
            audio_start_seconds=audio_start,
            audio_end_seconds=audio_end,
            audio_approximate=audio_approximate,
            audio_source=audio_source,
            alignment_method=method,
            turn_number=segment.turn_number,
        ),
        failure=None,
    )


def align_assertion_evidence(
    assertion: AssertionBase,
    transcript: Transcript,
    *,
    allowed_segment_ids: frozenset[str] | None = None,
    checker_version: EvidenceVersion = EVIDENCE_CHECKER_VERSION,
) -> AssertionEvidenceResult:
    """The evidence lane for one assertion: PASS only when every
    quote aligns; PASS carries the "transcript-supported" label."""
    items = tuple(
        align_evidence_item(
            item,
            transcript,
            allowed_segment_ids=allowed_segment_ids,
            checker_version=checker_version,
        )
        for item in assertion.evidence
    )
    if all(item.ok for item in items):
        return AssertionEvidenceResult(
            lane=EvidenceLane.PASS,
            label=TRANSCRIPT_SUPPORTED,
            items=items,
            checker_version=checker_version,
        )
    return AssertionEvidenceResult(
        lane=EvidenceLane.FAIL, label=None, items=items, checker_version=checker_version
    )
