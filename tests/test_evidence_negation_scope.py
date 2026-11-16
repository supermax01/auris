"""Adversarial suite for negation scope in the evidence check (evidence-v3).

Committed with the rules, before any re-score. Every case where a denial
applies to the quoted words must be refused; the accepted cases are denials
in another clause. All data is fictional.
"""

import pytest

from auris.assertions import EvidenceItem
from auris.evidence import EvidenceFailure, EvidenceItemResult, align_evidence_item
from auris.transcript import Transcript, TranscriptSegment


def _align(text: str, quote: str, version: str = "evidence-v3") -> EvidenceItemResult:
    segment = TranscriptSegment(
        id="s1",
        speaker_cluster_id="spk-a",
        role="patient",
        start_seconds=0.0,
        end_seconds=10.0,
        text=text,
    )
    return align_evidence_item(
        EvidenceItem(segment_id="s1", quote=quote),
        Transcript(segments=(segment,)),
        checker_version=version,
    )


REFUSED = [
    # Lists keep the scope open; there is no word window.
    ("No fever, chills, night sweats or cough.", "cough"),
    ("no fever chills night sweats or cough", "cough"),
    ("no fever and chills", "chills"),
    # Words that would break a terminator list stay inside the scope.
    ("His asthma is not very well controlled.", "well controlled"),
    ("She has not yet been diagnosed with diabetes.", "been diagnosed with diabetes"),
    ("There are no family members who have diabetes.", "have diabetes"),
    ("it's not so bad", "bad"),
    ("I'm not actually in pain", "in pain"),
    ("I've never had a seizure.", "had a seizure"),
    ("He denies chest pain.", "chest pain"),
    ("I don't have any chest pain", "any chest pain"),
    ("I haven't been coughing", "been coughing"),
    # A denial after what it denies, in the same clause.
    ("Chest X-ray negative.", "Chest X-ray"),
    ("Pneumonia was ruled out.", "Pneumonia"),
    ("Fever denied.", "Fever"),
    ("Penicillin allergy: no.", "penicillin allergy"),
    ("headaches not really", "headaches"),
    # Fillers do not end a scope.
    ("no um um fever", "fever"),
    # A terminator inside the quote does not shield it from a cue just before it.
    ("not painful but swollen", "painful but swollen"),
    ("no fever but a cough", "fever"),
    # Answer "no": only first-token "no" with a comma, and only that cue.
    ("No, no chest pain.", "chest pain"),
    ("No chest pain.", "chest pain"),
    ("So no, fever is there", "fever is there"),
    ("Okay no, fever", "fever"),
    # The first occurrence is used; a later, safe one is never preferred.
    ("No penicillin allergy. Penicillin allergy was discussed.", "penicillin allergy"),
    # Collapsed repeats cannot bring in a cue the quote lacks.
    ("No pain no pain.", "Um pain"),
]


# Found by an independent review after the re-score preview (2026-11-14). v3 accepts
# them, which is why v3 is parked and evidence-v2 stays the checker. Kept as strict
# expected failures so a future version must close them.
KNOWN_HOLES = [
    ("No, chest pain at all", "chest pain"),
    ("No, fevers or chills.", "fevers"),
    ("I've not had any, yeah, any fevers", "any fevers"),
    ("penicillin, yeah, no allergy to that", "penicillin"),
    ("I'm not allergic to anything yeah penicillin is fine", "penicillin"),
]


@pytest.mark.parametrize(("text", "quote"), KNOWN_HOLES)
@pytest.mark.xfail(strict=True, reason="known evidence-v3 hole; v3 is parked")
def test_known_v3_holes_are_refused(text, quote):
    assert not _align(text, quote).ok
    assert not _align(text, quote, "evidence-v2").ok  # v2 refuses them


@pytest.mark.parametrize(("text", "quote"), KNOWN_HOLES)
def test_the_current_checker_refuses_the_known_v3_holes(text, quote):
    assert _align(text, quote, "evidence-v2").failure is EvidenceFailure.OMITTED_NEGATION_CONTEXT


@pytest.mark.parametrize(("text", "quote"), REFUSED)
def test_a_denial_that_applies_to_the_quote_refuses_it(text, quote):
    result = _align(text, quote)
    assert not result.ok, (text, quote)
    assert result.failure is EvidenceFailure.OMITTED_NEGATION_CONTEXT


ACCEPTED = [
    ("no vomiting but I'm pretty sweaty", "I'm pretty sweaty"),
    (
        "No, I've been training for a race, but my weight has been relatively stable.",
        "my weight has been relatively stable",
    ),
    (
        "No, she's been completely fine, and I've been isolating myself from her.",
        "I've been isolating myself from her",
    ),
    ("Uh, no, I think I'm otherwise OK.", "I think I'm otherwise OK"),
    ("I have a cough but no fever", "I have a cough"),
    ("no allergies except penicillin", "penicillin"),
    ("I don't have a fever however my throat is sore", "my throat is sore"),
    ("no yeah I have a cough", "I have a cough"),
]


@pytest.mark.parametrize(("text", "quote"), ACCEPTED)
def test_a_denial_in_another_clause_no_longer_refuses(text, quote):
    assert _align(text, quote).ok, (text, quote)
    # The current check refuses every one of these: the change is real.
    assert _align(text, quote, "evidence-v2").failure is (EvidenceFailure.OMITTED_NEGATION_CONTEXT)


@pytest.mark.parametrize(("text", "quote"), REFUSED)
def test_v3_refuses_whatever_v2_refuses_on_the_adversarial_cases(text, quote):
    assert not _align(text, quote, "evidence-v2").ok


def test_quotes_carrying_their_own_cue_and_separate_sentences_are_unchanged():
    assert _align("I have no fever but a cough.", "no fever").ok
    assert _align("No fever. I have a cough.", "I have a cough").ok
    assert _align("No fever; I have a cough.", "I have a cough").ok
