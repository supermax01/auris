"""OperationOutcome parsing and two-pass checker-scope classification (PLAN A2)."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from auris.fhir.models import LaneState
from auris.validation.models import LaneResult, ResourceValidation, ValidatorIssue, resource_digest

EXTENSION = "http://hl7.org/fhir/StructureDefinition/operationoutcome-"
TIER_ZERO_REASON = "tier 0: terminology services disabled (-tx n/a)"


def _extension(value: dict[str, Any], suffix: str) -> str | None:
    for extension in value.get("extension", []):
        if not isinstance(extension, dict):
            raise ValueError("invalid OperationOutcome extension")
        if extension.get("url") == EXTENSION + suffix:
            return extension.get("valueString", extension.get("valueCode"))
    return None


def parse_issue(issue: dict[str, Any]) -> ValidatorIssue:
    return ValidatorIssue(
        severity=issue["severity"],
        code=issue["code"],
        details=issue.get("details", {}),
        diagnostics=issue.get("diagnostics"),
        expression=tuple(issue.get("expression", [])),
        location=tuple(issue.get("location", [])),
        message_id=_extension(issue, "message-id"),
        source=_extension(issue, "issue-source"),
        context=_extension(issue, "issue-context"),
    )


def parse_outcomes(raw: str) -> dict[str, tuple[ValidatorIssue, ...]]:
    """Map explicit input-file extensions to issues; never rely on entry order."""
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise ValueError("validator output must be a JSON object")
    if document.get("resourceType") == "OperationOutcome":
        outcomes = [document]
    elif document.get("resourceType") == "Bundle":
        outcomes = [entry["resource"] for entry in document.get("entry", [])]
    else:
        raise ValueError("validator output must be an OperationOutcome or Bundle")
    if not outcomes:
        raise ValueError("validator returned no outcomes")
    result = {}
    for outcome in outcomes:
        if (
            not isinstance(outcome, dict)
            or outcome.get("resourceType") != "OperationOutcome"
            or not isinstance(outcome.get("issue"), list)
        ):
            raise ValueError("invalid OperationOutcome in validator output")
        filename = _extension(outcome, "file")
        if not filename or filename in result:
            raise ValueError("missing or duplicate validator input-file identity")
        result[filename] = tuple(parse_issue(issue) for issue in outcome["issue"])
    return result


def is_terminology(issue: ValidatorIssue) -> bool:
    message = (issue.message_id or "").lower()
    return (
        issue.source == "TerminologyEngine"
        or issue.code == "code-invalid"
        or message.startswith(("terminology_", "tx_"))
        or message == "error_validating_code_running_without_terminology_services"
    )


def _checked(issues: tuple[ValidatorIssue, ...]) -> LaneResult:
    state = (
        LaneState.FAIL if any(i.severity in ("error", "fatal") for i in issues) else LaneState.PASS
    )
    return LaneResult(state=state, issues=issues)


def classify(
    resource: dict[str, Any],
    base: tuple[ValidatorIssue, ...],
    profile: tuple[ValidatorIssue, ...],
    *,
    failure: str | None = None,
) -> ResourceValidation:
    remaining = Counter(issue.identity for issue in base)
    extra = []
    for issue in profile:
        if remaining[issue.identity]:
            remaining[issue.identity] -= 1
        else:
            extra.append(issue)
    # Keep all base diagnostics and only the additional profile diagnostics.
    terms = tuple(
        issue.model_copy(update={"severity": "information", "original_severity": issue.severity})
        for issue in (*base, *extra)
        if is_terminology(issue)
    )
    base_lane = _checked(tuple(issue for issue in base if not is_terminology(issue)))
    profile_lane = _checked(tuple(issue for issue in extra if not is_terminology(issue)))
    profiles = tuple(resource.get("meta", {}).get("profile", []))
    if not profiles:
        profile_lane = LaneResult(
            state=LaneState.NOT_CHECKED, issues=profile_lane.issues, reason="no claimed profile"
        )
    elif not all(
        p.startswith("http://hl7.org/fhir/us/core/StructureDefinition/") for p in profiles
    ):
        profile_lane = LaneResult(
            state=LaneState.NOT_CHECKED,
            issues=profile_lane.issues,
            reason="unsupported claimed profile",
        )
    if any("profile_unknown" in (issue.message_id or "").lower() for issue in profile):
        profile_lane = LaneResult(
            state=LaneState.NOT_CHECKED,
            issues=profile_lane.issues,
            reason="validator could not resolve a profile; profile checks incomplete",
        )
    if failure:
        base_lane = LaneResult(state=LaneState.NOT_CHECKED, reason=failure, issues=base_lane.issues)
        profile_lane = LaneResult(
            state=LaneState.NOT_CHECKED, reason=failure, issues=profile_lane.issues
        )
    return ResourceValidation(
        resource_type=resource["resourceType"],
        id=resource["id"],
        resource_digest=resource_digest(resource),
        claimed_profiles=profiles,
        base_r4=base_lane,
        us_core_profile=profile_lane,
        terminology=LaneResult(state=LaneState.NOT_CHECKED, issues=terms, reason=TIER_ZERO_REASON),
    )
