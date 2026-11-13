"""Conservative exact-name normalisation; never fuzzy clinical coding."""

import re
import unicodedata

RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
ICD10CM = "http://hl7.org/fhir/sid/icd-10-cm"


def normalise(text: str) -> str:
    # Numeric punctuation is significant: 1.5 must not match 1 5 or 15.
    text = unicodedata.normalize("NFC", text).casefold()
    return " ".join(re.findall(r"[+-]?\d+(?:[./]\d+)*|[^\W\d_]+", text))


UK_US_NAMES = {
    "paracetamol": "acetaminophen",
    "salbutamol": "albuterol",
    "adrenaline": "epinephrine",
    "frusemide": "furosemide",
    "glyceryl trinitrate": "nitroglycerin",
    "lignocaine": "lidocaine",
    # The target is a MIN in CPC, outside the permitted auto-code TTYs.
    # Keep the standard synonym, but do not reduce it to one ingredient.
    "co amoxiclav": "amoxicillin / clavulanate",
}

DRUG_FAMILIES = frozenset(
    normalise(name)
    for name in (
        "penicillin",
        "penicillins",
        "sulfa",
        "sulfas",
        "sulfonamide",
        "sulfonamides",
        "sulphonamide",
        "sulphonamides",
        "cephalosporin",
        "cephalosporins",
        "NSAID",
        "NSAIDs",
        "nonsteroidal anti-inflammatory drugs",
        "non-steroidal anti-inflammatory drugs",
        "opioid",
        "opioids",
        "opiate",
        "opiates",
        "statin",
        "statins",
        "ACE inhibitor",
        "ACE inhibitors",
        "angiotensin converting enzyme inhibitors",
        "beta-blocker",
        "beta-blockers",
        "antibiotic",
        "antibiotics",
        "macrolide",
        "macrolides",
        "tetracyclines",
        "fluoroquinolones",
        "aminoglycosides",
        "corticosteroids",
        "benzodiazepines",
        "calcium channel blockers",
        "angiotensin receptor blockers",
        "ARBs",
    )
)


def drug_name(text: str) -> str:
    name = normalise(text)
    return normalise(UK_US_NAMES.get(name, name))
