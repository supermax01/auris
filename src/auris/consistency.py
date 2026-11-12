"""Deterministic lane-2 checks after quote alignment (PLAN A2/A4).

Hard failures require exact numeric support or whole-token concept support.
A concept may also use an explicit terminology alias for the same unambiguous
code (no fuzzy candidates). Lexical scope ambiguity is review, never a FAIL.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from pydantic import BaseModel, ConfigDict

from auris.assertions import AssertionBase, Experiencer, Polarity
from auris.evidence import AssertionEvidenceResult, has_negation, normalised_tokens
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
Synonyms = Callable[[str, str], Iterable[str]]


class ConsistencyResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    failures: tuple[str, ...] = ()
    review_reasons: tuple[str, ...] = ()

    @property
    def force_material(self) -> bool:
        return bool(self.failures or self.review_reasons)


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
    quotes = [item.aligned.aligned_text for item in evidence.items if item.aligned is not None]
    if not quotes:
        # Alignment already fails; don't manufacture clinical conflicts.
        return ConsistencyResult()
    failures: list[str] = []
    reasons: list[str] = []
    numbers = frozenset().union(*(numeric_tokens(quote) for quote in quotes))
    for field in ("value_text", "dose_text"):
        text = getattr(assertion, field, None)
        if text and not numeric_tokens(text).issubset(numbers):
            failures.append(f"{field}_number_not_in_quote")
    for field in ("concept_text", "allergen_text"):
        text = getattr(assertion, field, None)
        if text is None:
            continue  # Missing data is a mapper/review concern, not invention.
        aliases = [text, *(synonyms(text, assertion.kind) if synonyms else ())]
        if not any(contains_phrase(quote, alias) for quote in quotes for alias in aliases):
            failures.append(f"{field}_not_in_quote")
    negation = any(has_negation(quote) for quote in quotes)
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
    return ConsistencyResult(failures=tuple(failures), review_reasons=tuple(reasons))
