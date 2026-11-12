"""A3 draft proposals preserve the stated dose, route and frequency."""

from auris.assertions import ExplicitNewOrderAssertion
from auris.evidence import AssertionEvidenceResult
from auris.fhir.common import Anchors, base, code_text
from auris.fhir.models import BuildResult
from auris.terminology import RXNORM, TerminologyIndex
from auris.transcript import Transcript

PROFILE = "us-core-medicationrequest"


def _instruction(
    assertion: ExplicitNewOrderAssertion,
    evidence: AssertionEvidenceResult,
    transcript: Transcript,
) -> str | None:
    """Join verbatim pieces in transcript order, merging overlapping spans.

    A dose field may already include route/frequency. Use source spans so
    these words appear once. Unlocatable pieces cannot be safely ordered;
    refuse the mapping rather than inventing or dropping an instruction.
    """
    positions = {segment.id: index for index, segment in enumerate(transcript.segments)}
    spans: list[tuple[int, int, int]] = []
    for piece in (assertion.dose_text, assertion.route_text, assertion.frequency_text):
        if piece is None:
            continue
        matches: list[tuple[int, int, int]] = []
        for item in evidence.items:
            aligned = item.aligned
            if aligned is None:
                continue
            offset = aligned.aligned_text.find(piece)
            if offset >= 0:
                start = aligned.char_start + offset
                matches.append((positions[item.segment_id], start, start + len(piece)))
        if not matches:
            raise ValueError("dosage_text_not_in_quote")
        spans.append(min(matches))
    merged: list[tuple[int, int, int]] = []
    for segment, start, end in sorted(spans):
        if merged and merged[-1][0] == segment and start <= merged[-1][2]:
            previous = merged[-1]
            merged[-1] = (segment, previous[1], max(previous[2], end))
        else:
            merged.append((segment, start, end))
    return " ".join(transcript.segments[s].text[start:end] for s, start, end in merged) or None


def build_order(
    assertion: ExplicitNewOrderAssertion,
    identifier: str,
    anchors: Anchors,
    terminology: TerminologyIndex,
    evidence: AssertionEvidenceResult,
    transcript: Transcript,
) -> BuildResult:
    if assertion.concept_text is None:
        return BuildResult(profile=PROFILE)
    try:
        instruction = _instruction(assertion, evidence, transcript)
    except ValueError:
        return BuildResult(profile=PROFILE, reasons=("dosage_text_not_in_quote",))
    concept, reasons, flags = code_text(
        assertion.concept_text,
        RXNORM,
        terminology,
        encounter_start=anchors.encounter["period"]["start"],
    )
    resource = base("MedicationRequest", identifier, PROFILE)
    resource.update(
        {
            "status": "draft",
            "intent": "proposal",
            "reportedBoolean": False,
            "medicationCodeableConcept": concept,
            "subject": anchors.reference("patient"),
            "encounter": anchors.reference("encounter"),
            "requester": anchors.reference("practitioner"),
            "authoredOn": anchors.encounter["period"]["start"],
        }
    )
    if instruction is not None:
        resource["dosageInstruction"] = [{"text": instruction}]
    return BuildResult(
        profile=PROFILE,
        resource=resource,
        reasons=reasons,
        flags=flags,
        notes=("authoredOn inherited from Encounter.period.start (in-visit order)",),
    )
