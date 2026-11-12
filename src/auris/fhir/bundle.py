"""Collection Bundle transport. Clinical intake content is never changed."""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from auris._storage import write_private_text
from auris.fhir.common import build_anchors
from auris.fhir.models import LaneState, MappingOutcome
from auris.intake import Intake


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
) -> dict[str, Any]:
    """Include mapper-eligible resources, or all built previews when requested.

    This step does not authorize a clinical export: the three validator lanes
    are NOT_CHECKED until the validator step. A supplied FAIL always excludes
    a resource from the default collection, even if its mapper state is stale.
    References are rewritten only in copies to resolve against urn fullUrls.
    Unresolved material mappings block the default collection as a whole (A4).
    Intentional, evidence-passing ledger rows do not block it.
    """
    if not preview and any(
        outcome.is_material
        and (
            outcome.export_state == "needs_review"
            or LaneState.FAIL in outcome.lanes.model_dump().values()
        )
        for outcome in outcomes
    ):
        raise ValueError("material assertion requires review before final collection (PLAN A4)")
    anchors = build_anchors(intake)
    resources = [anchors.patient, anchors.practitioner, anchors.encounter]
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
