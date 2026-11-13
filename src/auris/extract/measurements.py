"""Quote-free measurements of generation cost and evidence failures."""

from collections import Counter
from collections.abc import Sequence
from statistics import mean, median
from typing import Any

from auris.assertions import AssertionBase
from auris.evidence import EvidenceFailure, align_assertion_evidence, normalised_tokens
from auris.fhir.models import LaneState, MappingOutcome
from auris.transcript import Transcript


def length_stats(lengths: Sequence[int]) -> dict[str, int | float | None]:
    return {
        "count": len(lengths),
        "total": sum(lengths),
        "min": min(lengths) if lengths else None,
        "max": max(lengths) if lengths else None,
        "mean": mean(lengths) if lengths else None,
        "median": median(lengths) if lengths else None,
    }


def json_whitespace_stats(text: str) -> dict[str, int | bool]:
    """Measure generated whitespace without counting spaces inside clinical text."""
    quoted = escaped = False
    whitespace = 0
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in " \t\r\n":
            whitespace += 1
    return {
        "single_line": "\n" not in text and "\r" not in text,
        "whitespace_outside_strings": whitespace,
        "compact": whitespace == 0,
    }


def extraction_measurements(
    assertions: Sequence[AssertionBase],
    calls: Sequence[dict[str, Any]],
    transcript: Transcript,
    citation_failures: dict[int, frozenset[str]],
) -> dict[str, Any]:
    """Count each reason once per assertion; also retain quote-level counts.

    Total generation cost includes discarded attempts and overlap. Missing usage
    makes the cost unknown; it is never replaced by a zero or a text estimate.
    """
    quote_lengths = [len(item.quote) for a in assertions for item in a.evidence]
    word_lengths = [len(normalised_tokens(item.quote)) for a in assertions for item in a.evidence]
    assertion_reasons: Counter[str] = Counter()
    quote_reasons: Counter[str] = Counter()
    failed_assertions = 0
    for position, assertion in enumerate(assertions):
        invalid = citation_failures.get(position, frozenset())
        evidence = align_assertion_evidence(
            assertion,
            transcript,
            allowed_segment_ids=frozenset(s.id for s in transcript.segments) - invalid,
        )
        reasons = [reason.value for reason in evidence.failures]
        quote_reasons.update(reasons)
        assertion_reasons.update(set(reasons))
        failed_assertions += bool(reasons)
    counts = [call.get("actual_output_tokens") for call in calls]
    known = all(type(n) is int and n >= 0 for n in counts)
    total = sum(counts) if known and calls else None
    return {
        "assertion_count": len(assertions),
        "output_tokens": total,
        "output_tokens_per_assertion": total / len(assertions)
        if total is not None and assertions
        else None,
        "output_token_scope": (
            "all attempts, including discarded output and overlapping windows / merged assertions"
        ),
        "calls_with_output_usage": sum(type(n) is int and n >= 0 for n in counts),
        "call_count": len(calls),
        "quote_characters": length_stats(quote_lengths),
        "quote_words": length_stats(word_lengths),
        "evidence_failed_assertions": failed_assertions,
        "evidence_scope": "quote alignment only; consistency not checked",
        "evidence_failures_by_reason": {
            r.value: assertion_reasons[r.value] for r in EvidenceFailure
        },
        "evidence_failed_quotes_by_reason": {
            r.value: quote_reasons[r.value] for r in EvidenceFailure
        },
    }


def mapped_evidence_measurements(outcomes: Sequence[MappingOutcome]) -> dict[str, Any]:
    """The complete A2 evidence lane includes hard consistency failures."""
    reasons: Counter[str] = Counter({reason.value: 0 for reason in EvidenceFailure})
    for outcome in outcomes:
        reasons.update(
            {*(r.value for r in outcome.evidence.failures), *outcome.consistency.failures}
        )
    return {
        "evidence_failed_assertions": sum(o.lanes.evidence == LaneState.FAIL for o in outcomes),
        "evidence_failures_by_reason": dict(sorted(reasons.items())),
        "evidence_scope": "quote alignment and consistency",
    }
