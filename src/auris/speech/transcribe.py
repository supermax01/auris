"""Audio files to a timed, speaker-labelled :class:`Transcript`.

Two ways in:

* **Tracks** — one file per speaker (a headset each, or a telemedicine
  recording with separate channels). Each file is one speaker, so nothing
  has to be guessed about who spoke when; the role is given (``clinician=``)
  or scored.
* **Room** — one file from a single microphone, the default setup.
  Speakers are found by voice (:mod:`auris.speech.diarize`) and
  roles are scored (:mod:`auris.speech.roles`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from auris.speech import audio as audio_io
from auris.speech.asr import AsrSegment, Transcriber, Word
from auris.speech.roles import assign_roles
from auris.transcript import SpeakerRole, Transcript, TranscriptSegment, WordTiming

if TYPE_CHECKING:
    import numpy as np

    from auris.config import AsrSegmentation
    from auris.speech.diarize import SpeakerEmbedder
    from auris.speech.spelling import DrugSpeller

UNATTRIBUTED = "unattributed"
# Readable labels; the fingerprint below is what decides whether a saved transcript is reused.
PIPELINE_VERSIONS = {"tracks": "tracks-v1", "room": "room-v2"}
_CODE = ("asr.py", "audio.py", "diarize.py", "roles.py", "spelling.py", "transcribe.py", "turns.py")
_LIBRARIES = ("faster-whisper", "ctranslate2", "onnxruntime", "av", "speechbrain", "torch")


def pipeline_fingerprint() -> dict[str, str]:
    """What made a transcript: a hash of the speech code and the installed library versions.

    Any change to either (even one a person forgot to label) makes saved
    transcripts stale, so they are made again rather than silently reused.
    """
    import hashlib
    from importlib import metadata

    folder = Path(__file__).parent
    code = hashlib.sha256()
    for name in _CODE:
        code.update(name.encode() + b"\0" + (folder / name).read_bytes())
    versions = {}
    for library in _LIBRARIES:
        try:
            versions[library] = metadata.version(library)
        except metadata.PackageNotFoundError:
            versions[library] = "not installed"
    return {"code_sha256": code.hexdigest(), **versions}


SEGMENT_GAP_SECONDS = 1.5


@dataclass(frozen=True)
class Track:
    path: Path
    role: SpeakerRole | None = None


@dataclass
class Piece:
    """A run of words from one speaker."""

    speaker: str
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(word.text for word in self.words).strip()


def _split(segment: AsrSegment, speaker_of: list[str | None]) -> list[Piece]:
    """Split one ASR segment where the speaker changes or a long pause falls."""
    pieces: list[Piece] = []
    for word, speaker in zip(segment.words, speaker_of, strict=True):
        name = speaker or UNATTRIBUTED
        last = pieces[-1] if pieces else None
        if (
            last is None
            or last.speaker != name
            or word.start - last.words[-1].end > SEGMENT_GAP_SECONDS
        ):
            pieces.append(Piece(name))
        pieces[-1].words.append(word)
    return pieces


def _timings(words: list[Word]) -> tuple[WordTiming, ...]:
    """Word timings made monotonic (ASR word boundaries are estimates)."""
    result: list[WordTiming] = []
    start_floor = end_floor = 0.0
    for word in words:
        start = max(round(word.start, 3), start_floor)
        end = max(round(word.end, 3), end_floor, start)
        result.append(WordTiming(word=word.text.strip(), start_seconds=start, end_seconds=end))
        start_floor, end_floor = start, end
    return tuple(result)


def build_transcript(pieces: list[Piece], roles: dict[str, SpeakerRole]) -> Transcript:
    ordered = sorted(
        (piece for piece in pieces if piece.text),
        key=lambda piece: (piece.words[0].start, piece.words[-1].end, piece.speaker),
    )
    segments = []
    for number, piece in enumerate(ordered, 1):
        timings = _timings(piece.words)
        segments.append(
            TranscriptSegment(
                id=f"s{number}",
                speaker_cluster_id=piece.speaker,
                role=roles.get(piece.speaker, SpeakerRole.UNKNOWN),
                start_seconds=timings[0].start_seconds,
                end_seconds=timings[-1].end_seconds,
                text=piece.text,
                words=timings,
            )
        )
    return Transcript(segments=tuple(segments))


def _role_report(pieces: list[Piece], roles: dict[str, SpeakerRole], given: bool) -> dict[str, Any]:
    sentences: dict[str, list[str]] = {}
    for piece in pieces:
        if piece.speaker != UNATTRIBUTED and piece.text:
            sentences.setdefault(piece.speaker, []).append(piece.text)
    if given:
        return {
            "method": "given",
            "speakers": [
                {"speaker": s, "role": roles[s].value, "words": len(" ".join(t).split())}
                for s, t in sorted(sentences.items())
            ],
        }
    scored, scores, margin = assign_roles(sentences)
    roles.update(scored)
    return {
        "method": "roles-score-v1",
        "margin": margin,
        "decided": any(role == SpeakerRole.CLINICIAN for role in scored.values()),
        "speakers": [
            {
                "speaker": i.speaker,
                "role": scored[i.speaker].value,
                "words": i.words,
                "score": i.score,
            }
            for i in scores
        ],
    }


def _shaped(
    transcript: Transcript,
    report: dict,
    speller: DrugSpeller | None,
    segmentation: AsrSegmentation | None,
) -> Transcript:
    """Turns joined (when on), then misheard drug names respelled (when on)."""
    if segmentation is not None and segmentation.join_turns:
        from auris.speech.turns import join_turns

        transcript, report["turns_joined"] = join_turns(
            transcript, gap_seconds=segmentation.gap_seconds, max_seconds=segmentation.max_seconds
        )
    return _respelled(transcript, report, speller)


def _respelled(transcript: Transcript, report: dict, speller: DrugSpeller | None) -> Transcript:
    """Misheard drug names respelled (when spelling is on); the changes go in the report."""
    if speller is None:
        return transcript
    from auris.speech.spelling import correct_transcript

    transcript, corrections = correct_transcript(transcript, speller)
    report["spelling_corrections"] = corrections
    return transcript


def transcribe_tracks(
    tracks: list[Track],
    transcriber: Transcriber,
    speller: DrugSpeller | None = None,
    segmentation: AsrSegmentation | None = None,
) -> tuple[Transcript, dict]:
    if len({track.role is None for track in tracks}) > 1:
        # Scoring some tracks could overwrite a role the person gave (or leave it unknown).
        raise ValueError("give a role for every track (ROLE=PATH) or for none")
    started = time.monotonic()
    pieces: list[Piece] = []
    seconds = 0.0
    names: dict[str, SpeakerRole | None] = {}
    for number, track in enumerate(tracks, 1):
        name = track.role.value if track.role else f"track{number}"
        if name in names:
            name = f"{name}{number}"
        names[name] = track.role
        samples = audio_io.load(track.path)
        seconds = max(seconds, audio_io.seconds(samples))
        for segment in transcriber.transcribe(samples):
            pieces.extend(_split(segment, [name] * len(segment.words)))
    given = all(role is not None for role in names.values())
    roles: dict[str, SpeakerRole] = {n: r for n, r in names.items() if r is not None}
    report = {
        "mode": "tracks",
        "audio_seconds": round(seconds, 2),
        "roles": _role_report(pieces, roles, given),
    }
    transcript = _shaped(build_transcript(pieces, roles), report, speller, segmentation)
    report["processing_seconds"] = round(time.monotonic() - started, 2)
    return transcript, report


def transcribe_room(
    samples: np.ndarray,
    transcriber: Transcriber,
    embedder: SpeakerEmbedder,
    *,
    speakers: int = 2,
    speller: DrugSpeller | None = None,
    segmentation: AsrSegmentation | None = None,
) -> tuple[Transcript, dict]:
    from auris.speech.diarize import diarize

    started = time.monotonic()
    segments = transcriber.transcribe(samples)
    asr_seconds = time.monotonic() - started
    words = [word for segment in segments for word in segment.words]
    labels = diarize(samples, words, embedder, speakers=speakers)
    speaker_of = [None if label is None else f"speaker{label + 1}" for label in labels]
    pieces: list[Piece] = []
    position = 0
    for segment in segments:
        count = len(segment.words)
        pieces.extend(_split(segment, speaker_of[position : position + count]))
        position += count
    roles: dict[str, SpeakerRole] = {}
    report = {
        "mode": "room",
        "speakers_requested": speakers,
        "audio_seconds": round(audio_io.seconds(samples), 2),
        "unattributed_words": sum(label is None for label in labels),
        "roles": _role_report(pieces, roles, False),
    }
    transcript = _shaped(build_transcript(pieces, roles), report, speller, segmentation)
    report["asr_seconds"] = round(asr_seconds, 2)
    report["processing_seconds"] = round(time.monotonic() - started, 2)
    return transcript, report
