"""A3 affirmed allergies; denials are handled by the tier-zero ledger builder."""

from auris.assertions import AllergyStatementAssertion
from auris.fhir.common import Anchors, base, code_text, status_concept
from auris.fhir.models import BuildResult
from auris.terminology import RXNORM, TerminologyIndex

PROFILE = "us-core-allergyintolerance"


def build_allergy(
    assertion: AllergyStatementAssertion,
    identifier: str,
    anchors: Anchors,
    terminology: TerminologyIndex,
) -> BuildResult:
    if assertion.allergen_text is None:
        return BuildResult(profile=PROFILE, reasons=("allergen_not_stated",))
    concept, reasons, flags = code_text(
        assertion.allergen_text,
        RXNORM,
        terminology,
        encounter_start=anchors.encounter["period"]["start"],
    )
    resource = base("AllergyIntolerance", identifier, PROFILE)
    resource.update(
        {
            "code": concept,
            "patient": anchors.reference("patient"),
            "encounter": anchors.reference("encounter"),
            "clinicalStatus": status_concept("allergyintolerance-clinical", "active"),
            "verificationStatus": status_concept("allergyintolerance-verification", "unconfirmed"),
        }
    )
    if assertion.reaction_manifestation_text is not None:
        resource["reaction"] = [
            {"manifestation": [{"text": assertion.reaction_manifestation_text}]}
        ]
    return BuildResult(profile=PROFILE, resource=resource, reasons=reasons, flags=flags)
