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

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

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
    start_seconds: float | None = None
    end_seconds: float | None = None
    text: str
    words: tuple[WordTiming, ...] | None = None
    turn_number: int | None = Field(default=None, ge=1)
    raw_text: str | None = None

    @model_validator(mode="after")
    def _check_extent_and_words(self) -> TranscriptSegment:
        if self.start_seconds is None or self.end_seconds is None:
            if self.start_seconds is not None or self.end_seconds is not None:
                raise ValueError("segment bounds must both be present or absent")
            if self.words is not None or self.turn_number is None:
                raise ValueError("untimed segments require a turn number and no word timings")
        else:
            _check_finite_non_negative(self.start_seconds, "start_seconds")
            _check_finite_non_negative(self.end_seconds, "end_seconds")
            if self.end_seconds < self.start_seconds:
                raise ValueError("segment end_seconds must be >= start_seconds")
        if not self.id.strip():
            raise ValueError("segment id must not be blank")
        if not self.speaker_cluster_id.strip():
            raise ValueError("speaker_cluster_id must not be blank")
        if self.words is not None:
            assert self.start_seconds is not None and self.end_seconds is not None
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


class TranscriptSource(_TranscriptModel):
    dataset: str
    encounter_id: str
    repository: str
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    license: str
    variant: str
    upstream_split: str
    source_files: tuple[str, ...]
    human_transcription: bool
    speaker_tags: Literal["source roles; not adjudicated", "speaker tags unverified"]
    markup_removed: dict[str, int] = Field(default_factory=dict)
    markup_characters_removed: int = Field(default=0, ge=0)
    silence_intervals_dropped: int = Field(default=0, ge=0)


class Transcript(_TranscriptModel):
    """An immutable transcript: an ordered tuple of segments.

    Segments keep their given order (overlapping speech is allowed, so
    extents may overlap). Segment ids must be unique: evidence cites
    segments by id (PLAN A5). An empty transcript is representable
    (e.g. silent audio) and still hashes.
    """

    schema_version: Literal["1", "2"] = "1"
    segments: tuple[TranscriptSegment, ...] = ()
    timing_mode: Literal["timed", "untimed"] = "timed"
    timing_source: Literal["unspecified", "human_utterance_bounds", "none"] = "unspecified"
    source: TranscriptSource | None = None

    @model_validator(mode="after")
    def _check_unique_segment_ids(self) -> Transcript:
        if self.schema_version == "1" and (
            self.timing_mode != "timed"
            or self.timing_source != "unspecified"
            or self.source is not None
            or any(s.raw_text is not None or s.turn_number is not None for s in self.segments)
        ):
            raise ValueError("extended transcript metadata requires schema version 2")
        if (self.timing_mode == "untimed") != (self.timing_source == "none"):
            raise ValueError("untimed transcripts must declare timing source none")
        seen: set[str] = set()
        for number, segment in enumerate(self.segments, 1):
            if (segment.start_seconds is None) != (self.timing_mode == "untimed"):
                raise ValueError("segment bounds disagree with transcript timing mode")
            if self.timing_mode == "untimed" and segment.turn_number != number:
                raise ValueError("untimed turn numbers must be consecutive in stored order")
            if segment.id in seen:
                raise ValueError(f"duplicate segment id: {segment.id!r}")
            seen.add(segment.id)
        return self

    def segment_by_id(self, segment_id: str) -> TranscriptSegment | None:
        for segment in self.segments:
            if segment.id == segment_id:
                return segment
        return None

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> dict:
        data = handler(self)
        if self.schema_version == "1":
            # Preserve hashes of already-frozen v1 transcripts and evaluations.
            for key in ("timing_mode", "timing_source", "source"):
                data.pop(key, None)
            for segment in data.get("segments", ()):
                segment.pop("raw_text", None)
                segment.pop("turn_number", None)
        return data

    def canonical_bytes(self) -> bytes:
        """Canonical serialisation the content hash is taken over."""
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        prefix = _HASH_PREFIX if self.schema_version == "1" else b"auris.transcript.v2\n"
        return prefix + payload.encode("utf-8")

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
