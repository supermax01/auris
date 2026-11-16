"""Quote-free measurements of generation cost and evidence failures."""

import re
from collections import Counter
from collections.abc import Sequence
from statistics import mean, median
from typing import Any

from auris.assertions import AssertionBase
from auris.evidence import EvidenceFailure, align_assertion_evidence, normalised_tokens
from auris.extract.objects import AssertionScanner
from auris.fhir.models import LaneState, MappingOutcome
from auris.transcript import Transcript


def repetition_metrics(text: str) -> dict[str, Any]:
    """Content-free diagnostics for pretty and compact output, including truncation.

    Measure adjacent repeated lexical/punctuation n-grams of widths 1..16;
    report the longest covered run in tokens. Repeated-line ratio counts nonblank
    lines beyond their first occurrence. Width <=16 misses whole-object loops
    spanning 60–130 tokens. V2 also counts complete top-level assertion objects
    using the streaming guard's identity. Polarity flip pairs count distinct
    unordered polarity variants for the same kind, normalized concept, evidence.
    Incomplete tails are excluded; diagnostics grant no schema acceptance.
    """
    words = re.findall(r"\w+|[^\w\s]", text)
    longest = 0
    for width in range(1, min(16, len(words) // 2) + 1):
        run = [0] * len(words)
        for i in range(width, len(words)):
            if words[i] == words[i - width]:
                run[i] = run[i - 1] + 1
                if run[i] >= width:
                    longest = max(longest, run[i] + width)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    scanner = AssertionScanner()
    scanner.feed(text)
    return {
        "version": "repetition-v2",
        **scanner.metrics,
        "longest_repeated_ngram_run_tokens": longest,
        "max_ngram_tokens": 16,
        "repeated_line_ratio": (len(lines) - len(set(lines))) / len(lines) if lines else 0,
    }


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
        "output_token_count_methods": dict(
            Counter(c.get("output_token_count_method", "unknown") for c in calls)
        ),
        "output_token_scope": (
            "all attempts, including discarded output and overlapping windows / merged assertions"
        ),
        "calls_with_output_usage": sum(type(n) is int and n >= 0 for n in counts),
        "call_count": len(calls),
        "loop_stops": sum(c.get("finish_reason") == "loop_guard" for c in calls),
        "truncations": sum(c.get("finish_reason") == "length" for c in calls),
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
