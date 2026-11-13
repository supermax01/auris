"""Collection Bundle transport. Clinical intake content is never changed."""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, UUID, uuid5

from auris._storage import write_private_text
from auris.extract.quarantine import QuarantinedItem, QuarantineReview
from auris.fhir.common import build_anchors
from auris.fhir.models import LaneState, MappingOutcome
from auris.intake import Intake
from auris.review import unresolved_quarantines

if TYPE_CHECKING:
    from auris.validation.models import ValidationRun


def _full_url(resource: dict[str, Any]) -> str:
    identifier = resource["id"]
    try:
        value = str(UUID(identifier))
    except ValueError:
        value = str(
            uuid5(NAMESPACE_URL, f"auris:bundle:v1:{resource['resourceType']}/{identifier}")
        )
    return f"urn:uuid:{value}"


def build_collection(
    intake: Intake,
    outcomes: Sequence[MappingOutcome],
    *,
    preview: bool = False,
    validation: ValidationRun | None = None,
    quarantined_items: Sequence[QuarantinedItem] = (),
    quarantine_reviews: Sequence[QuarantineReview] = (),
) -> dict[str, Any]:
    """Include checked resources, or all built previews when requested.
    References are rewritten only in copies to resolve against urn fullUrls.
    Unresolved material mappings block the default collection as a whole (A4).
    Intentional, evidence-passing ledger rows do not block it.
    """
    if not preview and unresolved_quarantines(quarantined_items, quarantine_reviews):
        raise ValueError(
            "malformed_item_quarantined requires dismissal before final export (PLAN A4)"
        )
    if not preview and any(
        outcome.is_material
        and (
            outcome.export_state not in ("final_exportable", "ledger_only")
            or LaneState.FAIL in outcome.lanes.model_dump().values()
        )
        for outcome in outcomes
    ):
        raise ValueError("material assertion requires review before final collection (PLAN A4)")
    anchors = build_anchors(intake)
    resources = [anchors.patient, anchors.practitioner, anchors.encounter]
    if not preview:
        if validation is None:
            raise ValueError("intake anchors require validation before final collection")
        for anchor in resources:
            result = validation.for_resource(anchor)
            if (
                result.base_r4.state != LaneState.PASS
                or result.us_core_profile.state != LaneState.PASS
            ):
                raise ValueError("intake anchors must pass R4 and profile validation")
    resources += [
        outcome.resource
        for outcome in outcomes
        if outcome.resource is not None
        and (
            preview
            or (
                outcome.export_state == "final_exportable"
                and LaneState.FAIL not in outcome.lanes.model_dump().values()
            )
        )
    ]
    if not preview:
        assert validation is not None
        for resource in resources[3:]:
            result = validation.for_resource(resource)
            if (
                result.base_r4.state != LaneState.PASS
                or result.us_core_profile.state != LaneState.PASS
                or result.terminology.state == LaneState.FAIL
            ):
                raise ValueError("exported resource must pass validation in this run")
    references = {f"{r['resourceType']}/{r['id']}": _full_url(r) for r in resources}
    if len(references) != len(resources) or len(set(references.values())) != len(resources):
        raise ValueError("duplicate resource identity in collection")

    def rewrite(value: Any) -> None:
        if isinstance(value, dict):
            if "reference" in value:
                if value["reference"] not in references:
                    raise ValueError("unresolved reference in collection")
                value["reference"] = references[value["reference"]]
            for child in value.values():
                rewrite(child)
        elif isinstance(value, list):
            for child in value:
                rewrite(child)

    entries = []
    for resource in resources:
        copied = copy.deepcopy(resource)
        rewrite(copied)
        entries.append({"fullUrl": _full_url(resource), "resource": copied})
    return {"resourceType": "Bundle", "type": "collection", "entry": entries}


def bundle_bytes(bundle: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            bundle, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode()


def write_bundle(path: str | Path, bundle: dict[str, Any]) -> None:
    write_private_text(path, bundle_bytes(bundle).decode())
