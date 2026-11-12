"""A3 explicit new orders are draft proposals with verbatim dose text only."""

from auris.assertions import ExplicitNewOrderAssertion
from auris.fhir.common import Anchors, base, code_text
from auris.fhir.models import BuildResult
from auris.terminology import RXNORM, TerminologyIndex

PROFILE = "us-core-medicationrequest"


def build_order(
    assertion: ExplicitNewOrderAssertion,
    identifier: str,
    anchors: Anchors,
    terminology: TerminologyIndex,
) -> BuildResult:
    if assertion.concept_text is None:
        return BuildResult(profile=PROFILE)
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
    if assertion.dose_text is not None:
        resource["dosageInstruction"] = [{"text": assertion.dose_text}]
    return BuildResult(
        profile=PROFILE,
        resource=resource,
        reasons=reasons,
        flags=flags,
        notes=("authoredOn inherited from Encounter.period.start (in-visit order)",),
    )
