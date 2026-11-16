"""Evidence alignment: the evidence lane.

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
evidence-v3 narrows that scope to the cue's clause: a cue reaches both ways
up to a sentence boundary or a closed list of terminators ("but", "except",
...), and a first-token answer "No," has no scope (scope-terms-v1). It never
refuses a quote that v2 accepts.
evidence-v3 is PARKED (2026-11-14): its "yeah"/"yes" terminators and the
answer-"No," rule trust speech-to-text punctuation, and an independent review
found real denials it accepts (tests: KNOWN_HOLES). It is never the default.
evidence-v4 (negation-scope v2, 2026-11-16) changes one thing from v2: a
cue's forward scope stops at a contrast word ("but", "however", "except", ...;
not "yeah"/"yes"), so "no vomiting but I'm pretty sweaty" no longer refuses
"I'm pretty sweaty". A cue after the contrast word still reaches back to the
sentence start, as in v2: it can point back through a pronoun or a class word
("We could try amoxicillin, but I don't think you need an antibiotic"). A
leading "No," keeps its full scope. Each scope is a subset of v2's sentence,
so v4 never refuses what v2 accepts. The version as reviewed, with further
rules that were rejected, is commit 4e91461.
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
written words nobody said.
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
EvidenceVersion = Literal["evidence-v1", "evidence-v2", "evidence-v3", "evidence-v4"]
EVIDENCE_CHECKER_VERSION: EvidenceVersion = "evidence-v4"
# fillers-v2: numbers (digits, ½, number words) are never collapsed and an
# all-capitals token ("ER") is never a filler; the closed list is unchanged.
DISFLUENCY_FILLERS_VERSION = "fillers-v2"
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
# evidence-v3: words that end a cue's clause. Closed list; deliberately not
# "well", "actually", "yet", "who", "which", "because", "so", "and", "or" or
# commas, each of which can sit inside the phrase a denial covers.
SCOPE_TERMS_VERSION = "scope-terms-v1"
_SCOPE_TERMINATORS = frozenset(
    {
        "but",
        "however",
        "although",
        "though",
        "except",
        "apart",
        "aside",
        "whereas",
        "nevertheless",
        "yeah",
        "yes",
    }
)

# evidence-v4 (scope-terms-v2): v3's terminators without "yeah"/"yes".
SCOPE_TERMS_V4_VERSION = "scope-terms-v2"
_SCOPE_TERMINATORS_V4 = _SCOPE_TERMINATORS - {"yeah", "yes"}


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
    # Set only by an exploratory span check (evaluation tooling): the
    # pieces of one quote split across segments share it.
    span_group: str | None = None

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
        if self.span_group is None:
            data.pop("span_group", None)
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


def sentence_spans(text: str) -> tuple[tuple[int, int, str], ...]:
    """(start, end, closing punctuation) per sentence, on the guard's boundaries.

    Decimals stay inside their sentence ("38.5"). The closing punctuation is
    the text after the sentence's last token up to the next sentence, so a
    caller can see "?" that sentence_texts() omits.
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
    spans = []
    for start, stop in zip(starts, stops, strict=True):
        end = tokens[stop - 1].char_end
        following = tokens[stop].char_start if stop < len(tokens) else len(text)
        spans.append((tokens[start].char_start, end, text[end:following].strip()))
    return tuple(spans)


def sentence_texts(text: str) -> tuple[str, ...]:
    """Sentence token spans using the alignment guard's boundaries.

    Preserve original wording and internal punctuation, including decimals;
    omit punctuation outside the first and last token of each sentence.
    """
    return tuple(text[start:end] for start, end, _ in sentence_spans(text))


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


def _cue_spans(tokens: list[_Token]) -> list[tuple[int, int]]:
    """(first, last) token index of every negation cue, "ruled out" as one."""
    spans = [
        (index, index)
        for index, token in enumerate(tokens)
        if token.normalised in _NEGATION_CUES or token.normalised.endswith("n't")
    ]
    spans += [
        (index, index + 1)
        for index in range(len(tokens) - 1)
        if tokens[index].normalised == "ruled" and tokens[index + 1].normalised == "out"
    ]
    return spans


def _is_answer_no(text: str, tokens: list[_Token], index: int) -> bool:
    """A first non-filler "no" directly followed by ',' or a sentence boundary."""
    token = tokens[index]
    if token.normalised != "no":
        return False
    if any(t.normalised not in DISFLUENCY_FILLERS for t in tokens[:index]):
        return False
    following = text[token.char_end : token.char_end + 1]
    return following == "," or following in _SENTENCE_BOUNDARIES


def _ends_clause(text: str, tokens: list[_Token], left: int, right: int) -> bool:
    """Whether a clause ends between adjacent tokens ``left`` and ``right``."""
    return _has_sentence_boundary(text, tokens[left], tokens[right])


def _omits_negation_context_v3(text: str, tokens: list[_Token], start: int, stop: int) -> bool:
    if _has_negation(tokens[start:stop]):
        return False
    for first, last in _cue_spans(tokens):
        if first == last and _is_answer_no(text, tokens, first):
            continue
        if first < stop and last >= start:  # a cue split by the quote's edge
            return True
        # Forward: tokens after the cue up to a terminator or boundary.
        index = last + 1
        while index < len(tokens) and not _ends_clause(text, tokens, index - 1, index):
            if tokens[index].normalised in _SCOPE_TERMINATORS:
                break
            if start <= index < stop:
                return True
            index += 1
        # Backward: tokens before the cue down to a terminator or boundary.
        index = first - 1
        while index >= 0 and not _ends_clause(text, tokens, index, index + 1):
            if tokens[index].normalised in _SCOPE_TERMINATORS:
                break
            if start <= index < stop:
                return True
            index -= 1
    return False


def _omits_negation_context_v4(text: str, tokens: list[_Token], start: int, stop: int) -> bool:
    if _has_negation(tokens[start:stop]):
        return False
    for first, last in _cue_spans(tokens):
        if first < stop and last >= start:  # a cue split by the quote's edge
            return True
        # Forward: up to a sentence boundary or a contrast word.
        index = last + 1
        while index < len(tokens) and not _ends_clause(text, tokens, index - 1, index):
            if tokens[index].normalised in _SCOPE_TERMINATORS_V4:
                break
            if start <= index < stop:
                return True
            index += 1
        # Backward: to the sentence boundary, as in evidence-v2.
        index = first - 1
        while index >= 0 and not _ends_clause(text, tokens, index, index + 1):
            if start <= index < stop:
                return True
            index -= 1
    return False


def negation_covers(text: str, phrases: list[str]) -> bool | None:
    """evidence-v4 scope for the consistency check: whether a denial in ``text``
    applies to an occurrence of any phrase (a cue inside the phrase counts).
    None when no phrase occurs in the text; callers then fall back to any cue."""
    tokens = _tokenise(text)
    words = [token.normalised for token in tokens]
    found = False
    for phrase in phrases:
        needle = [token.normalised for token in _tokenise(phrase)]
        if not needle:
            continue
        for start in range(len(words) - len(needle) + 1):
            if words[start : start + len(needle)] != needle:
                continue
            found = True
            stop = start + len(needle)
            if _has_negation(tokens[start:stop]) or _omits_negation_context_v4(
                text, tokens, start, stop
            ):
                return True
    return False if found else None


def _find_sequence(haystack: list[str], needle: list[str]) -> int | None:
    """Index of the first exact occurrence of ``needle``, or None."""
    if not needle or len(needle) > len(haystack):
        return None
    for start in range(len(haystack) - len(needle) + 1):
        if haystack[start : start + len(needle)] == needle:
            return start
    return None


# Spoken numbers count as much as digits: "one one zero" and "ten ten" may be
# two numbers, so a repeat containing one is never collapsed.
NUMBER_WORDS = frozenset(
    "zero oh one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty "
    "seventy eighty ninety hundred thousand half quarter".split()
)


def _is_number(normalised: str) -> bool:
    return any(char.isnumeric() for char in normalised) or normalised in NUMBER_WORDS


def _is_filler(tokens: list[_Token], index: int, text: str) -> bool:
    token = tokens[index]
    if token.normalised not in DISFLUENCY_FILLERS:
        return False
    raw = text[token.char_start : token.char_end]
    if raw.isupper():
        return False  # "ER" (emergency room) is a word, not the filler "er".
    if raw[0].isupper() and text[: token.char_start].rstrip()[-1:] not in ("", ".", "?", "!", ","):
        return False  # Capitalised mid-sentence ("the Er department") is a name, not "er".
    # The exact-v1 tokenizer splits hyphens: keep the assent pair "uh huh".
    return not (
        token.normalised == "uh"
        and index + 1 < len(tokens)
        and tokens[index + 1].normalised == "huh"
    )


def _disfluency_tokens(tokens: list[_Token], text: str) -> list[_Token]:
    """Remove closed-list fillers, then collapse adjacent equal 1–3-grams.

    Scan left to right, shortest repeat first, restarting after each collapse
    until stable. Each retained token covers *all* original copies, including
    the last copy's character/audio end. Intervening fillers remain in that
    original span. A repeat containing a digit is never collapsed: "2 2 mg"
    may be a stutter or two spoken numbers, and numbers always count.
    """
    kept = [token for i, token in enumerate(tokens) if not _is_filler(tokens, i, text)]
    start = 0
    while start < len(kept):
        for width in range(1, 4):
            first, second = kept[start : start + width], kept[start + width : start + 2 * width]
            if len(second) != width or [t.normalised for t in first] != [
                t.normalised for t in second
            ]:
                continue
            if any(_is_number(t.normalised) for t in first):
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


def _disfluency_span(
    tokens: list[_Token], text: str, quote: list[_Token], quote_text: str
) -> tuple[int, int] | None:
    normalised = _disfluency_tokens(tokens, text)
    needle = _disfluency_tokens(quote, quote_text)
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
    if checker_version != "evidence-v1" and segment is not None and not _tokenise(segment.text):
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
    if span is None and checker_version != "evidence-v1":
        span = _disfluency_span(tokens, segment.text, quote_tokens, item.quote)
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
    omits = {
        "evidence-v3": _omits_negation_context_v3,
        "evidence-v4": _omits_negation_context_v4,
    }.get(checker_version, _omits_negation_context)
    if omits(segment.text, tokens, start, stop) or (
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
