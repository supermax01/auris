"""Freeze scoring projections alongside complete production diagnostics."""

from typing import Any

from auris.assertions import AssertionBase, EvidenceItem
from auris.eval.gold import Code, Fact, Span, digest
from auris.fhir.models import ExportState, MappingOutcome, ValidationLanes, resource_digest


class Prediction(Fact):
    id: str
    lanes: ValidationLanes
    export_state: ExportState
    reviewed: bool = False
    cited_segment_ids: tuple[str, ...] = ()
    quoted_evidence: tuple[EvidenceItem, ...] = ()
    review_reasons: tuple[str, ...] = ()
    resource: dict[str, Any] | None = None


def record_quantities(resource: dict[str, Any] | None) -> tuple[dict[str, Any], ...]:
    """Read frozen FHIR quantities independently; BP is systolic then diastolic."""
    if not resource or resource.get("resourceType") != "Observation":
        return ()
    if "component" in resource:
        quantities = []
        for code in ("8480-6", "8462-4"):
            components = [
                c
                for c in resource["component"]
                if any(
                    v.get("system") == "http://loinc.org" and v.get("code") == code
                    for v in c.get("code", {}).get("coding", [])
                )
            ]
            # Missing or duplicate components cannot constitute a strict pair.
            quantities.append(
                components[0].get("valueQuantity", {}) if len(components) == 1 else {}
            )
        return tuple(quantities)
    return (resource["valueQuantity"],) if "valueQuantity" in resource else ()


def freeze_prediction(assertion: AssertionBase, outcome: MappingOutcome) -> Prediction:
    data = assertion.model_dump(mode="json")
    data["evidence"] = [
        Span(
            segment_id=a.segment_id,
            char_start=a.char_start,
            char_end=a.char_end,
            sha256=digest(a.aligned_text.encode()),
        )
        for item in outcome.evidence.items
        if (a := item.aligned)
    ]
    data["cited_segment_ids"] = tuple(item.segment_id for item in assertion.evidence)
    resource = outcome.resource or {}
    coding_field = (
        "medicationCodeableConcept"
        if resource.get("resourceType") == "MedicationRequest"
        else "code"
    )
    # Only standard code-system identifiers are shortened. This is URI identity,
    # not a concept lexicon; equivalence still comes exclusively from gold.
    systems = {
        "http://www.nlm.nih.gov/research/umls/rxnorm": "rxnorm",
        "http://hl7.org/fhir/sid/icd-10-cm": "icd10cm",
        "http://loinc.org": "loinc",
        "http://snomed.info/sct": "snomed",
    }
    data["codes"] = [
        Code(system=systems.get(c["system"], c["system"]), code=c["code"])
        for c in resource.get(coding_field, {}).get("coding", [])
    ]
    quantities = record_quantities(outcome.resource)
    if (
        quantities
        and all("value" in q and q.get("system") == "http://unitsofmeasure.org" for q in quantities)
        and len({q.get("code") for q in quantities}) == 1
    ):
        data["canonical_value"] = tuple(str(q["value"]) for q in quantities)
        data["ucum_unit"] = quantities[0].get("code")
    return Prediction(
        **data,
        id=outcome.id,
        lanes=outcome.lanes,
        export_state=outcome.export_state,
        resource=outcome.resource,
        review_reasons=outcome.review_reasons,
        quoted_evidence=assertion.evidence,
        reviewed=(
            outcome.resource is not None
            and outcome.reviewed_resource_digest == resource_digest(outcome.resource)
        ),
    )
