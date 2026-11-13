"""Deterministic lane-2 checks after quote alignment (PLAN A2/A4).

Numbers and allergen substances require exact support. Concept normalisation
outside explicit terminology aliases requires review, never a lane FAIL.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, SerializerFunctionWrapHandler, model_serializer

from auris.assertions import AssertionBase, Experiencer, Polarity
from auris.evidence import AssertionEvidenceResult, has_negation, normalised_tokens, sentence_texts
from auris.transcript import SpeakerRole, Transcript

FAMILY_CUES = frozenset(
    "mother father sister brother son daughter wife husband partner grandmother grandfather "
    "aunt uncle mom mum dad grandma grandpa sibling parent cousin niece nephew spouse "
    "stepmother stepfather grandson granddaughter".split()
)
_NEGATED_KINDS = frozenset({"allergy_denial", "clinician_ruled_out_problem"})
_CLINICIAN_KINDS = frozenset(
    {"clinician_assessed_problem", "clinician_ruled_out_problem", "explicit_new_order"}
)
_NUMBER = re.compile(r"[+−-]?(?:\d+(?:[.,]\d+)*|\.\d+)")
_VALUE_FIELDS = ("value_text", "unit_text", "dose_text")
_NULLABLE_FIELDS = (*_VALUE_FIELDS, "route_text", "frequency_text", "stated_time_text")
_LITERAL_NULLS = frozenset({"null", "none", "n/a", "unknown"})
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
        informational_flags=("literal_null_normalised",) if nulls else (),
        normalised_null_fields=tuple(nulls),
        review_reasons=("field_contains_commentary",) if unusable else (),
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


def check_consistency(
    assertion: AssertionBase,
    evidence: AssertionEvidenceResult,
    transcript: Transcript,
    synonyms: Synonyms | None = None,
) -> ConsistencyResult:
    hygiene = _field_hygiene(assertion)
    assertion = hygiene.usable_assertion(assertion)
    quotes = [item.aligned.aligned_text for item in evidence.items if item.aligned is not None]
    if not quotes:
        # Alignment already fails; don't manufacture clinical conflicts.
        return hygiene
    failures: list[str] = []
    reasons: list[str] = list(hygiene.review_reasons)
    numbers = frozenset().union(*(numeric_tokens(quote) for quote in quotes))
    for field in ("value_text", "dose_text"):
        text = getattr(assertion, field, None)
        if text and not numeric_tokens(text).issubset(numbers):
            failures.append(f"{field}_number_not_in_quote")
    key_terms: list[str] = []
    for field in ("concept_text", "allergen_text"):
        text = getattr(assertion, field, None)
        if text is None:
            continue  # Missing data is a mapper/review concern, not invention.
        aliases = [text, *(synonyms(text, assertion.kind) if synonyms else ())]
        key_terms.extend(aliases)
        if not any(contains_phrase(quote, alias) for quote in quotes for alias in aliases):
            target = reasons if field == "concept_text" else failures
            target.append(f"{field}_not_in_quote")
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
        reasons.append("polarity_conflicts_with_quote")
    if (
        assertion.polarity == Polarity.NEGATED or assertion.kind in _NEGATED_KINDS
    ) and not negation:
        reasons.append("negation_not_in_quote")
    if assertion.experiencer == Experiencer.PATIENT and any(
        # Possessive mother’s is the same family cue; don't stem clinical names.
        token.removesuffix("'s") in FAMILY_CUES
        for quote in quotes
        for token in normalised_tokens(quote)
    ):
        reasons.append("experiencer_cue_conflict")
    roles = {
        segment.role
        for item in evidence.items
        if item.aligned is not None
        and (segment := transcript.segment_by_id(item.segment_id)) is not None
    }
    if assertion.source_speaker_role not in roles:
        reasons.append("source_role_mismatch")
    if assertion.kind in _CLINICIAN_KINDS and (
        assertion.source_speaker_role != SpeakerRole.CLINICIAN or SpeakerRole.CLINICIAN not in roles
    ):
        reasons.append("kind_role_mismatch")
    return hygiene.model_copy(
        update={"failures": tuple(failures), "review_reasons": tuple(reasons)}
    )
