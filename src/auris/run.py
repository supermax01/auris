"""Complete a text-in run after unloading the model; preserve the A4 export gate."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from auris._storage import write_private_text
from auris.extract.pipeline import ExtractionRun, finish_pipeline
from auris.extract.prompt import PROMPT_VERSION
from auris.extract.quarantine import QuarantinedItem, QuarantineReview
from auris.fhir import Mapper
from auris.fhir.bundle import build_collection, write_bundle
from auris.fhir.common import build_anchors
from auris.fhir.models import LaneState, MappingOutcome
from auris.intake import Intake
from auris.render import NoteMetadata, note_items, render_note
from auris.review import unresolved_quarantines
from auris.validation.models import ValidationRun
from auris.validation.report import validation_document
from auris.validation.runner import ValidatorRunner


class ExportDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    exported_ids: tuple[str, ...]
    blocking_ids: tuple[str, ...]
    anchors_checked: bool
    summary: str


def export_decision(
    intake: Intake,
    outcomes: tuple[MappingOutcome, ...],
    validation: ValidationRun,
    *,
    quarantined_items: tuple[QuarantinedItem, ...] = (),
    quarantine_reviews: tuple[QuarantineReview, ...] = (),
) -> ExportDecision:
    anchors = build_anchors(intake)
    anchors_checked = all(
        validation.for_resource(resource).base_r4.state == LaneState.PASS
        and validation.for_resource(resource).us_core_profile.state == LaneState.PASS
        for resource in (anchors.patient, anchors.practitioner, anchors.encounter)
    )
    blockers = tuple(
        outcome.id
        for outcome in outcomes
        if outcome.is_material and outcome.export_state not in ("final_exportable", "ledger_only")
    )
    blockers += unresolved_quarantines(quarantined_items, quarantine_reviews)
    exported = (
        tuple(outcome.id for outcome in outcomes if outcome.export_state == "final_exportable")
        if anchors_checked and not blockers
        else ()
    )
    if exported:
        summary = (
            f"Bundle: {len(exported)} eligible extracted record(s) and supplied intake anchors."
        )
    else:
        summary = "Bundle: intake anchors only; no extracted records exported."
    if blockers:
        summary += " Unresolved material assertions block final export (PLAN A4)."
    if not anchors_checked:
        summary += " Intake anchors have not passed FHIR and profile checks."
    if validation.failure:
        summary += (
            " Validator NOT_CHECKED; see validation.json for the reason. "
            "Setup: auris setup fhir; auris setup validator --fetch."
        )
    summary += " Coding not checked (tier 0)."
    return ExportDecision(
        exported_ids=exported,
        blocking_ids=blockers,
        anchors_checked=anchors_checked,
        summary=summary,
    )


def finish_run(
    run: ExtractionRun,
    mapper: Mapper,
    validator: ValidatorRunner,
    output_dir: Path,
    *,
    fictional_demo: bool = False,
) -> ExportDecision | None:
    outcomes, validation = finish_pipeline(run, mapper, validator, output_dir)
    if validation is None:
        return None  # Failed extraction retains diagnostics, never a success note/bundle.
    decision = export_decision(
        mapper.intake,
        outcomes,
        validation,
        quarantined_items=tuple(run.quarantined_items),
        quarantine_reviews=tuple(run.quarantine_reviews),
    )
    if decision.blocking_ids or not decision.anchors_checked:
        # Explicit anchors-only transport, NOT a preview of unreviewed clinical resources.
        # Their real validation states remain visible in validation.json and the note.
        bundle = build_collection(mapper.intake, (), preview=True)
    else:
        bundle = build_collection(
            mapper.intake,
            outcomes,
            validation=validation,
            quarantined_items=run.quarantined_items,
            quarantine_reviews=run.quarantine_reviews,
        )
    items = note_items(outcomes, mapper.transcript, validation)
    document = validation_document(mapper.intake, outcomes, validation)
    document["export"] = decision.model_dump(mode="json")
    document["visit_review_items"] = [
        {
            **item.review_item(),
            "dismissed": item.id
            not in unresolved_quarantines(run.quarantined_items, run.quarantine_reviews),
        }
        for item in run.quarantined_items
    ]
    by_id = {item.id: item for item in items}
    for row in document["resources"]:
        if row["id"] in by_id:
            row["note"] = by_id[row["id"]].model_dump(mode="json")
    metadata = NoteMetadata(
        model_id=run.model.id,
        prompt_version=PROMPT_VERSION,
        run_timestamp=run.started_at,
        fictional_demo=fictional_demo,
    )
    document["note_metadata"] = metadata.model_dump(mode="json")
    write_private_text(output_dir / "validation.json", json.dumps(document, indent=2) + "\n")
    write_bundle(output_dir / "bundle.json", bundle)
    write_private_text(
        output_dir / "note.md",
        render_note(
            items,
            metadata,
            export_summary=decision.summary,
            malformed_item_count=len(run.quarantined_items),
            unresolved_malformed_count=len(
                unresolved_quarantines(run.quarantined_items, run.quarantine_reviews)
            ),
        ),
    )
    return decision
