"""Version-bound review receipts and deterministic, private run projections (A3/A4)."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from auris._storage import append_private_jsonl, write_private_text
from auris.extract.quarantine import QuarantinedItem
from auris.fhir.bundle import build_collection, write_bundle
from auris.fhir.models import LaneState, MappingOutcome, resource_digest
from auris.intake import Intake
from auris.render.note import NoteMetadata, _text, note_items, render_note
from auris.transcript import Transcript
from auris.validation.models import ValidationRun
from auris.validation.report import apply_validation, validation_document


class ReviewInputs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["review-inputs-1"] = "review-inputs-1"
    intake: Intake
    transcript: Transcript
    validation: ValidationRun
    note_metadata: NoteMetadata


class ReviewEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    entry_id: str = Field(default_factory=lambda: str(uuid4()))
    target_id: str
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: Literal["approve", "dismiss", "undo"]
    reviewer: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=200)
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    reason: str = Field(default="", max_length=4000)
    revokes: str | None = None
    diff: dict[str, Any]

    @field_validator("reviewer", "role")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reviewer name and role are required")
        return value  # Attribution exactly as entered, never authentication.


def target_digest(target: MappingOutcome | QuarantinedItem) -> str:
    if isinstance(target, QuarantinedItem):
        return resource_digest(target.model_dump(mode="json"))
    return resource_digest(target.resource if target.resource is not None else target.ledger or {})


def _event(raw: dict[str, Any]) -> ReviewEvent:
    # Accept the original step-10 quarantine receipt without changing its bytes.
    return ReviewEvent.model_validate(
        {"entry_id": "legacy-" + resource_digest(raw), "diff": {}, **raw}
    )


def read_events(path: Path) -> tuple[ReviewEvent, ...]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return ()
    with os.fdopen(descriptor, encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("review log must be a regular file with permissions 0600")
        return tuple(_event(json.loads(line)) for line in handle)


def active_events(events: tuple[ReviewEvent, ...]) -> dict[tuple[str, str], ReviewEvent]:
    stacks: dict[tuple[str, str], list[ReviewEvent]] = {}
    seen: set[str] = set()
    for event in events:
        if event.entry_id in seen:
            raise ValueError("duplicate review receipt")
        seen.add(event.entry_id)
        stack = stacks.setdefault((event.target_id, event.target_sha256), [])
        if event.action == "undo":
            if not stack or event.revokes != stack[-1].entry_id:
                raise ValueError("revocation must name the latest action on this version")
            stack.pop()
        else:
            if event.action == "dismiss" and not event.reason.strip():
                raise ValueError("dismissal requires a reason")
            stack.append(event)
    return {key: stack[-1] for key, stack in stacks.items() if stack}


class ReviewRun:
    """Reload source artifacts on each request; never write extraction or mappings."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        if (
            directory.is_symlink()
            or not directory.is_dir()
            or stat.S_IMODE(directory.stat().st_mode) != 0o700
        ):
            raise ValueError("review run directory must have permissions 0700, without a symlink")
        self.log_path = directory / "review.jsonl"
        self.load()

    def load(self) -> None:
        try:
            self.inputs = ReviewInputs.model_validate_json(
                (self.directory / "review-inputs.json").read_bytes()
            )
        except FileNotFoundError:
            raise ValueError(
                "review inputs missing; create a new run with auris run or demo"
            ) from None
        extraction = json.loads((self.directory / "extraction.json").read_bytes())
        if extraction["status"] != "complete":
            raise ValueError("failed extraction cannot be reviewed into a successful run")
        if extraction["metadata"]["transcript_sha256"] != self.inputs.transcript.content_hash:
            raise ValueError("review transcript differs from extraction; regenerate the run")
        self.outcomes = tuple(
            apply_validation(
                MappingOutcome.model_validate(
                    {k: v for k, v in row.items() if k != "export_state"}
                ).model_copy(update={"reviewed_resource_digest": None}),
                self.inputs.validation,
            )
            for row in extraction["mappings"]
        )
        self.quarantines = tuple(
            QuarantinedItem.model_validate(row) for row in extraction.get("quarantined_items", [])
        )
        if any(
            item.transcript_sha256 != self.inputs.transcript.content_hash
            for item in self.quarantines
        ):
            raise ValueError("quarantine review requires this visit's transcript hash")
        self.targets = {target.id: target for target in (*self.outcomes, *self.quarantines)}
        if len(self.targets) != len(self.outcomes) + len(self.quarantines):
            raise ValueError("duplicate review target identity")
        legacy = tuple(_event(row) for row in extraction.get("quarantine_reviews", []))
        self.events = (*legacy, *read_events(self.log_path))
        self.active = active_events(self.events)

    def current(self, target: MappingOutcome | QuarantinedItem) -> ReviewEvent | None:
        return self.active.get((target.id, target_digest(target)))

    def allowed_actions(self, target: MappingOutcome | QuarantinedItem) -> list[str]:
        current = self.current(target)
        if current and current.action == "dismiss":
            return ["undo"]
        actions = ["dismiss"]
        if (
            isinstance(target, MappingOutcome)
            and LaneState.FAIL not in target.lanes.model_dump().values()
            and (current is None or current.action != "approve")
        ):
            actions.insert(0, "approve")
        if current:
            actions.append("undo")
        return actions

    def act(
        self,
        *,
        target_id: str,
        target_sha256: str,
        action: str,
        reviewer: str,
        role: str,
        reason: str = "",
    ) -> dict[str, Any]:
        self.load()
        target = self.targets.get(target_id)
        if target is None:
            raise ValueError("unknown review target")
        if target_digest(target) != target_sha256:
            raise ValueError("item version changed; reload before reviewing")
        previous = self.current(target)
        if action == "approve" and previous and previous.action == "approve":
            raise ValueError("this item version is already approved; choose Dismiss or Undo")
        if action not in self.allowed_actions(target):
            raise ValueError("action refused: approval cannot override any FAIL lane or quarantine")
        if action == "dismiss" and not reason.strip():
            raise ValueError("dismissal requires a reason")
        event = ReviewEvent(
            target_id=target.id,
            target_sha256=target_sha256,
            action=action,
            reviewer=reviewer,
            role=role,
            reason=reason,
            revokes=previous.entry_id if action == "undo" and previous else None,
            diff={
                "before": previous.action if previous else "unreviewed",
                "effect": {
                    "approve": "review confirmed; content and lanes unchanged; A3 rules apply",
                    "dismiss": "withheld from export and note record sections",
                    "undo": "revoked latest action; previous review state restored",
                }[action],
            },
        )
        append_private_jsonl(self.log_path, event.model_dump(mode="json"))
        # If projection fails, the receipt remains durable; reopening retries all files.
        self.load()
        return self.rewrite()

    def rewrite(self) -> dict[str, Any]:
        from auris.run import export_decision

        approvals = {}
        dismissed = {}
        effective = []
        for outcome in self.outcomes:
            event = self.current(outcome)
            derived = outcome
            if event and event.action == "approve":
                if LaneState.FAIL not in outcome.lanes.model_dump().values():
                    approvals[outcome.id] = (event.reviewer, event.role)
                if outcome.id in approvals:
                    derived = outcome.model_copy(
                        update={"reviewed_resource_digest": target_digest(outcome)}
                    )
            if event and event.action == "dismiss":
                dismissed[outcome.id] = event
            else:
                effective.append(derived)
        unresolved = tuple(item for item in self.quarantines if not self.current(item))
        decision = export_decision(
            self.inputs.intake,
            tuple(effective),
            self.inputs.validation,
            quarantined_items=unresolved,
            reviewed_ids=frozenset(approvals),
        )
        ready = decision.anchors_checked and not decision.blocking_ids
        bundle = build_collection(
            self.inputs.intake,
            effective if ready else (),
            validation=self.inputs.validation,
            preview=not ready,
        )
        # Include dismissed facts solely in the separate, labelled dismissal list.
        by_id = {o.id: o for o in effective}
        all_outcomes = tuple(by_id.get(o.id, o) for o in self.outcomes)
        items = note_items(
            all_outcomes, self.inputs.transcript, self.inputs.validation, approvals=approvals
        )
        rendered = []
        for item, outcome in zip(items, all_outcomes, strict=True):
            if event := dismissed.get(item.id):
                item = item.model_copy(
                    update={
                        "dismissed": True,
                        "dismissal": f"dismissed by {event.reviewer}, {event.role}: {event.reason}",
                    }
                )
            elif item.id in approvals and outcome.export_state in (
                "final_exportable",
                "ledger_only",
            ):
                # Denial coding still cannot be supplied by approval at tier zero.
                item = item.model_copy(
                    update={
                        "reasons": tuple(
                            reason
                            for reason in item.reasons
                            if "cannot represent this allergy denial" in reason
                        )
                    }
                )
            rendered.append(item)
        report = validation_document(self.inputs.intake, all_outcomes, self.inputs.validation)
        report["note_metadata"] = self.inputs.note_metadata.model_dump(mode="json")
        report["export"] = decision.model_dump(mode="json")
        item_by_id = {item.id: item for item in rendered}
        for row in report["resources"]:
            if item := item_by_id.get(row["id"]):
                row["note"] = item.model_dump(mode="json")
                if item.dismissed:
                    row["export_state"] = "dismissed"
        review_rows = []
        for target in self.targets.values():
            event = self.current(target)
            stale = [
                e.entry_id
                for e in self.events
                if e.target_id == target.id and e.target_sha256 != target_digest(target)
            ]
            review_rows.append(
                {
                    "id": target.id,
                    "target_sha256": target_digest(target),
                    "state": event.action if event else "unreviewed",
                    "receipt": event.model_dump(mode="json") if event else None,
                    "stale_entries": stale,
                    "stale_message": "Earlier review no longer applies: item version changed."
                    if stale
                    else None,
                    "allowed_actions": self.allowed_actions(target),
                }
            )
        stale_entries = [
            event.entry_id
            for event in self.events
            if event.target_id not in self.targets
            or event.target_sha256 != target_digest(self.targets[event.target_id])
        ]
        report["review"] = {
            "attribution_only": True,
            "items": review_rows,
            "stale_entries": stale_entries,
        }
        report["visit_review_items"] = [
            {**item.review_item(), "dismissed": bool(self.current(item))}
            for item in self.quarantines
        ]
        note = render_note(
            rendered,
            self.inputs.note_metadata,
            export_summary=decision.summary,
            malformed_item_count=len(self.quarantines),
            unresolved_malformed_count=len(unresolved),
        )
        quarantine_dismissals = [
            f"- {item.id} — malformed item withheld — dismissed by {_text(event.reviewer)}, "
            f"{_text(event.role)}: {_text(event.reason)} — checks failed, not validated"
            for item in self.quarantines
            if (event := self.current(item))
        ]
        if quarantine_dismissals:
            if not dismissed:
                note += "\n## Dismissed in review\n"
            note += "\n" + "\n".join(quarantine_dismissals) + "\n"
        write_private_text(self.directory / "note.md", note)
        write_bundle(self.directory / "bundle.json", bundle)
        write_private_text(self.directory / "validation.json", json.dumps(report, indent=2) + "\n")
        return {
            "ready": ready,
            "export": report["export"],
            "review": review_rows,
            "stale_review_count": len(stale_entries),
            "items": [item.model_dump(mode="json") for item in rendered],
            "lanes": {row["id"]: row["lanes"] for row in report["resources"]},
            "paths": {
                name: str((self.directory / name).absolute())
                for name in ("note.md", "bundle.json", "validation.json")
            },
        }
