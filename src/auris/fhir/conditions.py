"""A3 condition rows: assessed, ruled out, and patient reported."""

from auris.assertions import AssertionBase, Temporality
from auris.fhir.common import Anchors, base, code_text, status_concept
from auris.fhir.contracts import fixed
from auris.fhir.models import BuildResult
from auris.terminology import ICD10CM, TerminologyIndex

DIAGNOSIS = "us-core-condition-encounter-diagnosis"
PROBLEM = "us-core-condition-problems-health-concerns"


def _condition(
    assertion: AssertionBase,
    identifier: str,
    anchors: Anchors,
    terminology: TerminologyIndex,
    profile: str,
    verification: str,
) -> BuildResult:
    if assertion.concept_text is None:
        return BuildResult(profile=profile)
    concept, reasons, flags = code_text(
        assertion.concept_text,
        ICD10CM,
        terminology,
        encounter_start=anchors.encounter["period"]["start"],
    )
    resource = base("Condition", identifier, profile)
    resource.update(
        {
            "code": concept,
            "category": [
                fixed(profile, "Condition.category:us-core")
                if profile == DIAGNOSIS
                else status_concept("condition-category", "problem-list-item")
            ],
            "subject": anchors.reference("patient"),
            "encounter": anchors.reference("encounter"),
            "verificationStatus": status_concept("condition-ver-status", verification),
        }
    )
    if verification != "refuted":
        status = {Temporality.CURRENT: "active", Temporality.RESOLVED: "resolved"}.get(
            assertion.temporality
        )
        if status:
            resource["clinicalStatus"] = status_concept("condition-clinical", status)
        if assertion.temporality == Temporality.FUTURE:
            reasons += ("condition_temporality_unclear",)
    return BuildResult(profile=profile, resource=resource, reasons=reasons, flags=flags)


def build_assessed(
    assertion: AssertionBase, identifier: str, anchors: Anchors, terminology: TerminologyIndex
) -> BuildResult:
    return _condition(assertion, identifier, anchors, terminology, DIAGNOSIS, "provisional")


def build_ruled_out(
    assertion: AssertionBase, identifier: str, anchors: Anchors, terminology: TerminologyIndex
) -> BuildResult:
    return _condition(assertion, identifier, anchors, terminology, DIAGNOSIS, "refuted")


def build_reported_condition(
    assertion: AssertionBase, identifier: str, anchors: Anchors, terminology: TerminologyIndex
) -> BuildResult:
    return _condition(assertion, identifier, anchors, terminology, PROBLEM, "unconfirmed")
