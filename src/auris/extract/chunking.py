"""Context-bounded segment windows and lossless, evidence-based merging."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from auris.assertions import MAX_EVIDENCE_ITEMS, AssertionBase, parse_assertion
from auris.evidence import align_assertion_evidence, normalised_tokens
from auris.extract.backend import Backend, ExtractionError
from auris.extract.prompt import messages, render_transcript
from auris.transcript import Transcript, TranscriptSegment

HEADROOM_TOKENS = 256
WINDOW_TOKENS = 4096
OVERLAP_SEGMENTS = 2


@dataclass(frozen=True)
class Chunk:
    segments: tuple[TranscriptSegment, ...]
    input_tokens: int
    token_count_method: str


class TokenCounter:
    def __init__(self, backend: Backend) -> None:
        self.backend = backend
        self.used_estimate = False
        self._cache: dict[str, int] = {}

    @property
    def method(self) -> str:
        return "conservative_utf8_chars" if self.used_estimate else "server_tokenize"

    def count(self, text: str) -> int:
        if text not in self._cache:
            measured = self.backend.tokenize(text)
            if measured is None:
                self.used_estimate = True
                # Three ASCII chars/token; non-ASCII bytes each reserve a token.
                # This is an estimate, not a tokenizer guarantee. The request
                # must still fail rather than consume any partial completion.
                ascii_chars = sum(ord(char) < 128 for char in text)
                measured = math.ceil(ascii_chars / 3) + len(text.encode()) - ascii_chars
            self._cache[text] = measured
        return self._cache[text]


def plan_chunks(
    transcript: Transcript,
    schema: dict[str, Any],
    backend: Backend,
    *,
    n_ctx: int,
    max_tokens: int,
) -> tuple[Chunk, ...]:
    counter = TokenCounter(backend)
    schema_tokens = counter.count(json.dumps(schema, separators=(",", ":"), ensure_ascii=False))

    def input_size(segments: Sequence[TranscriptSegment]) -> int:
        # Reserve schema even on servers which compile it solely as a grammar.
        return schema_tokens + sum(counter.count(m["content"]) for m in messages(segments))

    capacity = n_ctx - max_tokens - HEADROOM_TOKENS
    whole = input_size(transcript.segments)
    if whole <= capacity:
        return (Chunk(transcript.segments, whole, counter.method),)
    if input_size(()) >= capacity:
        raise ExtractionError("prompt + schema + output cap leave no context for transcript")
    chunks: list[Chunk] = []
    start = 0
    while start < len(transcript.segments):
        stop = start
        while stop < len(transcript.segments):
            proposed = transcript.segments[start : stop + 1]
            if input_size(proposed) > capacity:
                break
            # A single segment may exceed the target window, but must fit n_ctx.
            if stop > start and counter.count(render_transcript(proposed)) > WINDOW_TOKENS:
                break
            stop += 1
        if stop == start:
            raise ExtractionError("one transcript segment exceeds the available context budget")
        selected = transcript.segments[start:stop]
        chunks.append(Chunk(selected, input_size(selected), counter.method))
        if stop == len(transcript.segments):
            break
        if stop - start <= OVERLAP_SEGMENTS:
            raise ExtractionError("context cannot fit a progressing window with 2-segment overlap")
        start = stop - OVERLAP_SEGMENTS
    return tuple(chunks)


@dataclass(frozen=True)
class MergeResult:
    assertions: tuple[AssertionBase, ...]
    conflict_positions: frozenset[int]


def merge_assertions(
    groups: Sequence[Sequence[AssertionBase]], transcript: Transcript
) -> MergeResult:
    """Coalesce only identical facts with overlapping aligned evidence.

    Conflicting facts are both retained and flagged, including cross-key
    polarity/experiencer/kind disagreements for the same concept/span.
    Merged encounters can exceed the per-call 64-assertion decoding bound.
    """
    merged: list[AssertionBase] = []
    conflicts: set[int] = set()

    def spans(assertion: AssertionBase) -> set[tuple[str, int, int]]:
        aligned = align_assertion_evidence(assertion, transcript)
        return {
            (item.segment_id, item.aligned.char_start, item.aligned.char_end)
            for item in aligned.items
            if item.aligned is not None
        }

    def overlaps(first: AssertionBase, second: AssertionBase) -> bool:
        return any(
            a_id == b_id and max(a_start, b_start) < min(a_end, b_end)
            for a_id, a_start, a_end in spans(first)
            for b_id, b_start, b_end in spans(second)
        )

    for group in groups:
        for assertion in group:
            duplicates: int | None = None
            conflicting: list[int] = []
            for position, previous in enumerate(merged):
                # Unknown concept cannot safely identify the same fact.
                if not assertion.concept_text or not previous.concept_text:
                    continue
                if normalised_tokens(assertion.concept_text) != normalised_tokens(
                    previous.concept_text
                ):
                    continue
                if not overlaps(previous, assertion):
                    continue
                first = previous.model_dump(mode="json", exclude={"evidence"})
                second = assertion.model_dump(mode="json", exclude={"evidence"})
                first["concept_text"] = second["concept_text"] = normalised_tokens(
                    assertion.concept_text
                )
                if first == second:
                    duplicates = position
                else:
                    conflicting.append(position)
            if duplicates is not None:
                previous = merged[duplicates]
                combined = tuple(dict.fromkeys((*previous.evidence, *assertion.evidence)))
                if len(combined) <= MAX_EVIDENCE_ITEMS:
                    data = previous.model_dump(mode="json")
                    data["evidence"] = [item.model_dump() for item in combined]
                    merged[duplicates] = parse_assertion(data)
                    if conflicting:
                        conflicts.update([duplicates, *conflicting])
                    continue
                conflicting.append(duplicates)  # Preserve all evidence beyond the schema bound.
            if conflicting:
                conflicts.update([len(merged), *conflicting])
            merged.append(assertion)
    return MergeResult(tuple(merged), frozenset(conflicts))
