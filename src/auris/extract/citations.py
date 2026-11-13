"""Diagnose citations; resolve only unique location aliases in the shown window."""

from collections.abc import Sequence
from typing import Any, Literal

from auris.assertions import AssertionBase
from auris.evidence import normalised_tokens
from auris.extract.prompt import segment_location, shown_segments
from auris.transcript import Transcript, TranscriptSegment

CitationClass = Literal[
    "cited_empty_segment",
    "in_window",
    "adjacent_or_overlap",
    "earlier_window",
    "malformed_or_nonexistent",
    "id_format_confusion",
]


def classify_citation(
    segment_id: str,
    segments: Sequence[TranscriptSegment],
    transcript: Transcript,
    earlier_ids: frozenset[str] = frozenset(),
) -> CitationClass:
    cited = transcript.segment_by_id(segment_id)
    if cited is not None and not normalised_tokens(cited.text):
        return "cited_empty_segment"
    allowed = {s.id for s in shown_segments(segments)}
    if segment_id in allowed:
        return "in_window"
    if any(segment_id == segment_location(s) for s in transcript.segments):
        return "id_format_confusion"
    positions = {s.id: i for i, s in enumerate(transcript.segments)}
    position = positions.get(segment_id)
    if position is None:
        return "malformed_or_nonexistent"
    if any(abs(position - positions[id_]) <= 2 for id_ in allowed):
        return "adjacent_or_overlap"
    if segment_id in earlier_ids:
        return "earlier_window"
    # A real but unseen, nonadjacent id is still an invalid citation.
    return "malformed_or_nonexistent"


def resolve_citations(
    assertions: Sequence[AssertionBase],
    segments: Sequence[TranscriptSegment],
    *,
    known_segment_ids: frozenset[str] = frozenset(),
) -> tuple[tuple[AssertionBase, ...], list[dict[str, Any]]]:
    """No quote search across the encounter and no widening of allowed evidence.

    A timestamp/turn label must match exactly as rendered and identify one shown
    segment. Actual ids (including foreign ids) are never treated as aliases.
    Normal quote alignment and qualifier/negation checks still run afterwards.
    """
    visible = shown_segments(segments)
    allowed = {s.id for s in visible}
    aliases: dict[str, list[str]] = {}
    for segment in visible:
        aliases.setdefault(segment_location(segment), []).append(segment.id)
    resolved = []
    changes = []
    for position, assertion in enumerate(assertions):
        evidence = []
        for index, item in enumerate(assertion.evidence):
            candidates = aliases.get(item.segment_id, [])
            if item.segment_id not in allowed | known_segment_ids and len(candidates) == 1:
                changes.append(
                    {
                        "assertion_position": position,
                        "evidence_position": index,
                        "original_segment_id": item.segment_id,
                        "segment_id": candidates[0],
                        "reason": "unique_shown_location",
                    }
                )
                item = item.model_copy(update={"segment_id": candidates[0]})
            evidence.append(item)
        resolved.append(assertion.model_copy(update={"evidence": tuple(evidence)}))
    return tuple(resolved), changes
