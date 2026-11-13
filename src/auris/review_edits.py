"""Private correction overlays and exact-version rechecks; extraction stays immutable."""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict

from auris._storage import write_private_text
from auris.assertions import ASSERTION_MODELS, AssertionBase, parse_assertion
from auris.evidence import EVIDENCE_CHECKER_VERSION
from auris.fhir.common import build_anchors
from auris.fhir.mapper import Mapper
from auris.fhir.models import MappingOutcome, resource_digest
from auris.intake import Intake
from auris.transcript import SpeakerRole, Transcript
from auris.validation.models import ValidationRun
from auris.validation.outcomes import classify
from auris.validation.report import apply_validation

if TYPE_CHECKING:
    from auris.review_state import ReviewEvent

MAX_FIELD_LENGTH = 4000
LOCKED_FIELDS = frozenset({"evidence", "source_speaker_role"})
MODELS = {get_args(model.model_fields["kind"].annotation)[0]: model for model in ASSERTION_MODELS}


def editable_assertion(assertion: AssertionBase, fields: dict[str, Any]) -> AssertionBase:
    """Apply a bounded patch, discard only fields belonging to the previous kind."""
    kind = fields.get("kind", assertion.kind)
    if not isinstance(kind, str) or kind not in MODELS:
        raise ValueError("unknown assertion kind")
    model = MODELS[kind]
    allowed = model.model_fields.keys() - LOCKED_FIELDS
    if fields.keys() - allowed:
        raise ValueError("unknown or read-only correction field")
    if not fields or any(
        value is not None and (not isinstance(value, str) or len(value) > MAX_FIELD_LENGTH)
        for value in fields.values()
    ):
        raise ValueError("correction fields must be text or null, at most 4000 characters each")
    original = assertion.model_dump(mode="json")
    data = {name: original.get(name) for name in model.model_fields}
    return parse_assertion({**data, **fields, "kind": kind})


def field_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in sorted(before.keys() | after.keys())
        if before.get(key) != after.get(key) or (key in before) != (key in after)
    }


def active_corrections(events: Sequence[ReviewEvent]) -> tuple[ReviewEvent, ...]:
    active: dict[str, ReviewEvent] = {}
    for event in events:
        if event.action in ("edit", "speaker"):
            active[event.entry_id] = event
        elif event.action == "revoke_edit":
            revoked = active.get(event.revokes or "")
            if revoked is None:
                raise ValueError("correction revocation must name an active correction")
            latest = next(
                e for e in reversed(tuple(active.values())) if e.target_id == revoked.target_id
            )
            if latest != revoked:
                raise ValueError("undo the latest correction for this target first")
            del active[revoked.entry_id]
    return tuple(active.values())


class CorrectedOutcome(MappingOutcome):
    """Review-only identity; keep the extraction mapper's wire contract unchanged."""

    review_version_digest: str


class Version(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    outcome: CorrectedOutcome
    validation: ValidationRun
    status: Literal["rechecking", "complete", "blocked"]
    message: str | None = None
    checked_at: str | None = None


class Layer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["review-versions-1"] = "review-versions-1"
    corrections: dict[str, dict[str, Any]] = {}
    versions: dict[str, Version] = {}

    @classmethod
    def read(cls, path: Path) -> Layer:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return cls()
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError("review versions must be a regular file with permissions 0600")
            return cls.model_validate_json(handle.read())

    def save(self, path: Path) -> None:
        if path.is_symlink():
            raise ValueError("review versions must not be a symlink")
        write_private_text(
            path,
            self.model_dump_json(
                indent=2, exclude={"versions": {"__all__": {"outcome": {"export_state"}}}}
            )
            + "\n",
        )


def overlays(
    originals: dict[str, AssertionBase],
    transcript: Transcript,
    layer: Layer,
    events: Sequence[ReviewEvent],
) -> tuple[dict[str, AssertionBase], Transcript]:
    assertions = dict(originals)
    roles = {}
    for event in active_corrections(events):
        payload = layer.corrections[event.entry_id]
        if event.action == "edit":
            assertion = parse_assertion(payload)
            if assertion.evidence != originals[event.target_id].evidence:
                raise ValueError("corrections cannot change evidence")
            assertions[event.target_id] = assertion
        else:
            roles[event.target_id] = SpeakerRole(payload["role"])
    effective = transcript.model_copy(
        update={
            "segments": tuple(
                s.model_copy(update={"role": roles.get(s.id, s.role)}) for s in transcript.segments
            )
        }
    )
    for identifier, assertion in assertions.items():
        # Source role is derived only when an actual cited role has changed.
        cited = [effective.segment_by_id(e.segment_id) for e in assertion.evidence]
        if any(s and s.role != transcript.segment_by_id(s.id).role for s in cited):
            cited_roles = {s.role for s in cited if s is not None}
            assertions[identifier] = assertion.model_copy(
                update={
                    "source_speaker_role": next(iter(cited_roles))
                    if len(cited_roles) == 1
                    else None
                }
            )
    return assertions, effective


def revision_context(
    identifier: str, assertion: AssertionBase, events: Sequence[ReviewEvent]
) -> tuple[str, ...]:
    cited = {e.segment_id for e in assertion.evidence}
    return tuple(
        e.entry_id
        for e in active_corrections(events)
        if (e.action == "edit" and e.target_id == identifier)
        or (
            e.action == "speaker"
            and (e.target_id in cited or assertion.kind == "explicit_new_order")
        )
    )


def version_key(
    assertion: AssertionBase,
    transcript: Transcript,
    original: MappingOutcome,
    revisions: tuple[str, ...] = (),
) -> str:
    """Include indirect role dependencies: all clinician clusters for new orders."""
    return resource_digest(
        {
            "original": resource_digest(original.model_dump(mode="json")),
            "assertion": assertion.model_dump(mode="json"),
            "corrections": revisions,
            "cited_roles": {
                e.segment_id: s.role.value
                if (s := transcript.segment_by_id(e.segment_id))
                else None
                for e in assertion.evidence
            },
            "clinicians": sorted(
                {
                    s.speaker_cluster_id
                    for s in transcript.segments
                    if s.role == SpeakerRole.CLINICIAN
                }
            )
            if assertion.kind == "explicit_new_order"
            else None,
        }
    )


def unchecked(resources: Sequence[dict[str, Any]], message: str) -> ValidationRun:
    return ValidationRun(
        resources=tuple(classify(resource, (), (), failure=message) for resource in resources),
        wall_seconds=0,
        failure=message,
    )


class CorrectionChecker:
    """Lazy setup-only dependencies. Never download or write shared cache during review."""

    def map(
        self,
        intake: Intake,
        transcript: Transcript,
        assertions: dict[str, AssertionBase],
        originals: dict[str, MappingOutcome],
        extraction: dict[str, Any],
    ) -> dict[str, MappingOutcome]:
        from auris.config import load_frozen_config
        from auris.fhir.contracts import load_unit_sets
        from auris.setup import fhir_packages, terminology
        from auris.terminology import TerminologyIndex

        config = load_frozen_config()
        core = config.fhir.package("hl7.fhir.r4.core")
        assert core is not None
        try:
            units = load_unit_sets(fhir_packages.default_cache_dir() / core.filename, core.sha256)
            with TerminologyIndex(terminology.default_cache_dir() / "index.sqlite") as index:
                mapper = Mapper(
                    intake,
                    transcript,
                    index,
                    units,
                    evidence_checker_version=extraction.get("metadata", {}).get(
                        "evidence_checker_version", EVIDENCE_CHECKER_VERSION
                    ),
                )
                return remap(mapper, assertions, originals, extraction)
        except (OSError, ValueError, sqlite3.Error):
            raise ValueError(
                "correction checks unavailable; complete local FHIR and terminology setup, "
                "then retry"
            ) from None

    def validate(self, intake: Intake, directory: Path, resources: Sequence[dict]) -> ValidationRun:
        from auris.config import load_frozen_config
        from auris.setup import validator
        from auris.validation.runner import ValidatorRunner

        work = directory / "validator-work"
        work.mkdir(mode=0o700, exist_ok=True)
        if work.is_symlink() or stat.S_IMODE(work.stat().st_mode) != 0o700:
            raise ValueError("validator workdir must be private and not a symlink")
        return ValidatorRunner(
            load_frozen_config(),
            validator.default_cache_dir(),
            work_directory=work,
            allow_example_urls=intake.fictional,
        ).validate(resources)


def remap(
    mapper: Mapper,
    assertions: dict[str, AssertionBase],
    originals: dict[str, MappingOutcome],
    extraction: dict[str, Any],
) -> dict[str, MappingOutcome]:
    failures = {
        row["assertion_position"]: set(row["segment_ids"])
        for row in extraction.get("citation_failures", [])
    }
    results = {}
    for identifier, assertion in assertions.items():
        original = originals[identifier]
        invalid = failures.get(original.assertion_position, set())
        mapped = mapper.map_assertion(
            assertion,
            original.assertion_position,
            allowed_segment_ids=frozenset(s.id for s in mapper.transcript.segments) - invalid,
        )
        resource = {**mapped.resource, "id": identifier} if mapped.resource else None
        # Stable card/resource identities survive kind and transcript-overlay changes.
        updates: dict[str, Any] = {"id": identifier, "resource": resource}
        if "merge_conflict" in original.review_reasons:
            updates.update(
                is_material=True, review_reasons=(*mapped.review_reasons, "merge_conflict")
            )
        results[identifier] = mapped.model_copy(update=updates)
    return results


def validation_resources(
    intake: Intake, outcomes: Sequence[MappingOutcome]
) -> list[dict[str, Any]]:
    anchors = build_anchors(intake)
    return [anchors.patient, anchors.practitioner, anchors.encounter] + [
        o.resource for o in outcomes if o.resource is not None
    ]


def checked_version(outcome: MappingOutcome, run: ValidationRun) -> Version:
    # Even a malformed/partial checker response is NOT_CHECKED, never a cached PASS.
    if outcome.resource is not None:
        outcome = apply_validation(outcome, run)
    relevant = [
        r
        for r in run.resources
        if r.resource_type in ("Patient", "Practitioner", "Encounter") or r.id == outcome.id
    ]
    incomplete = bool(run.failure) or any(
        r.base_r4.state != "PASS"
        or r.us_core_profile.state != "PASS"
        or r.terminology.state == "FAIL"
        for r in relevant
    )
    return Version(
        outcome=outcome,
        validation=run,
        checked_at=datetime.now(UTC).isoformat(),
        status="blocked" if incomplete else "complete",
        message=(
            "Re-check could not pass all FHIR checks. "
            "Export is blocked; retry checks or dismiss this item."
        )
        if incomplete
        else None,
    )
