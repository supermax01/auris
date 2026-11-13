"""Fictional validator cases; no clinical values or codes are invented here."""

import copy
from pathlib import Path

from fhir_helpers import BY_NAME, GOLDENS

from auris.fhir.common import build_anchors
from auris.validation.models import ValidationRun
from auris.validation.outcomes import classify

RECORDED = Path(__file__).parent / "fixtures" / "validator"


def golden_resources():
    return [copy.deepcopy(c["expected"]["resource"]) for c in GOLDENS if c["expected"]["resource"]]


def broken_resources():
    def resource(name, identifier):
        value = copy.deepcopy(BY_NAME[name]["expected"]["resource"])
        value["id"] = identifier
        return value

    allergy = resource("allergy-ingredient", "fictional-allergy-missing-status")
    allergy.pop("clinicalStatus")
    bp_root = resource("blood-pressure", "fictional-bp-root-only")
    bp_root["valueQuantity"] = bp_root.pop("component")[0]["valueQuantity"]
    bp_slice = resource("blood-pressure", "fictional-bp-missing-systolic")
    bp_slice["component"].pop(0)
    medication = resource("new-order", "fictional-medication-missing-intent")
    medication.pop("intent")
    condition = resource("clinician_assessed_problem", "fictional-condition-missing-category")
    condition.pop("category")
    vital = resource("heart-rate", "fictional-vital-missing-effective")
    vital.pop("effectiveDateTime")
    return [allergy, bp_root, bp_slice, medication, condition, vital]


def passing_run(intake, outcomes=()):
    """Explicit fake checker for export-policy unit tests, never integration."""
    anchors = build_anchors(intake)
    resources = [anchors.patient, anchors.practitioner, anchors.encounter]
    resources.extend(o.resource for o in outcomes if o.resource is not None)
    return ValidationRun(resources=tuple(classify(r, (), ()) for r in resources), wall_seconds=0)
