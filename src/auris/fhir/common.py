"""Shared resource construction; references are supplied by intake only."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from auris.fhir.contracts import profile_url
from auris.intake import Intake
from auris.terminology import ICD10CM, TerminologyIndex

Resource = dict[str, Any]


def resource_id(transcript_hash: str, position: int, kind: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"auris:assertion:v1:{transcript_hash}:{position}:{kind}"))


def coded(system: str, code: str) -> dict[str, Any]:
    return {"coding": [{"system": system, "code": code}]}


def status_concept(system: str, code: str) -> dict[str, Any]:
    return coded(f"http://terminology.hl7.org/CodeSystem/{system}", code)


def base(resource_type: str, identifier: str, profile: str) -> Resource:
    return {
        "resourceType": resource_type,
        "id": identifier,
        "meta": {"profile": [profile_url(profile)]},
    }


@dataclass(frozen=True)
class Anchors:
    patient: Resource
    practitioner: Resource
    encounter: Resource

    def reference(self, name: str) -> dict[str, str]:
        resource = getattr(self, name)
        return {"reference": f"{resource['resourceType']}/{resource['id']}"}


def build_anchors(intake: Intake) -> Anchors:
    resources = []
    for name in ("patient", "practitioner", "encounter"):
        supplied = getattr(intake, name).model_dump(mode="json", by_alias=True, exclude_none=True)
        identifier = supplied.get("id")
        if identifier is None:
            if name == "patient":
                identifier = intake.encounter.subject.reference.removeprefix("Patient/")
            else:
                key = json.dumps(supplied, sort_keys=True, separators=(",", ":"))
                identifier = str(uuid5(NAMESPACE_URL, f"auris:intake:v1:{name}:{key}"))
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,64}", identifier):
            raise ValueError("intake resource id must satisfy the R4 id format")
        resource = base(name.title(), identifier, f"us-core-{name}")
        resource.update(supplied)
        if name == "encounter":
            for concept in resource["type"]:
                if not concept.get("coding"):
                    concept.pop("coding", None)
        resources.append(resource)
    return Anchors(*resources)


def code_text(
    text: str,
    system: str,
    terminology: TerminologyIndex,
    *,
    encounter_start: str,
) -> tuple[Resource, tuple[str, ...], tuple[str, ...]]:
    concept: Resource = {"text": text}
    reasons: tuple[str, ...] = ()
    if system == ICD10CM and not terminology.icd_valid_on(encounter_start):
        match = None
        reasons = ("terminology_release_out_of_period",)
    else:
        match = terminology.exact(text, system)
    if match is None:
        return concept, (*reasons, "needs_coding_review"), ("uncoded_text_only",)
    concept["coding"] = [match.coding()]
    return concept, (), ()
