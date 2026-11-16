"""Who is who: which speaker is the clinician.

A transparent score, not a model: clinicians ask more questions, say "you"
more than "I", and use clinical phrasing ("let me examine", "I'll prescribe");
patients describe themselves ("I've had", "my chest"). The highest-scoring
main speaker is the clinician and the next is the patient. When the two are
too close to call, both stay unknown and a person decides in review, where
speakers can be corrected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from auris.transcript import SpeakerRole

_YOU = re.compile(r"\b(?:you|your|you're|you've|you'd|you'll|yourself)\b", re.I)
_I = re.compile(r"\b(?:i|my|me|i'm|i've|i'd|i'll|myself|mine)\b", re.I)
_CLINICIAN = re.compile(
    r"\b(?:how can i help|what brings you|let me (?:have a look|examine|check|take)|"
    r"i(?:'ll| will| would| can|'d) "
    r"(?:prescribe|recommend|refer|examine|suggest|arrange|send|give you|start you)|"
    r"(?:any|do you have any) (?:allergies|medical conditions|other symptoms|medications?)|"
    r"have you (?:had|noticed|been|tried|taken|ever)|do you (?:smoke|drink|take|have)|"
    r"on a scale of|blood (?:pressure|test)s?|diagnos\w*|prescri\w+|"
    r"follow[- ]up|safety[- ]net|come back|any questions|"
    r"(?:twice|three times|once) (?:a|per) day|milligrams?|tablets? (?:a|per|every))\b",
    re.I,
)
_PATIENT = re.compile(
    r"\b(?:i've (?:had|been|got|noticed)|i have (?:had|been|got)|i (?:feel|felt|get|got)|"
    r"it (?:hurts|started|feels|gets)|my (?:chest|head|back|stomach|throat|leg|arm|knee|"
    r"wife|husband|mum|mom|dad|mother|father|son|daughter|partner|doctor|gp)|"
    r"i'm (?:worried|not sure|on|taking)|i take|thank you,? doctor|doctor)\b",
    re.I,
)
MAIN_SPEAKER_SHARE = 0.10
MIN_MARGIN = 0.15


@dataclass(frozen=True)
class SpeakerScore:
    speaker: str
    words: int
    score: float


def score(sentences: list[str]) -> float:
    text = " ".join(sentences)
    words = max(1, len(text.split()))
    questions = sum(sentence.rstrip().endswith("?") for sentence in sentences) / max(
        1, len(sentences)
    )
    pronouns = (len(_YOU.findall(text)) - len(_I.findall(text))) / words
    cues = (len(_CLINICIAN.findall(text)) - len(_PATIENT.findall(text))) / words
    return round(questions + 5 * pronouns + 20 * cues, 4)


def assign_roles(
    sentences: dict[str, list[str]],
) -> tuple[dict[str, SpeakerRole], list[SpeakerScore], float]:
    """Roles per speaker, the scores behind them, and the clinician margin."""
    total = sum(len(" ".join(lines).split()) for lines in sentences.values()) or 1
    scores = sorted(
        (
            SpeakerScore(speaker, len(" ".join(lines).split()), score(lines))
            for speaker, lines in sentences.items()
        ),
        key=lambda item: (-item.score, item.speaker),
    )
    roles = {item.speaker: SpeakerRole.UNKNOWN for item in scores}
    main = [item for item in scores if item.words / total >= MAIN_SPEAKER_SHARE]
    if len(main) < 2:
        return roles, scores, 0.0
    margin = round(main[0].score - main[1].score, 4)
    if margin >= MIN_MARGIN:
        roles[main[0].speaker] = SpeakerRole.CLINICIAN
        roles[main[1].speaker] = SpeakerRole.PATIENT
    return roles, scores, margin
