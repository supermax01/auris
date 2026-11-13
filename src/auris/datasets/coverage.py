"""Model-free candidate counts, never clinical annotation or performance estimates."""

import re
from collections import Counter
from collections.abc import Iterable, Mapping

from auris.evidence import has_negation, sentence_texts
from auris.terminology.index import TerminologyIndex
from auris.terminology.text import RXNORM, UK_US_NAMES, normalise
from auris.transcript import Transcript

CATEGORIES = (
    "vital_sign",
    "symptom",
    "clinician_assessed_problem",
    "clinician_ruled_out_problem",
    "patient_reported_condition",
    "explicit_new_order",
    "reported_medication",
    "allergy_statement",
    "allergy_denial",
    "family_history_cue",
    "drug_name_mention",
    "dosed_order_candidate",
)
_NUMBER = (
    r"(?:\d+(?:\.\d+)?|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|"
    r"sixty|seventy|eighty|ninety|hundred)"
)
_VITALS = {
    "blood_pressure": r"blood pressure|\bbp\b",
    "heart_rate": r"heart rate|\bpulse\b",
    "respiratory_rate": r"respiratory rate|breathing rate|respirations",
    "temperature": r"temperature",
    "oxygen_saturation": r"oxygen saturation|\bsats\b|\bspo2\b",
    "weight": r"\bweight\b|\bweigh(?:s|ed)?\b",
    "height": r"\bheight\b|\btall\b",
}
_ALLERGY = re.compile(r"\ballerg(?:y|ies|ic)\b", re.I)
_RULED = re.compile(
    r"\brul(?:e|ed|ing) out\b|\bno evidence of\b|\bnot (?:a |an )?(?:case of|consistent with)\b",
    re.I,
)
_FAMILY = re.compile(
    r"\bfamily history\b|\b(?:my|your|his|her|their) "
    r"(?:mother|father|sister|brother|parent|grandmother|grandfather)\b",
    re.I,
)
_ORDER = re.compile(r"\bprescrib\w*\b|\bstart\b|\bgive you\b|\bput you on\b|\brecommend\b", re.I)
_DOSE = re.compile(
    rf"\b{_NUMBER}\s*(?:mg|mcg|g|ml|milligrams?|micrograms?|grams?|millilitres?|milliliters?|tablets?|puffs?|units?)\b",
    re.I,
)


class DrugLexicon:
    """Longest exact token sequence at each position; no fuzzy coding or inference."""

    def __init__(self, names: Iterable[str]) -> None:
        terms = {normalise(name) for name in names} - {""}
        terms.update(alias for alias, target in UK_US_NAMES.items() if normalise(target) in terms)
        self.names = tuple(sorted(terms))
        self.by_first: dict[str, list[tuple[str, ...]]] = {}
        for name in self.names:
            tokens = tuple(name.split())
            self.by_first.setdefault(tokens[0], []).append(tokens)
        for values in self.by_first.values():
            values.sort(key=lambda value: (-len(value), value))

    @classmethod
    def from_index(cls, index: TerminologyIndex) -> "DrugLexicon":
        return cls(
            row[0]
            for row in index.connection.execute(
                "SELECT DISTINCT normalised FROM terms WHERE system=?", (RXNORM,)
            )
        )

    def count(self, text: str) -> int:
        tokens = tuple(normalise(text).split())
        position = count = 0
        while position < len(tokens):
            match = next(
                (
                    name
                    for name in self.by_first.get(tokens[position], ())
                    if tokens[position : position + len(name)] == name
                ),
                None,
            )
            count += int(match is not None)
            position += len(match) if match else 1
        return count


def count_candidates(transcript: Transcript, drugs: DrugLexicon) -> Counter[str]:
    counts: Counter[str] = Counter(dict.fromkeys(CATEGORIES, 0))
    for segment in transcript.segments:
        for sentence in sentence_texts(segment.text):
            text = sentence.casefold().replace("’", "'")
            mentions = drugs.count(text)
            family = bool(_FAMILY.search(text))
            ruled = bool(_RULED.search(text))
            counts["family_history_cue"] += family
            counts["drug_name_mention"] += mentions
            # Numeric vital cue within <= 8 intervening whitespace tokens, either order.
            for pattern in _VITALS.values():
                if re.search(
                    rf"(?:{pattern})(?:\s+\S+){{0,8}}\s+{_NUMBER}\b|\b{_NUMBER}(?:\s+\S+){{0,8}}\s+(?:{pattern})",
                    text,
                ):
                    counts["vital_sign"] += 1
            if re.search(
                r"\b(?:pain|ache|aches|cough|nausea|vomiting|dizzy|dizziness|breathless|itch|itching)\b",
                text,
            ):
                counts["symptom"] += 1
            if _ALLERGY.search(text):
                counts["allergy_denial" if has_negation(text) else "allergy_statement"] += 1
            if segment.role == "clinician":
                counts["clinician_ruled_out_problem"] += ruled
                if (
                    not ruled
                    and not family
                    and re.search(
                        r"\byou (?:have|have got)\b|\bdiagnos\w*\b|\bsounds like\b|\blikely\b", text
                    )
                ):
                    counts["clinician_assessed_problem"] += 1
                if mentions and _ORDER.search(text) and not has_negation(text) and not family:
                    counts["explicit_new_order"] += 1
                    counts["dosed_order_candidate"] += bool(_DOSE.search(text))
            if (
                segment.role == "patient"
                and not family
                and re.search(
                    r"\bi (?:have|have got|suffer from)\b|\bi've got\b|"
                    r"\bdiagnosed\b|\bhistory of\b",
                    text,
                )
            ):
                counts["patient_reported_condition"] += 1
            if (
                mentions
                and re.search(r"\b(?:take|taking|using|on|continue|continuing)\b", text)
                and not _ORDER.search(text)
            ):
                counts["reported_medication"] += 1
    return counts


def aggregate(corpus: Mapping[str, Transcript], drugs: DrugLexicon) -> dict[str, dict[str, int]]:
    totals: Counter[str] = Counter(dict.fromkeys(CATEGORIES, 0))
    encounters: Counter[str] = Counter(dict.fromkeys(CATEGORIES, 0))
    for transcript in corpus.values():
        counts = count_candidates(transcript, drugs)
        totals.update(counts)
        encounters.update({key: int(value > 0) for key, value in counts.items()})
    return {key: {"candidates": totals[key], "encounters": encounters[key]} for key in CATEGORIES}
