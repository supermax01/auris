"""Immutable prompt bytes and reproducible, segment-boundary rendering."""

import hashlib
from collections.abc import Sequence
from importlib.resources import files

from auris.extract.profiles import ExtractionProfile, profile_contract
from auris.transcript import TranscriptSegment

PROMPT_VERSION = "extraction_v3"


def prompt_text(profile: ExtractionProfile = "full-v0") -> str:
    version = profile_contract(profile).prompt_version
    return files("auris.extract").joinpath(f"prompts/{version}.md").read_text("utf-8")


def prompt_sha256(profile: ExtractionProfile = "full-v0") -> str:
    return hashlib.sha256(prompt_text(profile).encode("utf-8")).hexdigest()


def _timestamp(seconds: float) -> str:
    # Integer tenths prevent rounding 59.99 into a displayed 00:60.0.
    tenths = round(seconds * 10)
    minutes, remainder = divmod(tenths, 600)
    return f"{minutes:02d}:{remainder / 10:04.1f}"


def render_transcript(segments: Sequence[TranscriptSegment]) -> str:
    # Escape embedded line breaks, leaving the original text in Transcript
    # for evidence alignment. Quotes must use original words, not escapes.
    return "\n".join(
        f"[{s.id} | {s.role} | {_location(s)}] " + s.text.replace("\r", "\\r").replace("\n", "\\n")
        for s in segments
    )


def _location(segment: TranscriptSegment) -> str:
    if segment.start_seconds is None:
        return f"turn {segment.turn_number}"
    assert segment.end_seconds is not None
    return f"{_timestamp(segment.start_seconds)}–{_timestamp(segment.end_seconds)}"


def messages(
    segments: Sequence[TranscriptSegment], profile: ExtractionProfile = "full-v0"
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": prompt_text(profile)},
        {"role": "user", "content": "Transcript:\n" + render_transcript(segments)},
    ]
