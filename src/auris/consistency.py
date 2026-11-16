"""Deterministic lane-2 checks after quote alignment.

Numbers and allergen substances require exact support. Concept normalisation
outside explicit terminology aliases requires review, never a lane FAIL.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, SerializerFunctionWrapHandler, model_serializer

from auris.assertions import AssertionBase, Experiencer, Polarity
from auris.evidence import (
    DISFLUENCY_FILLERS,
    AssertionEvidenceResult,
    has_negation,
    negation_covers,
    normalised_tokens,
    sentence_spans,
    sentence_texts,
)
from auris.reason_codes import CheckReason
from auris.transcript import SpeakerRole, Transcript

FAMILY_CUES = frozenset(
    "mother father sister brother son daughter wife husband partner grandmother grandfather "
    "aunt uncle mom mum dad grandma grandpa sibling parent cousin niece nephew spouse "
    "stepmother stepfather grandson granddaughter".split()
)
_NEGATED_KINDS = frozenset({"allergy_denial", "clinician_ruled_out_problem"})
_CLINICIAN_KINDS = frozenset(
    {"clinician_assessed_problem", "clinician_ruled_out_problem", "explicit_new_order", "plan_item"}
)
_NUMBER = re.compile(r"[+−-]?(?:\d+(?:[.,]\d+)*|\.\d+)")
_VALUE_FIELDS = ("value_text", "unit_text", "dose_text")
_NULLABLE_FIELDS = (
    *_VALUE_FIELDS,
    "route_text",
    "frequency_text",
    "stated_time_text",
    "reaction_manifestation_text",
    "stated_resolution_text",
)
# Placeholder words a model writes instead of leaving a field empty.
_LITERAL_NULLS = frozenset(
    {
        "null",
        "none",
        "n/a",
        "unknown",
        "not stated",
        "not mentioned",
        "not specified",
        "not given",
        "not provided",
        "unspecified",
        "not applicable",
        "none stated",
    }
)
_COMMENTARY = re.compile(
    r"[()]|\b(?:implied|not\s+explicitly\s+stated|presumably|likely|context)\b", re.IGNORECASE
)
Synonyms = Callable[[str, str], Iterable[str]]


class ConsistencyResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    failures: tuple[str, ...] = ()
    review_reasons: tuple[str, ...] = ()
    informational_flags: tuple[str, ...] = ()
    normalised_null_fields: tuple[str, ...] = ()
    unusable_fields: tuple[str, ...] = ()

    @model_serializer(mode="wrap")
    def serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        for field in ("informational_flags", "normalised_null_fields", "unusable_fields"):
            if not getattr(self, field):
                data.pop(field)
        return data

    @property
    def force_material(self) -> bool:
        return bool(self.failures or self.review_reasons)

    def usable_assertion(self, assertion: AssertionBase) -> AssertionBase:
        """Keep the raw extraction intact; only the mapper sees cleaned fields."""
        return assertion.model_copy(
            update={field: None for field in (*self.normalised_null_fields, *self.unusable_fields)}
        )


def _field_hygiene(assertion: AssertionBase) -> ConsistencyResult:
    nulls, unusable = [], []
    for field in _NULLABLE_FIELDS:
        text = getattr(assertion, field, None)
        if text is None:
            continue
        if text.strip().casefold() in _LITERAL_NULLS:
            nulls.append(field)
        elif field in _VALUE_FIELDS and _COMMENTARY.search(text):
            unusable.append(field)
    return ConsistencyResult(
        informational_flags=(CheckReason.LITERAL_NULL_NORMALISED,) if nulls else (),
        normalised_null_fields=tuple(nulls),
        review_reasons=(CheckReason.FIELD_CONTAINS_COMMENTARY,) if unusable else (),
        unusable_fields=tuple(unusable),
    )


def numeric_tokens(text: str) -> frozenset[str]:
    # Tokenizer preserves decimal punctuation and signs. Slash separates BP
    # components; numbers embedded in a unit (500mg) keep their numeric part.
    # A digit embedded in a word (SpO2) is not a spoken numeric value.
    return frozenset(
        match.group()
        for token in normalised_tokens(text)
        for part in re.split(r"[/ :]", token)
        if (match := _NUMBER.match(part)) is not None
    )


def contains_phrase(text: str, phrase: str) -> bool:
    haystack, needle = normalised_tokens(text), normalised_tokens(phrase)
    return bool(needle) and any(
        haystack[start : start + len(needle)] == needle
        for start in range(len(haystack) - len(needle) + 1)
    )


# Clock times ("9:15", "9am", "9.30pm") and ordinals ("12th") are when, not
# how much; a dotted time counts as a clock only after "at", "by", "from"...
_CLOCK_OR_ORDINAL = re.compile(
    r"\d{1,2}:\d{2}(?:am|pm)?|\d{1,2}(?:[.]\d{2})?(?:am|pm)|\d+(?:st|nd|rd|th)", re.IGNORECASE
)
_TIME_PREPOSITIONS = frozenset({"at", "by", "from", "until", "till", "around", "about"})


def _ordered_numbers(tokens: tuple[str, ...]) -> list[tuple[int, str]]:
    numbers = []
    for index, token in enumerate(tokens):
        if _CLOCK_OR_ORDINAL.fullmatch(token):
            continue
        if (
            re.fullmatch(r"\d{1,2}\.\d{2}", token)
            and index
            and (tokens[index - 1] in _TIME_PREPOSITIONS)
        ):
            continue  # "at 9.15" is a time, "37.15" alone is a value.
        for part in re.split(r"[/ :]", token):
            if (match := _NUMBER.match(part)) is not None:
                numbers.append((index, match.group()))
    return numbers


# Measurement names by measurement, so "pulse rate" is the pulse but "pulse
# ox" is oxygen saturation, and "your pressure is 128 over 82" never lends 128
# to the pulse. Another group's name ends this measurement's number window.
MEASUREMENT_GROUPS: dict[str, tuple[str, ...]] = {
    "blood pressure": ("blood pressure", "bp", "pressure", "systolic", "diastolic"),
    "heart rate": ("heart rate", "pulse", "pulse rate", "hr", "heart"),
    "respiratory rate": (
        "respiratory rate",
        "respiration rate",
        "respirations",
        "resp rate",
        "resps",
        "rr",
        "breathing rate",
    ),
    "temperature": ("temperature", "temp", "body temperature"),
    "weight": ("weight", "body weight"),
    "height": ("height", "body height"),
    "bmi": ("bmi",),
    "oxygen saturation": (
        "spo2",
        "pulse oximetry",
        "pulse ox",
        "pulse oximeter",
        "oxygen saturation",
        "oxygen saturations",
        "oxygen sats",
        "o2 sats",
        "o2 sat",
        "o2",
        "sats",
        "sat",
        "saturation",
        "saturations",
        "oxygen",
    ),
    "glucose": ("blood sugar", "glucose", "sugar", "sugars", "bms", "bm"),
    "peak flow": ("peak flow",),
}
MEASUREMENT_NAMES = frozenset(name for names in MEASUREMENT_GROUPS.values() for name in names)
# Words that lead a number into the measurement named next: "72 for the pulse".
_CONNECTORS = frozenset({"for", "the", "your", "his", "her", "their", "of", "is", "was", "as"})


def measurement_stops(aliases: Iterable[str]) -> frozenset[str]:
    """Names of every other measurement, for a vital named by these aliases."""
    own = {tuple(normalised_tokens(alias)) for alias in aliases}
    groups = [
        names
        for names in MEASUREMENT_GROUPS.values()
        if any(tuple(normalised_tokens(name)) in own for name in names)
    ]
    mine = {tuple(normalised_tokens(name)) for names in groups for name in names} | own
    return frozenset(n for n in MEASUREMENT_NAMES if tuple(normalised_tokens(n)) not in mine)


def _mention_tokens(tokens: tuple[str, ...]) -> tuple[str, ...]:
    # "blood pressure's 140", "temp’s 38": the contraction still names it.
    return tuple(re.sub(r"['’]s$", "", token) for token in tokens)


def _mentions(tokens: tuple[str, ...], aliases: Iterable[str]) -> list[tuple[int, int]]:
    spans = []
    for alias in aliases:
        needle = normalised_tokens(alias)
        spans.extend(
            (start, start + len(needle))
            for start in range(len(tokens) - len(needle) + 1)
            if needle and tokens[start : start + len(needle)] == needle
        )
    return spans


def number_attached(
    text: str, aliases: Iterable[str], value: str, stop_aliases: Iterable[str] = ()
) -> bool | None:
    """Do the value's numbers belong to this concept in this text?

    True when, in one sentence, the numbers right after a mention of the
    concept start with the value's numbers (up to the next measurement named,
    and not numbers that lead into that next name: "72 for the pulse"); or,
    when nothing follows the mention and no other measurement is named before
    it in the sentence, the numbers right before it end with them ("128 over
    82 is your blood pressure"); or the value's numbers are the only numbers
    and no other measurement is named. None: the value is not here, or the
    concept is never named. False: the value sits with another measurement.
    """
    wanted = [number for _, number in _ordered_numbers(normalised_tokens(value))]
    if not wanted:
        return True
    alias_list = list(aliases)
    stops = [a for a in stop_aliases if a not in set(alias_list)]
    all_numbers = [n for _, n in _ordered_numbers(normalised_tokens(text))]
    if not set(wanted) <= set(all_numbers):
        return None
    if set(all_numbers) <= set(wanted) and not _mentions(
        _mention_tokens(normalised_tokens(text)), stops
    ):
        return True
    named = False
    for sentence in sentence_texts(text):
        raw = normalised_tokens(sentence)
        tokens = _mention_tokens(raw)
        numbers = _ordered_numbers(raw)
        others = _mentions(tokens, stops)
        own = [
            (start, stop)
            for start, stop in _mentions(tokens, alias_list)
            # "pulse" inside "pulse ox" names oxygen saturation, not the pulse.
            if not any(s <= start and stop <= e and e - s > stop - start for s, e in others)
        ]
        boundaries = sorted({*others, *own})
        for start, stop in own:
            named = True
            following = min((s for s, _ in boundaries if s >= stop), default=len(tokens))
            after = [(i, n) for i, n in numbers if stop <= i < following]
            if after and following < len(tokens):
                # "98 percent for the oxygen saturation, and 72 for the pulse":
                # a number that leads into the next name, which has no number of
                # its own after it, belongs to that next measurement.
                nxt_end = min(e for s, e in boundaries if s == following)
                beyond = min((s for s, _ in boundaries if s >= nxt_end), default=len(tokens))
                has_own = any(nxt_end <= i < beyond for i, _ in numbers)
                last = after[-1][0]
                if not has_own and all(t in _CONNECTORS for t in tokens[last + 1 : following]):
                    after = after[:-1]
            if after:
                nearest = [n for _, n in after[: len(wanted)]]
            elif not any(e <= start for _, e in others):
                nearest = [n for i, n in numbers if i < start][-len(wanted) :]
            else:
                # Another measurement is named earlier: only "N for the <this>" binds.
                gap = [
                    n
                    for i, n in numbers
                    if i < start
                    and tokens[i + 1 : start]  # punctuation is gone: "37.2, pulse" is no link
                    and all(t in _CONNECTORS for t in tokens[i + 1 : start])
                ]
                nearest = gap[-len(wanted) :]
            if nearest == wanted:
                return True
    return False if named else None


_INTERJECTIONS = frozenset({"hmm", "mm", "mmm", "oh", "well", "ah", "uh", "um", "er", "erm"})
_DENIAL_OPENERS = frozenset({"no", "nope", "nah", "never", "none", "not", "neither", "nothing"})
_NOT_A_DENIAL = frozenset(
    {"sure", "idea", "know", "remember", "recall", "understand", "catch", "hear", "certain"}
)
# "I don't", "I haven't", "I'm not", "I do not" deny; "I hope not", "I don't know" do not.
_FIRST_PERSON = frozenset({"i", "i've", "i'm"})
_DENYING_WORDS = frozenset(
    {"don't", "dont", "haven't", "havent", "didn't", "didnt", "not", "never", "wasn't", "can't"}
)
_AFFIRMATIONS = frozenset(
    {"yes", "yeah", "yep", "yup", "correct", "exactly", "definitely", "absolutely"}
)
_LEAD_IN = r"(?:(?:and|so|but|or|ok|okay|right|well|now|then|um|uh|er|erm|ah)\W+)*"
_QUESTION_OPENER = re.compile(
    rf"^\W*{_LEAD_IN}(?:(?:do|does|did|have|has|had|are|is|was|were|can|could|would|will"
    r"|shall)\W+(?:you|your|it|there|this|that|the|any|anything|anyone|he|she|they)\b"
    r"|any\b)",
    re.IGNORECASE,
)
# Patients drop subjects ("had it since Monday", "is the worst at night"), so
# their opener must be a modal plus a pronoun: "Could it be...", "Do I need...".
_PATIENT_QUESTION_OPENER = re.compile(
    rf"^\W*{_LEAD_IN}(?:could|would|can|should|will|is|are|do|does|did)\W+"
    r"(?:it|this|that|i|my|you)\b",
    re.IGNORECASE,
)
_TITLES = frozenset({"dr", "mr", "mrs", "ms", "prof", "st", "nurse"})
_DISCOURSE = frozenset(
    "right ok okay so well alright all fine good great lovely perfect brilliant cheers thanks "
    "thank you yes yeah yep no nope sure um uh er erm mm hmm ah oh then now".split()
)


def _sentences(text: str) -> list[tuple[int, int, str]]:
    """sentence_spans, re-joined after titles: "Did Dr. Patel say...?" is one."""
    merged: list[tuple[int, int, str]] = []
    for start, end, closing in sentence_spans(text):
        last = normalised_tokens(text[merged[-1][0] : merged[-1][1]])[-1:] if merged else ()
        if merged and merged[-1][2] == "." and last and last[0] in _TITLES:
            merged[-1] = (merged[-1][0], end, closing)
        else:
            merged.append((start, end, closing))
    return merged


# Unpunctuated clinician speech can carry a question mid-sentence: "so I see
# you have an inhaler do you get heartburn".
_EMBEDDED_QUESTION = re.compile(
    r"\b(?:do|does|did|are|is|was|were|have|has|can|could|would|will)\s+you\b", re.IGNORECASE
)


def _sentence_is_question(
    sentence: str, closing: str, segment_text: str, *, clinician: bool
) -> bool:
    """Ends in "?", or opens like a question ("and is your pulse still 110.").

    Patients need the stricter opener because they often drop the subject of a
    statement ("had it since Monday"). Without any punctuation in the segment,
    a clinician sentence containing "do you"/"have you"/... counts as asking.
    """
    if "?" in closing:
        return True
    opener = _QUESTION_OPENER if clinician else _PATIENT_QUESTION_OPENER
    if opener.match(sentence):
        return True
    unpunctuated = not any(mark in segment_text for mark in ".?!")
    return clinician and unpunctuated and bool(_EMBEDDED_QUESTION.search(sentence))


def _conversation(transcript: Transcript) -> list:
    """Segments in spoken order: by start time when every segment has one."""
    segments = list(transcript.segments)
    if all(s.start_seconds is not None for s in segments):
        return sorted(segments, key=lambda s: s.start_seconds)
    return segments


def _opening_words(text: str) -> list[str]:
    sentences = sentence_spans(text)
    for index, (start, end, closing) in enumerate(sentences[:2]):
        words = [t for t in normalised_tokens(text[start:end]) if t not in _INTERJECTIONS]
        # "Asthma? No." echoes the question before answering it.
        if words and not ("?" in closing and index == 0 and len(sentences) > 1):
            return words
    return []


def _is_denial(words: list[str]) -> bool:
    if not words:
        return False
    if words[0] in _DENIAL_OPENERS:
        return not (len(words) > 1 and words[1] in _NOT_A_DENIAL)
    if words[0] in _FIRST_PERSON and len(words) > 1:
        rest = words[1:3]
        denies = rest[0] in _DENYING_WORDS or rest[:2] in (["do", "not"], ["have", "not"])
        return denies and not set(words[1:4]) & _NOT_A_DENIAL
    return False


def _reply_to(transcript: Transcript, segment_id: str):
    ordered = _conversation(transcript)
    index = next((i for i, s in enumerate(ordered) if s.id == segment_id), None)
    if index is None:
        return None
    question = ordered[index]
    later = [s for s in ordered[index + 1 :] if s.role != question.role and s.text.strip()]
    if question.end_seconds is not None:
        # A backchannel ("Uh, no.") that starts before the question finishes is
        # not its answer; prefer the first turn starting after it ends.
        answers = [
            s
            for s in later
            if s.start_seconds is not None and s.start_seconds >= question.end_seconds - 0.3
        ]
        if answers:
            return answers[0]
    return later[0] if later else None


def _answer_denies(transcript: Transcript, segment_id: str) -> bool:
    reply = _reply_to(transcript, segment_id)
    return reply is not None and _is_denial(_opening_words(reply.text))


_ACKNOWLEDGEMENTS = _DISCOURSE | {"maybe", "perhaps", "sure", "mhm", "yes", "yeah", "i", "see"}


def _speaks_to_it(text: str) -> bool:
    """The patient said something of their own: not just "Okay." or "Maybe.".

    A short denial ("No.", "I do not.") is not a report; a longer answer that
    merely opens with one ("I don't smoke, I do drink at weekends") is.
    """
    content = set(normalised_tokens(text)) - DISFLUENCY_FILLERS - _ACKNOWLEDGEMENTS
    return bool(content) and not (_is_denial(_opening_words(text)) and len(content) <= 3)


def _affirms(text: str) -> bool:
    tokens = normalised_tokens(text)
    # "Mm-hmm", "mhm" and "uh-huh" are yes (the tokenizer splits hyphens).
    if tokens[:2] in (("mm", "hmm"), ("mm", "hm"), ("uh", "huh")) or tokens[:1] == ("mhm",):
        return True
    words = _opening_words(text)
    return (
        bool(words)
        and not _is_denial(words)
        and (words[0] in _AFFIRMATIONS or (words[0] in _FIRST_PERSON and words[1:2] == ["do"]))
    )


def _quotes(evidence: AssertionEvidenceResult, transcript: Transcript) -> list[str]:
    """Aligned quote texts. Pieces of one quote split across segments (the
    exploratory span check sets ``span_group``) are read as one quote, keeping
    any sentence punctuation that ends an earlier piece ("No pain." | "I have a
    cough" stays two sentences)."""
    quotes: dict[str, list[str]] = {}
    for number, item in enumerate(evidence.items):
        if item.aligned is None:
            continue
        key = item.aligned.span_group or f"item-{number}"
        parts = quotes.setdefault(key, [])
        if parts and item.aligned.span_group:
            previous = evidence.items[number - 1]
            segment = transcript.segment_by_id(previous.segment_id)
            if previous.aligned is not None and segment is not None:
                tail = segment.text[previous.aligned.char_end :]
                parts[-1] += "".join(char for char in tail if char in ".?!;")
        parts.append(item.aligned.aligned_text)
    return [" ".join(parts) for parts in quotes.values()]


def check_consistency(
    assertion: AssertionBase,
    evidence: AssertionEvidenceResult,
    transcript: Transcript,
    synonyms: Synonyms | None = None,
) -> ConsistencyResult:
    hygiene = _field_hygiene(assertion)
    assertion = hygiene.usable_assertion(assertion)
    quotes = _quotes(evidence, transcript)
    if not quotes:
        # Alignment already fails; don't manufacture clinical conflicts.
        return hygiene
    failures: list[str] = []
    reasons: list[str] = list(hygiene.review_reasons)
    numbers = frozenset().union(*(numeric_tokens(quote) for quote in quotes))
    for field in ("value_text", "dose_text"):
        text = getattr(assertion, field, None)
        if text and not numeric_tokens(text).issubset(numbers):
            failures.append(
                {
                    "value_text": CheckReason.VALUE_TEXT_NUMBER_NOT_IN_QUOTE,
                    "dose_text": CheckReason.DOSE_TEXT_NUMBER_NOT_IN_QUOTE,
                }[field]
            )
    key_terms: list[str] = []
    concept_aliases: list[str] = []
    for field in ("concept_text", "allergen_text"):
        text = getattr(assertion, field, None)
        if text is None:
            continue  # Missing data is a mapper/review concern, not invention.
        aliases = [text, *(synonyms(text, assertion.kind) if synonyms else ())]
        key_terms.extend(aliases)
        if field == "concept_text":
            concept_aliases = aliases
        if not any(contains_phrase(quote, alias) for quote in quotes for alias in aliases):
            target = reasons if field == "concept_text" else failures
            target.append(
                {
                    "concept_text": CheckReason.CONCEPT_TEXT_NOT_IN_QUOTE,
                    "allergen_text": CheckReason.ALLERGEN_TEXT_NOT_IN_QUOTE,
                }[field]
            )
    if evidence.checker_version == "evidence-v4":
        # The same clause scope as the evidence check: a denial counts when it
        # covers the concept in the quote ("nausea, but no vomiting" does not
        # deny nausea); with no key term in a quote, any cue counts.
        negation = False
        for quote in quotes:
            covered = negation_covers(quote, key_terms)
            if covered or (covered is None and has_negation(quote)):
                negation = True
                break
    else:
        polarity_texts: list[str] = []
        for quote in quotes:
            matching_sentences = [
                sentence
                for sentence in sentence_texts(quote)
                if any(contains_phrase(sentence, term) for term in key_terms)
            ]
            # Keep the whole quote when no key term establishes a narrower scope.
            polarity_texts.extend(matching_sentences or [quote])
        negation = any(has_negation(text) for text in polarity_texts)
    if assertion.polarity == Polarity.AFFIRMED and negation:
        reasons.append(CheckReason.POLARITY_CONFLICTS_WITH_QUOTE)
    if (
        assertion.polarity == Polarity.NEGATED or assertion.kind in _NEGATED_KINDS
    ) and not negation:
        reasons.append(CheckReason.NEGATION_NOT_IN_QUOTE)
    if assertion.experiencer == Experiencer.PATIENT and any(
        # Possessive mother’s is the same family cue; don't stem clinical names.
        token.removesuffix("'s") in FAMILY_CUES
        for quote in quotes
        for token in normalised_tokens(quote)
    ):
        reasons.append(CheckReason.EXPERIENCER_CUE_CONFLICT)
    roles = {
        segment.role
        for item in evidence.items
        if item.aligned is not None
        and (segment := transcript.segment_by_id(item.segment_id)) is not None
    }
    if assertion.source_speaker_role not in roles:
        reasons.append(CheckReason.SOURCE_ROLE_MISMATCH)
    if assertion.kind in _CLINICIAN_KINDS and (
        assertion.source_speaker_role != SpeakerRole.CLINICIAN or SpeakerRole.CLINICIAN not in roles
    ):
        reasons.append(CheckReason.KIND_ROLE_MISMATCH)
    value = getattr(assertion, "value_text", None) or getattr(assertion, "dose_text", None)
    value_numbers = numeric_tokens(value or "")
    # Each cited item, judged by the whole sentences its quote touches (a
    # fragment like "is 97" must not hide the measurement it belongs to).
    cited = []  # (segment, aligned text, touched sentences)
    for item in evidence.items:
        segment = transcript.segment_by_id(item.segment_id)
        if item.aligned is None or segment is None:
            continue
        clinician = segment.role == SpeakerRole.CLINICIAN
        sentences = []  # (text, is a question, is only discourse, carries concept/value)
        for s_start, s_end, closing in _sentences(segment.text):
            if not (s_start < item.aligned.char_end and s_end > item.aligned.char_start):
                continue
            sentence = segment.text[s_start:s_end]
            sentences.append(
                (
                    sentence,
                    _sentence_is_question(sentence, closing, segment.text, clinician=clinician),
                    set(normalised_tokens(sentence)) <= _DISCOURSE,
                    any(contains_phrase(sentence, alias) for alias in concept_aliases)
                    or (bool(value_numbers) and value_numbers <= numeric_tokens(sentence)),
                )
            )
        cited.append((segment, item.aligned.aligned_text, sentences))
    # A number must belong to its own measurement or medicine, in one sentence (A3).
    stops = measurement_stops(concept_aliases) if assertion.kind == "vital_sign" else ()
    for field in ("value_text", "dose_text"):
        text = getattr(assertion, field, None)
        if text and numeric_tokens(text) <= numbers:
            attached = [
                number_attached(" ".join(s for s, *_ in sentences), concept_aliases, text, stops)
                for _, _, sentences in cited
            ]
            if True not in attached:
                reasons.append(CheckReason.NUMBER_NOT_ATTACHED_TO_CONCEPT)
                break
    # A patient's report needs the patient's own words: the concept, a "yes", or content.
    if assertion.kind == "patient_reported_condition" and not any(
        segment.role == SpeakerRole.PATIENT
        and (any(carries for *_, carries in sentences) or _affirms(text) or _speaks_to_it(text))
        for segment, text, sentences in cited
    ):
        reasons.append(CheckReason.PATIENT_REPORT_WITHOUT_PATIENT_TURN)
    # A question is not a statement: "Do you have asthma?" "No." is not asthma.
    # Support needs a statement sentence (not a question, not "Thanks.")
    # carrying the concept or value. A denied question supports a negated
    # fact ("No cough?" "No."), and contradicts an affirmed one even when some
    # other sentence seems to support it.
    every = [entry for _, _, sentences in cited for entry in sentences]
    asked_about_it = [
        segment
        for segment, _, sentences in cited
        if any(question and carries for _, question, _, carries in sentences)
    ]
    asked = asked_about_it or [
        segment for segment, _, sentences in cited if any(q for _, q, _, _ in sentences)
    ]
    if asked:
        statements = [entry for entry in every if not entry[1] and not entry[2]]
        if any(carries for *_, carries in every):
            supported = any(carries for *_, carries in statements)
        else:
            supported = bool(statements)
        # A "no" counts against this fact only when the question asked about it
        # (or when nothing else in the quote supports the fact at all).
        relevant = asked_about_it or ([] if supported else asked)
        denied = any(_answer_denies(transcript, segment.id) for segment in relevant)
        if assertion.polarity == Polarity.NEGATED and denied:
            supported = True
        if not supported:
            reasons.append(CheckReason.QUOTE_IS_QUESTION)
        if assertion.polarity == Polarity.AFFIRMED and denied:
            reasons.append(CheckReason.ANSWER_DENIES_QUESTION)
    return hygiene.model_copy(
        update={"failures": tuple(failures), "review_reasons": tuple(reasons)}
    )
