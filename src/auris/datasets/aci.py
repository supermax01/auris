"""ACI-Bench turn parser. Never repair or infer a speaker tag."""

import re

from auris.transcript import Transcript, TranscriptSegment, TranscriptSource

_ROLES = {"doctor": "clinician", "patient": "patient", "patient_guest": "companion"}
_TAG = re.compile(r"\[(doctor|patient|patient_guest)\]", re.IGNORECASE)
# The human reference includes this non-speaker annotation inside one turn.
_ANNOTATION = re.compile(r"\[\s*inaudible(?:\s+\d\d:\d\d:\d\d)?\s*\]", re.IGNORECASE)


def parse_turns(src: str) -> tuple[TranscriptSegment, ...]:
    tags = list(_TAG.finditer(src))
    if not tags:
        raise ValueError("ACI transcript requires a supported speaker tag")
    turns = []
    if src[: tags[0].start()].strip():
        # A source opening utterance has no tag. Preserve it without guessing.
        turns.append(("unknown", src[: tags[0].start()].strip()))
    for index, tag in enumerate(tags):
        end = tags[index + 1].start() if index + 1 < len(tags) else len(src)
        turns.append((tag[1].casefold(), src[tag.end() : end].strip()))
    result = []
    for index, (speaker, raw) in enumerate(turns):
        # Reject malformed/unknown tags anywhere, rather than assigning their
        # words to the preceding speaker. Preserve the known inaudible annotation.
        residual = _ANNOTATION.sub("", raw)
        if "[" in residual or "]" in residual:
            raise ValueError("malformed/unknown ACI speaker tag")
        result.append(
            TranscriptSegment(
                id=f"turn-{index + 1:04d}",
                speaker_cluster_id=speaker,
                role=_ROLES.get(speaker, "unknown"),
                text=raw,
                raw_text=raw,
                turn_number=index + 1,
            )
        )
    return tuple(result)


def adapt_aci(src: str, source: TranscriptSource) -> Transcript:
    return Transcript(
        schema_version="2",
        timing_mode="untimed",
        timing_source="none",
        segments=parse_turns(src),
        source=source,
    )
