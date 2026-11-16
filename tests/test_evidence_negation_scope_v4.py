"""Adversarial suite for evidence-v4 (negation-scope v2, 2026-11-16).

Written with the rules, before the re-score. Every case where a denial applies
to the quoted words must be refused, including every v3 case and every known
v3 hole. All data is fictional.
"""

import pytest
from test_evidence_negation_scope import ACCEPTED as V3_ACCEPTED
from test_evidence_negation_scope import KNOWN_HOLES, REFUSED, _align

from auris.evidence import EvidenceFailure


def _v4(text, quote):
    return _align(text, quote, "evidence-v4")


NEW_REFUSED = [
    # Negative raising and other complement-taking words keep a pronoun inside.
    ("I don't think I have a fever", "I have a fever"),
    ("I can't say I've had chest pain", "I've had chest pain"),
    ("There's no evidence he has pneumonia", "he has pneumonia"),
    ("There's no evidence that he has pneumonia", "he has pneumonia"),
    ("The patient denies she has chest pain", "she has chest pain"),
    ("I'm not sure and I think I have asthma", "I think I have asthma"),
    ("I don't believe he smokes and he drinks", "he drinks"),
    # A clause after "but" that points back still denies what came before.
    ("I thought it was asthma but it's not", "asthma"),
    ("I thought it was asthma, but it's not asthma.", "asthma"),
    ("I had headaches but not anymore", "I had headaches"),
    ("They said pneumonia but it wasn't confirmed", "pneumonia"),
    ("I thought I had the flu but I don't think so", "I had the flu"),
    ("I used to smoke but no longer", "I used to smoke"),
    # A new clause that points back also denies what came before.
    ("I was told I have asthma and I don't think that's right", "I have asthma"),
    ("chest pain and I don't have that", "chest pain"),
    # Topicalised denial, no connector: the clause start is not a boundary.
    ("chest pain I don't have", "chest pain"),
    ("Allergies I don't have any", "Allergies"),
    # Lists after a cue, punctuated or not, with "and" but no new subject.
    (
        "denies fever chills night sweats weight loss nausea vomiting diarrhea constipation "
        "or shortness of breath",
        "shortness of breath",
    ),
    ("no fever and no chills and also night sweats", "night sweats"),
    # A leading "No," keeps its scope (v3's answer rule is removed).
    ("No, chest pain at all", "chest pain"),
    ("Uh, no, I think I'm otherwise OK.", "I think I'm otherwise OK"),
    # "yeah" is not a boundary.
    ("I've not had any, yeah, any fevers", "any fevers"),
    ("nothing yeah penicillin is fine", "penicillin"),
    # A denial after "but" can point back through a class word: no lexical rule
    # sees that amoxicillin is an antibiotic, so a later cue always reaches back.
    (
        "We could try amoxicillin, but I don't think you need an antibiotic.",
        "We could try amoxicillin",
    ),
    ("You could have an x-ray, but I don't think you need imaging.", "You could have an x-ray"),
    ("I have nausea but no vomiting", "I have nausea"),
    ("I take aspirin but not every day", "I take aspirin"),
    ("I have a cough but no fever", "I have a cough"),
    # Found by the blinded review: a new-clause rule (rejected) accepted this.
    (
        "they indicate the possibility of um a heart attack okay so i'm not saying you",
        "they indicate the possibility of um a heart attack",
    ),
]

# A new-clause rule would accept these; it failed the blinded review, so v4 refuses them.
RULE_B_ONLY = [
    ("no fever and I have a cough", "I have a cough"),
    ("there's no swelling so I'm going to order an x-ray", "I'm going to order an x-ray"),
    ("I've been coughing and I don't have a fever", "I've been coughing"),
    ("no yeah I have a cough", "I have a cough"),
    (
        "No, she's been completely fine, and I've been isolating myself from her.",
        "I've been isolating myself from her",
    ),
]

NEW_ACCEPTED = [
    (
        "I never had any nausea or vomiting but my wife checked my temperature",
        "my wife checked my temperature",
    ),
    ("It's nothing to worry about, but you've got some chest pain", "you've got some chest pain"),
    ("No allergies apart from penicillin", "penicillin"),
]


@pytest.mark.parametrize(("text", "quote"), REFUSED + KNOWN_HOLES + NEW_REFUSED + RULE_B_ONLY)
def test_v4_refuses_every_denial_that_applies(text, quote):
    result = _v4(text, quote)
    assert result.failure is EvidenceFailure.OMITTED_NEGATION_CONTEXT, (text, quote)


@pytest.mark.parametrize(("text", "quote"), NEW_REFUSED)
def test_v2_refuses_the_new_cases_too(text, quote):
    assert _align(text, quote, "evidence-v2").failure is EvidenceFailure.OMITTED_NEGATION_CONTEXT


@pytest.mark.parametrize(
    ("text", "quote"),
    [
        case
        for case in V3_ACCEPTED
        if case not in RULE_B_ONLY + NEW_REFUSED and "otherwise OK" not in case[1]
    ]
    + NEW_ACCEPTED,
)
def test_v4_accepts_a_denial_in_another_clause(text, quote):
    assert _v4(text, quote).ok, (text, quote)
    assert _align(text, quote, "evidence-v2").failure is EvidenceFailure.OMITTED_NEGATION_CONTEXT


def test_a_quote_with_its_own_cue_is_never_refused_for_this_reason():
    assert _v4("I have no fever but a cough", "no fever").ok
