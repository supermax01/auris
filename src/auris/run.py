"""Complete a text-in run after unloading the model; preserve the A4 export gate."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from auris._storage import write_private_text
from auris.extract.pipeline import ExtractionRun, finish_pipeline
from auris.extract.prompt import PROMPT_VERSION
from auris.extract.quarantine import QuarantinedItem, QuarantineReview
from auris.fhir import Mapper
from auris.fhir.common import build_anchors
from auris.fhir.models import LaneState, MappingOutcome
from auris.intake import Intake
from auris.render import NoteMetadata
from auris.review import unresolved_quarantines
from auris.validation.models import ValidationRun
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
    reviewed_ids: frozenset[str] = frozenset(),
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
        if outcome.is_material
        and outcome.export_state != "final_exportable"
        and not (
            outcome.export_state == "ledger_only"
            and (
                outcome.id in reviewed_ids
                or (
                    outcome.kind in ("symptom", "patient_reported_condition")
                    and set(outcome.review_reasons) <= {"negated"}
                )
            )
        )
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
    # Retain only immutable inputs alongside extraction.json. All mutable outputs
    # are derived by the same code used after each clinician review action.
    from auris.review_state import ReviewInputs, ReviewRun

    inputs = ReviewInputs(
        intake=mapper.intake,
        transcript=mapper.transcript,
        validation=validation,
        note_metadata=NoteMetadata(
            model_id=run.model.id,
            prompt_version=PROMPT_VERSION,
            run_timestamp=run.started_at,
            fictional_demo=fictional_demo,
        ),
    )
    write_private_text(output_dir / "review-inputs.json", inputs.model_dump_json(indent=2) + "\n")
    return ExportDecision.model_validate(ReviewRun(output_dir).rewrite()["export"])
