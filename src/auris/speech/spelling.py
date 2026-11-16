"""Drug-name spelling: a misheard drug name becomes the ingredient it sounds like.

Whisper often hears a drug name right but spells it wrong ("fruzamide",
"lizinapril", "loratidine"). Those words are not English and not a drug
name, so coding and evidence checks treat them as unknown. A word is
replaced only when all of these hold:

* it is at least six letters, not a known drug name, and not English (a
  pinned public-domain word list, with plain plural and verb endings);
* it sounds like an RxNorm ingredient: their sound skeletons (spelling
  variants for one sound merged, vowels merged, repeats collapsed) differ by
  at most one letter, they start with the same sound, and the letters differ
  by at most a third of the name (at least 2 allowed);
* exactly one ingredient is closest; a tie changes nothing.

The heard text is kept (``raw_text``) and every change is listed in the
transcript report, so a person can always see what was heard. A wrong
replacement is a confident wrong drug name; medication facts already need
clinician approval, and the speech evaluation counts any drug name nobody
said ("drug words invented").
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache
from typing import TYPE_CHECKING

from auris.transcript import Transcript, TranscriptSegment, WordTiming

if TYPE_CHECKING:
    from pathlib import Path

    from auris.config import FrozenConfig

VERSION = "drug-spelling-v2"
MIN_LETTERS = 6
MAX_SKELETON_DISTANCE = 1
_LETTERS = re.compile(r"[A-Za-z]+")
_ENDINGS = ("ies", "es", "s", "ed", "ing", "ly")
# One sound, several spellings: merged before comparing.
_SOUNDS = (("ph", "f"), ("th", "t"), ("ck", "k"), ("qu", "kw"), ("x", "ks"), ("z", "s"))


def skeleton(word: str) -> str:
    """The word reduced to its sound outline."""
    text = word.lower()
    for spelling, sound in _SOUNDS:
        text = text.replace(spelling, sound)
    text = re.sub(r"c(?=[eiy])", "s", text).replace("c", "k").replace("y", "i")
    text = re.sub(r"[aeiou]", "a", text)
    return re.sub(r"(.)\1+", r"\1", text)


def levenshtein(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def _letters_allowed(target: str) -> int:
    return max(2, len(target) // 3)


class DrugSpeller:
    """Corrects single misheard words to the one ingredient name they sound like."""

    def __init__(self, ingredients: Iterable[str], english: Iterable[str], known: Iterable[str]):
        self.english = frozenset(word.lower() for word in english)
        # Every word of every known name: "ethinyl" in "ethinyl estradiol" is a drug word too.
        self.known = frozenset(token for name in known for token in name.lower().split())
        self.targets = tuple(
            sorted(
                {t for t in ingredients if t.isascii() and t.isalpha() and len(t) >= MIN_LETTERS}
            )
        )
        self._by_sound: dict[str, list[tuple[str, str]]] = {}
        for target in self.targets:
            outline = skeleton(target)
            self._by_sound.setdefault(outline[0], []).append((target, outline))
        self.correct = lru_cache(maxsize=None)(self._correct)

    def is_english(self, word: str) -> bool:
        if word in self.english:
            return True
        for ending in _ENDINGS:
            if word.endswith(ending) and len(word) > len(ending) + 2:
                stem = word[: -len(ending)]
                if stem in self.english or (ending == "ies" and stem + "y" in self.english):
                    return True
                if ending in ("ed", "ing") and (stem + "e" in self.english):
                    return True
        return False

    def is_known(self, word: str) -> bool:
        """A word of a known drug name, or one with a plural ending ("ibuprofens")."""
        return word in self.known or any(
            word.endswith(ending) and word[: -len(ending)] in self.known for ending in ("s", "es")
        )

    def _correct(self, word: str) -> str | None:
        """The ingredient ``word`` (lower-case letters) was meant to be, or None."""
        if len(word) < MIN_LETTERS or self.is_known(word) or self.is_english(word):
            return None
        outline = skeleton(word)
        scored = []
        for target, target_outline in self._by_sound.get(outline[0], ()):
            sound = levenshtein(outline, target_outline)
            if sound > MAX_SKELETON_DISTANCE:
                continue
            letters = levenshtein(word, target)
            if letters <= _letters_allowed(target):
                scored.append(((sound, letters), target))
        if not scored:
            return None
        scored.sort()
        if len(scored) > 1 and scored[1][0] == scored[0][0]:
            return None  # two equally close names: leave it for a person
        return scored[0][1]


def _respell(text: str, speller: DrugSpeller, changes: list[tuple[str, str]]) -> str:
    def replace(match: re.Match[str]) -> str:
        heard = match.group(0)
        target = speller.correct(heard.lower())
        if target is None:
            return heard
        changes.append((heard, target))
        return target.capitalize() if heard[0].isupper() else target

    return _LETTERS.sub(replace, text)


def correct_transcript(
    transcript: Transcript, speller: DrugSpeller
) -> tuple[Transcript, list[dict]]:
    """The transcript with misheard drug names respelled, and the list of changes."""
    segments: list[TranscriptSegment] = []
    corrections: list[dict] = []
    for segment in transcript.segments:
        changes: list[tuple[str, str]] = []
        text = _respell(segment.text, speller, changes)
        if not changes:
            segments.append(segment)
            continue
        words = segment.words
        if words is not None:
            words = tuple(
                WordTiming(
                    word=_respell(w.word, speller, []),
                    start_seconds=w.start_seconds,
                    end_seconds=w.end_seconds,
                )
                for w in words
            )
        segments.append(
            segment.model_copy(
                update={
                    "text": text,
                    "words": words,
                    "raw_text": segment.raw_text if segment.raw_text is not None else segment.text,
                }
            )
        )
        corrections += [
            {"segment": segment.id, "heard": heard, "corrected": target}
            for heard, target in changes
        ]
    if not corrections:
        return transcript, corrections
    # Version 2 keeps raw_text when saved (version 1 drops it); validated, not just copied.
    respelled = Transcript.model_validate(
        {
            **transcript.model_dump(),
            "schema_version": "2",
            "segments": [segment.model_dump() for segment in segments],
        }
    )
    return respelled, corrections


def spelling_report(config: FrozenConfig) -> dict | None:
    """What a transcript records about spelling (None when off): the method, the
    word list and the RxNorm release, so a change in any of them is never reused."""
    spelling = config.asr.spelling
    if not spelling.enabled:
        return None
    assert spelling.word_list is not None
    return {
        "version": VERSION,
        "word_list_sha256": spelling.word_list.sha256,
        "rxnorm_release": config.terminology.rxnorm.release,
        "rxnorm_sha256": config.terminology.rxnorm.sha256,
    }


def load_speller(config: FrozenConfig, cache: Path, index_path: Path | None = None) -> DrugSpeller:
    """The speller from the pinned word list and the RxNorm index (ingredients only)."""
    from auris.setup import terminology
    from auris.setup.asr import ensure_word_list
    from auris.terminology.index import TerminologyIndex
    from auris.terminology.lexicon import DrugLexicon
    from auris.terminology.text import RXNORM

    pin = config.asr.spelling.word_list
    if pin is None:
        raise ValueError("drug-name spelling has no word list")
    english = ensure_word_list(pin, cache, fetch=False).read_text(encoding="utf-8").split()
    path = index_path or terminology.default_cache_dir() / "index.sqlite"
    if not path.is_file():
        raise ValueError("terminology index missing; run auris setup terminology")
    with TerminologyIndex(path) as index:
        if index.metadata.get("rxnorm") != config.terminology.rxnorm.model_dump(mode="json"):
            # The provenance names the pinned release; the speller must have read it.
            raise ValueError(
                "drug-name spelling needs the pinned RxNorm index; rerun auris setup terminology"
            )
        ingredients = [
            row[0]
            for row in index.connection.execute(
                "SELECT DISTINCT normalised FROM terms WHERE system=? AND tty='IN'", (RXNORM,)
            )
        ]
        known = DrugLexicon.from_index(index).names
    return DrugSpeller(ingredients, english, known)
