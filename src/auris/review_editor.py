"""Activate attributed corrections and complete their private version checks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from auris._storage import append_private_jsonl
from auris.fhir.models import MappingOutcome, resource_digest
from auris.review_edits import (
    CorrectedOutcome,
    Version,
    checked_version,
    editable_assertion,
    field_diff,
    overlays,
    revision_context,
    unchecked,
    validation_resources,
    version_key,
)
from auris.validation.models import ValidationRun
from auris.validation.report import apply_validation

if TYPE_CHECKING:
    from auris.review_state import ReviewRun


class ReviewEditor:
    def __init__(self, run: ReviewRun) -> None:
        self.run = run

    def prepare_correction(
        self,
        *,
        target_id: str,
        target_sha256: str,
        reviewer: str,
        role: str,
        fields: dict[str, Any] | None = None,
        speaker_role: str | None = None,
        undo: bool = False,
    ) -> tuple[str, ...]:
        """Durably activate an overlay with unchecked lanes before starting the JVM."""
        run = self.run
        from auris.review_state import ReviewEvent, target_digest

        run.load()
        previous = run.correction_for(target_id)
        target = run.targets.get(target_id)
        if fields is not None or (undo and previous and previous.action == "edit"):
            if not isinstance(target, MappingOutcome) or target_id not in run.assertions:
                raise ValueError("this item has no editable assertion; regenerate the run")
            old_hash = target_digest(target)
        else:
            segment = run.transcript.segment_by_id(target_id)
            if segment is None or not any(
                e.segment_id == target_id for a in run.assertions.values() for e in a.evidence
            ):
                raise ValueError("only a cited transcript segment's role can be corrected")
            old_hash = resource_digest(segment.model_dump(mode="json"))
        if old_hash != target_sha256:
            raise ValueError("item version changed; reload before correcting")
        event = ReviewEvent(
            target_id=target_id,
            target_sha256=old_hash,
            action="revoke_edit" if undo else "edit" if fields is not None else "speaker",
            reviewer=reviewer,
            role=role,
            revokes=previous.entry_id if undo and previous else None,
            old_version_sha256=old_hash,
            diff={},
        )
        if undo:
            if previous is None:
                raise ValueError("no correction to undo")
        elif fields is not None:
            before = run.assertions[target_id]
            after = editable_assertion(before, fields)
            if after == before:
                raise ValueError("correction does not change any field")
            run.layer.corrections[event.entry_id] = after.model_dump(mode="json")
            run.layer.corrections[event.entry_id]["source_speaker_role"] = run.original_assertions[
                target_id
            ].model_dump(mode="json")["source_speaker_role"]
            event = event.model_copy(
                update={
                    "diff": field_diff(
                        before.model_dump(mode="json"), after.model_dump(mode="json")
                    )
                }
            )
        else:
            if speaker_role not in ("clinician", "patient", "other"):
                raise ValueError("speaker role must be clinician, patient or other")
            if speaker_role == segment.role.value:
                raise ValueError("correction does not change the speaker role")
            run.layer.corrections[event.entry_id] = {"role": speaker_role}
            event = event.model_copy(
                update={"diff": {"role": {"before": segment.role.value, "after": speaker_role}}}
            )
        assertions, transcript = overlays(
            run.original_assertions, run.inputs.transcript, run.layer, (*run.events, event)
        )
        new_keys = {
            identifier: version_key(
                assertion,
                transcript,
                run.originals[identifier],
                revision_context(identifier, assertion, (*run.events, event)),
            )
            for identifier, assertion in assertions.items()
        }
        changed = {
            identifier: assertion
            for identifier, assertion in assertions.items()
            if new_keys[identifier] != run.version_keys[identifier]
        }
        # Undo restores previously checked exact versions, including their receipts.
        needed = {
            identifier: assertion
            for identifier, assertion in changed.items()
            if new_keys[identifier]
            != version_key(
                run.original_assertions[identifier],
                run.inputs.transcript,
                run.originals[identifier],
            )
            and (not undo or new_keys[identifier] not in run.layer.versions)
        }
        mapped = (
            run.checker.map(run.inputs.intake, transcript, needed, run.originals, run.extraction)
            if needed
            else {}
        )
        keys = []
        for identifier, outcome in mapped.items():
            key = new_keys[identifier]
            outcome = CorrectedOutcome.model_validate(
                {
                    **outcome.model_dump(mode="json", exclude={"export_state"}),
                    "review_version_digest": key,
                }
            )
            validation = unchecked(
                validation_resources(run.inputs.intake, [outcome]), "re-checking"
            )
            run.layer.versions[key] = Version(
                outcome=apply_validation(outcome, validation),
                validation=validation,
                status="rechecking",
                message=(
                    "Re-checking has not finished. Final export is blocked; retry checks or undo."
                ),
            )
            keys.append(key)
        affected_versions = []
        for identifier in changed:
            key = new_keys[identifier]
            baseline = version_key(
                run.original_assertions[identifier],
                run.inputs.transcript,
                run.originals[identifier],
            )
            new_hash = target_digest(run.originals[identifier]) if key == baseline else key
            affected_versions.append(
                {
                    "target_id": identifier,
                    "before": target_digest(run.targets[identifier]),
                    "after": new_hash,
                }
            )
        new_hash = (
            next(row["after"] for row in affected_versions if row["target_id"] == target_id)
            if fields is not None or (undo and previous and previous.action == "edit")
            else resource_digest(transcript.segment_by_id(target_id).model_dump(mode="json"))
        )
        if undo:
            event = event.model_copy(
                update={
                    "diff": {
                        k: {"before": v["after"], "after": v["before"]}
                        for k, v in previous.diff.items()
                        if k != "affected_versions"
                    }
                }
            )
        event = event.model_copy(
            update={
                "new_version_sha256": new_hash,
                "diff": {**event.diff, "affected_versions": affected_versions},
            }
        )
        # Orphaned versions after an append failure are harmless: only log events activate them.
        run.layer.save(run.layer_path)
        append_private_jsonl(run.log_path, event.model_dump(mode="json"))
        run.load()
        run.rewrite()
        return tuple(keys)

    def recheck_resources(self, keys: tuple[str, ...]) -> list[dict[str, Any]]:
        run = self.run
        return validation_resources(
            run.inputs.intake, [run.layer.versions[k].outcome for k in keys]
        )

    def finish_recheck(self, keys: tuple[str, ...], validation: ValidationRun) -> dict[str, Any]:
        run = self.run
        run.load()
        resources = run.recheck_resources(keys)
        try:
            for resource in resources:
                validation.for_resource(resource)
            if validation.failure:
                validation = unchecked(resources, "validator could not finish the re-check")
        except ValueError:
            validation = unchecked(resources, "validator returned incomplete re-check results")
        for key in keys:
            run.layer.versions[key] = checked_version(run.layer.versions[key].outcome, validation)
        run.layer.save(run.layer_path)
        run.load()
        return run.rewrite()

    def recheck(self, keys: tuple[str, ...]) -> dict[str, Any]:
        run = self.run
        resources = run.recheck_resources(keys)
        try:
            validation = run.checker.validate(run.inputs.intake, run.directory, resources)
        except Exception:
            # Backend errors must not leave an old PASS or leak clinical text via HTTP errors.
            validation = unchecked(resources, "validator could not finish the re-check")
        return run.finish_recheck(keys, validation)

    def correct(self, **kwargs: Any) -> dict[str, Any]:
        run = self.run
        keys = run.prepare_correction(**kwargs)
        return run.recheck(keys) if keys else run.rewrite()
