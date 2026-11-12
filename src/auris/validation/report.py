"""Apply validator lanes and write the private per-resource validation.json."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from auris._storage import write_private_text
from auris.fhir.common import build_anchors
from auris.fhir.models import LaneState, MappingOutcome, ValidationLanes
from auris.intake import Intake
from auris.validation.models import LaneResult, ValidationRun, ValidatorIssue
from auris.validation.runner import ValidatorRunner


def apply_validation(outcome: MappingOutcome, run: ValidationRun) -> MappingOutcome:
    if outcome.resource is None:
        return outcome
    result = run.for_resource(outcome.resource)
    return outcome.model_copy(
        update={
            "lanes": ValidationLanes(
                reduced_schema=outcome.lanes.reduced_schema,
                evidence=outcome.lanes.evidence,
                base_r4=result.base_r4.state,
                us_core_profile=result.us_core_profile.state,
                terminology=result.terminology.state,
            ),
            "validation_digest": result.resource_digest,
        }
    )


def validate_mappings(
    runner: ValidatorRunner, intake: Intake, outcomes: Sequence[MappingOutcome]
) -> tuple[tuple[MappingOutcome, ...], ValidationRun]:
    anchors = build_anchors(intake)
    resources = [anchors.patient, anchors.practitioner, anchors.encounter]
    resources.extend(o.resource for o in outcomes if o.resource is not None)
    run = runner.validate(resources)
    return tuple(apply_validation(outcome, run) for outcome in outcomes), run


def honest_label(lanes: ValidationLanes) -> str:
    if lanes.export_checks_pass:
        if lanes.terminology == LaneState.PASS:
            return "validated"
        return "profile-checked, terminology NOT_CHECKED"
    if LaneState.FAIL in lanes.model_dump().values():
        return "checks failed, terminology NOT_CHECKED"
    return "checks incomplete, terminology NOT_CHECKED"


def validation_document(
    intake: Intake, outcomes: Sequence[MappingOutcome], run: ValidationRun
) -> dict[str, Any]:
    rows = []
    anchors = build_anchors(intake)
    for resource in (anchors.patient, anchors.practitioner, anchors.encounter):
        result = run.for_resource(resource)
        lanes = ValidationLanes(
            reduced_schema=LaneState.PASS,
            evidence=LaneState.NOT_CHECKED,
            base_r4=result.base_r4.state,
            us_core_profile=result.us_core_profile.state,
            terminology=result.terminology.state,
        )
        checked = result.base_r4.state == result.us_core_profile.state == LaneState.PASS
        failed = LaneState.FAIL in (result.base_r4.state, result.us_core_profile.state)
        rows.append(
            {
                "id": result.id,
                "resource_type": result.resource_type,
                "claimed_profile": result.claimed_profiles,
                "review_reasons": [],
                "export_state": "final_exportable"
                if checked
                else ("validation_failed" if failed else "pending_validation"),
                "label": "profile-checked, terminology NOT_CHECKED"
                if checked
                else honest_label(lanes),
                "lanes": {
                    "reduced_schema": LaneResult(state=LaneState.PASS).model_dump(mode="json"),
                    "evidence": LaneResult(
                        state=LaneState.NOT_CHECKED,
                        reason="supplied intake anchor; not extracted from transcript",
                    ).model_dump(mode="json"),
                    **{
                        name: getattr(result, name).model_dump(mode="json")
                        for name in ("base_r4", "us_core_profile", "terminology")
                    },
                },
            }
        )
    for original in outcomes:
        outcome = apply_validation(original, run)
        evidence_issues = tuple(
            ValidatorIssue(
                severity="error",
                code="evidence",
                details={"text": item.failure.value},
                expression=(item.segment_id,),
                source="Auris evidence checker",
            )
            for item in outcome.evidence.items
            if item.failure is not None
        )
        details = {
            "reduced_schema": LaneResult(state=outcome.lanes.reduced_schema),
            "evidence": LaneResult(state=outcome.lanes.evidence, issues=evidence_issues),
        }
        for name in ("base_r4", "us_core_profile", "terminology"):
            details[name] = (
                getattr(run.for_resource(outcome.resource), name)
                if outcome.resource
                else (LaneResult(state=LaneState.NOT_CHECKED, reason="no FHIR resource emitted"))
            )
        rows.append(
            {
                "id": outcome.id,
                "resource_type": outcome.resource["resourceType"] if outcome.resource else None,
                "claimed_profile": outcome.claimed_profile,
                "review_reasons": outcome.review_reasons,
                "export_state": outcome.export_state,
                "label": honest_label(outcome.lanes),
                "lanes": {name: lane.model_dump(mode="json") for name, lane in details.items()},
            }
        )
    return {
        "tier": 0,
        "resources": rows,
        "validator": run.model_dump(mode="json", exclude={"resources"}),
    }


def write_validation(
    path: str | Path, intake: Intake, outcomes: Sequence[MappingOutcome], run: ValidationRun
) -> None:
    write_private_text(
        path, json.dumps(validation_document(intake, outcomes, run), indent=2) + "\n"
    )
