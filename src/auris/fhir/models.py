"""Mapper outcomes and derived export policy (PLAN A2/A4)."""

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from auris.assertions import AssertionBase
from auris.consistency import ConsistencyResult
from auris.evidence import AssertionEvidenceResult


class LaneState(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_CHECKED = "NOT_CHECKED"


class ValidationLanes(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    reduced_schema: LaneState = LaneState.PASS
    evidence: LaneState
    base_r4: LaneState = LaneState.NOT_CHECKED
    us_core_profile: LaneState = LaneState.NOT_CHECKED
    terminology: LaneState = LaneState.NOT_CHECKED

    @property
    def export_checks_pass(self) -> bool:
        return (
            all(
                state == LaneState.PASS
                for state in (
                    self.reduced_schema,
                    self.evidence,
                    self.base_r4,
                    self.us_core_profile,
                )
            )
            and self.terminology != LaneState.FAIL
        )


def resource_digest(resource: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(resource, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


ExportState = Literal[
    "final_exportable", "needs_review", "ledger_only", "pending_validation", "validation_failed"
]


class MappingOutcome(BaseModel):
    """Export eligibility is derived, never assigned by review or a caller.

    A3 ledger rows use ledger_only. Refused resource mappings keep the full
    assertion in ledger and use needs_review until that ledger version is
    approved. Approval never builds FHIR for a refused mapping.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    assertion_position: int = Field(ge=0)
    kind: str
    claimed_profile: str | None
    resource: dict[str, Any] | None = None
    ledger: dict[str, Any] | None = None
    evidence: AssertionEvidenceResult
    consistency: ConsistencyResult = ConsistencyResult()
    review_reasons: tuple[str, ...] = ()
    mapper_flags: tuple[str, ...] = ()
    provenance_notes: tuple[str, ...] = ()
    is_material: bool
    ledger_only: bool = False
    validation_digest: str | None = None
    reviewed_resource_digest: str | None = None
    lanes: ValidationLanes

    @computed_field
    @property
    def export_state(self) -> ExportState:
        if LaneState.FAIL in self.lanes.model_dump().values():
            return "validation_failed"
        if self.ledger_only:
            if (
                self.consistency.review_reasons or "merge_conflict" in self.review_reasons
            ) and self.reviewed_resource_digest != resource_digest(self.ledger or {}):
                return "needs_review"
            return "ledger_only"
        if self.resource is None:
            if self.ledger is not None and self.reviewed_resource_digest == resource_digest(
                self.ledger
            ):
                return "ledger_only"
            return "needs_review"
        digest = resource_digest(self.resource)
        if (self.is_material or self.review_reasons) and self.reviewed_resource_digest != digest:
            return "needs_review"
        if self.validation_digest != digest or not self.lanes.export_checks_pass:
            return "pending_validation"
        return "final_exportable"


class BuildResult(BaseModel):
    """Internal builder result before evidence/review/export policy is applied."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    profile: str | None = None
    resource: dict[str, Any] | None = None
    reasons: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    ledger_only: bool = False


def ledger_entry(assertion: AssertionBase) -> dict[str, Any]:
    # Preserve every stated field, polarity and quote. Exact offsets live in
    # MappingOutcome.evidence for both resources and ledger entries.
    return assertion.model_dump(mode="json")
