"""Join a speaker's consecutive speech-to-text pieces into turns.

Whisper ends a segment wherever its 30-second window or its own sentence
guess falls, often mid-sentence, and Auris splits further at speaker changes
and long pauses. A human transcript is written in turns. When a sentence is
split, a quote of the whole sentence spans two segments and the evidence
check (one quote, one segment) fails. Two consecutive segments are joined
when all of these hold:

* the same speaker and role;
* no one else's segment starts between them (they are adjacent in time order);
* the pause between them is at most ``gap_seconds``;
* the joined turn lasts at most ``max_seconds`` (a long monologue still
  splits, so one segment never outgrows an extraction window).

Words, timings and order are unchanged; only segment boundaries move.
"""

from __future__ import annotations

from auris.transcript import Transcript, TranscriptSegment

JOIN_VERSION = "join-turns-v1"


def join_turns(
    transcript: Transcript, *, gap_seconds: float, max_seconds: float
) -> tuple[Transcript, int]:
    """The transcript with consecutive same-speaker segments joined, and how many
    joins were made. Untimed transcripts are returned unchanged."""
    if transcript.timing_mode != "timed" or not transcript.segments:
        return transcript, 0
    ordered = sorted(
        transcript.segments, key=lambda s: (s.start_seconds or 0.0, s.end_seconds or 0.0, s.id)
    )
    turns: list[TranscriptSegment] = [ordered[0]]
    joins = 0
    for segment in ordered[1:]:
        last = turns[-1]
        if _joinable(last, segment, gap_seconds=gap_seconds, max_seconds=max_seconds):
            turns[-1] = _joined(last, segment)
            joins += 1
        else:
            turns.append(segment)
    if not joins:
        return transcript, 0
    segments = tuple(
        turn.model_copy(update={"id": f"s{number}"}) for number, turn in enumerate(turns, 1)
    )
    joined = Transcript.model_validate(
        {
            **transcript.model_dump(),
            "segments": [segment.model_dump() for segment in segments],
        }
    )
    return joined, joins


def _joinable(
    last: TranscriptSegment, segment: TranscriptSegment, *, gap_seconds: float, max_seconds: float
) -> bool:
    if (last.speaker_cluster_id, last.role) != (segment.speaker_cluster_id, segment.role):
        return False
    if None in (last.start_seconds, last.end_seconds, segment.start_seconds, segment.end_seconds):
        return False
    assert last.start_seconds is not None and last.end_seconds is not None
    assert segment.start_seconds is not None and segment.end_seconds is not None
    if segment.start_seconds < last.end_seconds:  # overlapping: keep apart
        return False
    if segment.start_seconds - last.end_seconds > gap_seconds:
        return False
    if segment.end_seconds - last.start_seconds > max_seconds:
        return False
    if last.raw_text is not None or segment.raw_text is not None:
        return False  # respelled text keeps its own segment (the heard text is per segment)
    return True


def _joined(last: TranscriptSegment, segment: TranscriptSegment) -> TranscriptSegment:
    words = None
    if last.words is not None and segment.words is not None:
        words = (*last.words, *segment.words)
    return last.model_copy(
        update={
            "end_seconds": segment.end_seconds,
            "text": f"{last.text.rstrip()} {segment.text.lstrip()}".strip(),
            "words": words,
        }
    )
