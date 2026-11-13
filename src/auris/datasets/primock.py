"""Strict reader for PriMock57's long-form Praat IntervalTier TextGrids."""

import re
from collections import Counter
from dataclasses import dataclass

from auris.transcript import Transcript, TranscriptSegment, TranscriptSource

_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_STRING = r'"(?:[^\"]|"")*"'
_HEADER = re.compile(
    rf'\s*File type = "ooTextFile"\s*Object class = "TextGrid"\s*'
    rf"xmin = ({_NUMBER})\s*xmax = ({_NUMBER})\s*tiers\? <exists>\s*"
    rf'size = 1\s*item \[\]:\s*item \[1\]:\s*class = "IntervalTier"\s*'
    rf"name = ({_STRING})\s*xmin = ({_NUMBER})\s*xmax = ({_NUMBER})\s*"
    rf"intervals: size = (\d+)\s*"
)
_INTERVAL = re.compile(
    rf"intervals \[(\d+)\]:\s*xmin = ({_NUMBER})\s*xmax = ({_NUMBER})\s*"
    rf"text = ({_STRING})\s*"
)
_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9_]*(?:\s*/)?\s*>")


@dataclass(frozen=True)
class Interval:
    number: int
    start: float
    end: float
    text: str


def parse_textgrid(text: str, *, tier: str) -> tuple[Interval, ...]:
    """Refuse unsupported tiers, broken counts, gaps in syntax and invalid bounds.

    Doubled Praat quotes decode to one quote; multiline strings are supported.
    Silence is retained here so the adapter can count it before dropping it.
    """
    import math

    header = _HEADER.match(text)
    if header is None or header[3][1:-1].casefold() not in (tier.casefold(), "speaker"):
        raise ValueError("expected a single named IntervalTier in long-form TextGrid")
    low, high, tier_low, tier_high = (float(header[i]) for i in (1, 2, 4, 5))
    if not all(math.isfinite(v) for v in (low, high, tier_low, tier_high)) or not (
        0 <= low <= tier_low <= tier_high <= high
    ):
        raise ValueError("invalid TextGrid tier bounds")
    position = header.end()
    result = []
    previous_end = tier_low
    for number in range(1, int(header[6]) + 1):
        match = _INTERVAL.match(text, position)
        if match is None or int(match[1]) != number:
            raise ValueError("malformed TextGrid interval or count")
        start, end = float(match[2]), float(match[3])
        if not (math.isfinite(start) and math.isfinite(end)) or not (
            previous_end <= start <= end <= tier_high
        ):
            raise ValueError("invalid TextGrid interval bounds")
        result.append(Interval(number, start, end, match[4][1:-1].replace('""', '"')))
        previous_end = end
        position = match.end()
    if text[position:].strip():
        raise ValueError("unexpected trailing TextGrid content")
    return tuple(result)


def strip_markup(text: str) -> tuple[str, Counter[str]]:
    """Replace angle-bracket tags with spaces, then collapse whitespace.

    Enclosed words, disfluencies, repetitions, punctuation and uncertainty stay.
    No guesses replace unintelligible speech. Raw strings are stored separately.
    """
    tags = Counter(_TAG.findall(text))
    return " ".join(_TAG.sub(" ", text).split()), tags


def adapt_primock(doctor: str, patient: str, source: TranscriptSource) -> Transcript:
    intervals = []
    silence = 0
    removed: Counter[str] = Counter()
    for rank, (speaker, text) in enumerate((("doctor", doctor), ("patient", patient))):
        for interval in parse_textgrid(text, tier=speaker):
            if not interval.text.strip():
                silence += 1
                continue
            clean, tags = strip_markup(interval.text)
            removed.update(tags)
            # Keep markup-only utterances (empty extraction text) for future WER.
            intervals.append((interval.start, rank, interval, speaker, clean))
    intervals.sort(key=lambda row: (row[0], row[1], row[2].number))
    segments = tuple(
        TranscriptSegment(
            id=f"{speaker}-{interval.number:04d}",
            speaker_cluster_id=speaker,
            role="clinician" if speaker == "doctor" else "patient",
            start_seconds=interval.start,
            end_seconds=interval.end,
            text=clean,
            raw_text=interval.text,
        )
        for _, _, interval, speaker, clean in intervals
    )
    return Transcript(
        schema_version="2",
        timing_source="human_utterance_bounds",
        segments=segments,
        source=source.model_copy(
            update={
                "markup_removed": dict(sorted(removed.items())),
                "markup_characters_removed": sum(len(tag) * n for tag, n in removed.items()),
                "silence_intervals_dropped": silence,
            }
        ),
    )
