"""Typed validator results, with original issues preserved separately from lanes."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from auris.fhir.models import LaneState, resource_digest

Severity = Literal["fatal", "error", "warning", "information"]


class ResultModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ValidatorIssue(ResultModel):
    severity: Severity
    code: str
    details: dict[str, Any] = {}
    diagnostics: str | None = None
    expression: tuple[str, ...] = ()
    location: tuple[str, ...] = ()
    message_id: str | None = None
    source: str | None = None
    context: str | None = None
    original_severity: Severity | None = None

    @property
    def text(self) -> str | None:
        return self.details.get("text")

    @property
    def identity(self) -> str:
        # Source line/column extensions change when meta.profile is removed.
        # If expression exists it is the stable location; context is useful
        # in reports but the same inherited invariant can have two contexts.
        return json.dumps(
            [
                self.severity,
                self.code,
                self.message_id,
                self.details,
                self.diagnostics,
                self.expression or self.location,
            ],
            sort_keys=True,
        )


class LaneResult(ResultModel):
    state: LaneState
    issues: tuple[ValidatorIssue, ...] = ()
    reason: str | None = None


class ResourceValidation(ResultModel):
    resource_type: str
    id: str
    resource_digest: str
    claimed_profiles: tuple[str, ...]
    base_r4: LaneResult
    us_core_profile: LaneResult
    terminology: LaneResult


class ValidationRun(ResultModel):
    resources: tuple[ResourceValidation, ...]
    wall_seconds: float
    command: tuple[str, ...] = ()
    returncode: int | None = None
    failure: str | None = None
    raw_output: str | None = None
    stdout: str = ""
    stderr: str = ""

    def for_resource(self, resource: dict[str, Any]) -> ResourceValidation:
        for result in self.resources:
            if (result.resource_type, result.id) == (resource["resourceType"], resource["id"]):
                if result.resource_digest != resource_digest(resource):
                    raise ValueError("resource changed since validation; revalidate it")
                return result
        raise ValueError("resource was not checked in this validator run")
