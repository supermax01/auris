"""Transcript model (PLAN section 5, steps 2-4).

A transcript is an immutable, ordered list of diarised segments. Each
segment carries the speaker cluster assigned by diarisation, the role
assigned to that speaker (step 4), its audio extent in seconds and its
text. Word timings, when the ASR stage provides them, are marked
approximate: ASR word boundaries are estimates, not measurements.

The transcript is the evidence source for every later stage (PLAN A5),
so it has a stable content hash: SHA-256 over a canonical JSON
serialisation (sorted keys, compact separators, UTF-8, segments in
stored order, prefixed with a domain-separation tag). Any change to
any field of any segment changes the hash.
"""

from __future__ import annotations

import hashlib
import json
import math
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from auris._storage import write_private_text

_HASH_PREFIX = b"auris.transcript.v1\n"


class SpeakerRole(StrEnum):
    """Role assigned to a speaker cluster (PLAN section 5, step 4)."""

    CLINICIAN = "clinician"
    PATIENT = "patient"
    COMPANION = "companion"
    OTHER = "other"
    UNKNOWN = "unknown"


def _check_finite_non_negative(value: float, field: str) -> float:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} must be a finite number of seconds >= 0")
    return value


class _TranscriptModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WordTiming(_TranscriptModel):
    """One ASR word with its estimated audio extent, in seconds."""

    word: str = Field(min_length=1)
    start_seconds: float
    end_seconds: float
    approximate: Literal[True] = True

    @model_validator(mode="after")
    def _check_extent(self) -> WordTiming:
        if not self.word.strip():
            raise ValueError("word must not be blank")
        _check_finite_non_negative(self.start_seconds, "start_seconds")
        _check_finite_non_negative(self.end_seconds, "end_seconds")
        if self.end_seconds < self.start_seconds:
            raise ValueError("word end_seconds must be >= start_seconds")
        return self


class TranscriptSegment(_TranscriptModel):
    """One immutable diarised segment of a transcript."""

    id: str = Field(min_length=1)
    speaker_cluster_id: str = Field(min_length=1)
    role: SpeakerRole
    start_seconds: float
    end_seconds: float
    text: str
    words: tuple[WordTiming, ...] | None = None

    @model_validator(mode="after")
    def _check_extent_and_words(self) -> TranscriptSegment:
        _check_finite_non_negative(self.start_seconds, "start_seconds")
        _check_finite_non_negative(self.end_seconds, "end_seconds")
        if self.end_seconds < self.start_seconds:
            raise ValueError("segment end_seconds must be >= start_seconds")
        if not self.id.strip():
            raise ValueError("segment id must not be blank")
        if not self.speaker_cluster_id.strip():
            raise ValueError("speaker_cluster_id must not be blank")
        if self.words is not None:
            if len(self.words) == 0:
                raise ValueError("words must be omitted (null) rather than empty")
            for previous, current in zip(self.words, self.words[1:], strict=False):
                if (
                    current.start_seconds < previous.start_seconds
                    or current.end_seconds < previous.end_seconds
                ):
                    raise ValueError("words must be ordered by start_seconds and end_seconds")
            for word in self.words:
                if word.start_seconds < self.start_seconds or word.end_seconds > self.end_seconds:
                    raise ValueError("word timings must lie within the segment bounds")
        return self


class Transcript(_TranscriptModel):
    """An immutable transcript: an ordered tuple of segments.

    Segments keep their given order (overlapping speech is allowed, so
    extents may overlap). Segment ids must be unique: evidence cites
    segments by id (PLAN A5). An empty transcript is representable
    (e.g. silent audio) and still hashes.
    """

    schema_version: Literal["1"] = "1"
    segments: tuple[TranscriptSegment, ...] = ()

    @model_validator(mode="after")
    def _check_unique_segment_ids(self) -> Transcript:
        seen: set[str] = set()
        for segment in self.segments:
            if segment.id in seen:
                raise ValueError(f"duplicate segment id: {segment.id!r}")
            seen.add(segment.id)
        return self

    def segment_by_id(self, segment_id: str) -> TranscriptSegment | None:
        for segment in self.segments:
            if segment.id == segment_id:
                return segment
        return None

    def canonical_bytes(self) -> bytes:
        """Canonical serialisation the content hash is taken over."""
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return _HASH_PREFIX + payload.encode("utf-8")

    @property
    def content_hash(self) -> str:
        """SHA-256 hex digest of :meth:`canonical_bytes`."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Transcript:
        return cls.model_validate_json(text)

    def save(self, path: str | Path) -> None:
        """Save at mode 0600 in a new or existing 0700 workdir (A8)."""
        write_private_text(path, self.to_json())

    @classmethod
    def load(cls, path: str | Path) -> Transcript:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))
