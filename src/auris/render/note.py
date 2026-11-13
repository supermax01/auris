"""Plain Python note templates. Clinical text comes only from mapping outcomes."""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from auris.fhir.models import ExportState, LaneState, MappingOutcome
from auris.terminology.text import DRUG_FAMILIES, normalise
from auris.transcript import Transcript
from auris.validation.models import ValidationRun

SCOPE = (
    "Research software, not cleared or approved for clinical use; clinician review mandatory. "
    "Regulatory classification is deployment-specific."
)


class NoteMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    model_id: str
    prompt_version: str
    run_timestamp: str
    fictional_demo: bool = False


class NoteItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    kind: str
    export_state: ExportState
    fact: str
    receipts: str
    label: str
    reasons: tuple[str, ...]
    in_ledger: bool
    section: Literal["S", "O", "A", "P", "D"]
    reference: str

    def line(self) -> str:
        return f"- {self.reference} — {self.fact} {self.receipts} — {self.label}"

    def review_line(self) -> str:
        return f"- {self.reference} {self.receipts} — {self.label} — {'; '.join(self.reasons)}"


def _text(value: object) -> str:
    """Keep arbitrary quoted text on one inert Markdown line."""
    flattened = " ".join(str(value).split())
    return re.sub(r"([\\`*_\[\]#])", r"\\\1", html.escape(flattened, quote=False))


def _code_text(concept: dict[str, Any]) -> str:
    # The mapper preserves spoken concept.text, including text-only concepts.
    return _text(concept.get("text") or "name not captured")


def _status(resource: dict[str, Any], field: str) -> str:
    codes = resource.get(field, {}).get("coding", [])
    return _text(codes[0]["code"]) if codes else "status not captured"


def _fact(outcome: MappingOutcome) -> str:
    resource = outcome.resource
    if resource is None:
        ledger = outcome.ledger or {}
        prefix = {
            "symptom": "Symptom",
            "reported_medication": "Reported medicine",
            "allergy_denial": "Allergy denial",
            "allergy_statement": "Allergy statement",
            "patient_reported_condition": "Patient-reported condition",
            "clinician_assessed_problem": "Assessed problem",
            "clinician_ruled_out_problem": "Ruled-out problem",
            "explicit_new_order": "Proposed order (no record)",
            "vital_sign": "Measurement (no record)",
        }[outcome.kind]
        if ledger.get("medication_intent") == "discussed":
            prefix = "Discussed medicine (not ordered)"
        elif ledger.get("medication_intent") == "continuing":
            prefix = "Continuing medicine (not a new order)"
        if ledger.get("experiencer") == "family_member":
            prefix = "Family history (not the patient's condition)"
        elif ledger.get("experiencer") in ("other", "unknown", None):
            prefix += " (" + str(ledger.get("experiencer") or "unknown") + " experiencer)"
        quotes = dict.fromkeys(
            item.aligned.aligned_text if item.aligned else item.quote
            for item in outcome.evidence.items
        )
        fact = prefix + ": " + "; ".join(f"“{_text(quote)}”" for quote in quotes)
    elif resource["resourceType"] == "Observation":
        # Fixed vital codings need not carry text/display. The claimed specific
        # profile already names the measurement; do not invent a terminology lookup.
        profile_name = (outcome.claimed_profile or "").rsplit("/", 1)[-1]
        concept = _text(profile_name.removeprefix("us-core-").replace("-", " ").capitalize())
        if "component" in resource:
            quantities = [part["valueQuantity"] for part in resource["component"]]
            value = "/".join(str(q["value"]) for q in quantities)
            fact = f"{concept} {_text(value)} {_text(quantities[0]['unit'])}"
        else:
            quantity = resource["valueQuantity"]
            fact = f"{concept} {_text(quantity['value'])} {_text(quantity['unit'])}"
    elif resource["resourceType"] == "Condition":
        prefix = {
            "clinician_assessed_problem": "Assessed",
            "clinician_ruled_out_problem": "Ruled out",
            "patient_reported_condition": "Patient-reported",
        }[outcome.kind]
        status = _status(resource, "verificationStatus")
        if "clinicalStatus" in resource:
            status += ", " + _status(resource, "clinicalStatus")
        fact = f"{prefix}: {_code_text(resource['code'])} ({status})"
    elif resource["resourceType"] == "MedicationRequest":
        dose = "; ".join(
            _text(d["text"]) for d in resource.get("dosageInstruction", []) if d.get("text")
        )
        fact = f"DRAFT — {_code_text(resource['medicationCodeableConcept'])}"
        fact += f" — {dose}" if dose else " — dose not captured"
    else:
        fact = (
            f"Allergy: {_code_text(resource['code'])} ({_status(resource, 'verificationStatus')})"
        )
        reactions = [
            _code_text(manifestation)
            for reaction in resource.get("reaction", [])
            for manifestation in reaction["manifestation"]
        ]
        if reactions:
            fact += "; stated reaction: " + "; ".join(reactions)
    if outcome.lanes.evidence != LaneState.PASS:
        fact = "Unconfirmed extraction — " + fact
    return fact


def _receipts(outcome: MappingOutcome, transcript: Transcript) -> str:
    receipts = []
    for item in outcome.evidence.items:
        segment = transcript.segment_by_id(item.segment_id)
        role = segment.role.value if segment else "unknown"
        seconds = (
            item.aligned.audio_start_seconds
            if item.aligned
            else (segment.start_seconds if segment else None)
        )
        stamp = (
            f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}" if seconds is not None else "--:--"
        )
        if transcript.timing_mode == "untimed":
            stamp = f"turn {segment.turn_number}" if segment else "turn unknown"
        receipt = f"[{stamp} · {role}]"
        if receipt not in receipts:
            receipts.append(receipt)
    return " ".join(receipts)


def _label(outcome: MappingOutcome) -> str:
    labels = []
    if outcome.lanes.evidence == LaneState.PASS:
        labels.append("transcript-supported")
    state = outcome.export_state
    labels.append(
        {
            "needs_review": "⚠ needs clinician review · not validated",
            "validation_failed": "⚠ checks failed · needs clinician review · not validated",
            "pending_validation": "checks incomplete · not validated",
            "ledger_only": "ledger only",
            "final_exportable": "eligible for export",
        }[state]
    )
    if state == "ledger_only" and outcome.review_reasons:
        labels.append("needs clinician review · not validated")
    if "uncoded_text_only" in outcome.mapper_flags:
        concept = (outcome.resource or {}).get("code", {}).get("text", "")
        family = outcome.kind == "allergy_statement" and normalise(concept) in DRUG_FAMILIES
        labels.append(
            ("drug family, kept as text" if family else "kept as text") + " · needs coding review"
        )
    if outcome.lanes.export_checks_pass:
        if outcome.lanes.terminology == LaneState.PASS and state == "final_exportable":
            labels.append("validated")
        else:
            labels.append("profile-checked")
    if outcome.lanes.terminology == LaneState.NOT_CHECKED:
        labels.append("coding not checked")
    elif outcome.lanes.terminology == LaneState.FAIL:
        labels.append("coding checks failed")
    return " · ".join(labels)


_REASONS = {
    "medication": "Medicines require clinician review",
    "allergy": "Allergies require clinician review",
    "negated": "A denial requires review before any record export",
    "refuted": "The ruled-out problem requires clinician confirmation",
    "non_patient_experiencer": "This statement concerns someone other than the patient",
    "patient_experiencer_required": "Cannot create a patient record for another person",
    "snomed_not_supplied": (
        "The installed code set cannot represent this allergy denial as a record"
    ),
    "clinician_confirmation_required": "Clinician confirmation is required",
    "needs_coding_review": "A clinician must review the coding",
    "uncoded_text_only": "No unambiguous local code; text retained",
    "evidence_failed": "The quote could not be safely aligned to the transcript",
    "consistency_failed": "Extracted fields do not agree with the quoted evidence",
    "concept_text_not_in_quote": "The extracted concept does not occur in the quote",
    "allergen_text_not_in_quote": "The extracted allergen does not occur in the quote",
    "value_text_number_not_in_quote": "An extracted measurement number does not occur in the quote",
    "dose_text_number_not_in_quote": "An extracted dose number does not occur in the quote",
    "dosage_text_not_in_quote": (
        "The dose, route or schedule could not be located verbatim in the quoted evidence"
    ),
    "merge_conflict": "Overlapping extraction windows disagree",
    "polarity_conflicts_with_quote": "The assertion conflicts with a denial in the quote",
    "negation_not_in_quote": "The extracted denial has no denial cue in the quote",
    "experiencer_cue_conflict": "The person assigned to this fact conflicts with the quote",
    "source_role_mismatch": "The extracted speaker role differs from the cited speaker",
    "kind_role_mismatch": "This kind of assertion is not supported by the cited speaker role",
    "unit_missing": "The measurement unit was not stated",
    "bp_component_missing": "One blood pressure component is missing",
    "unit_not_in_profile_set": "The measurement unit is not allowed by the claimed profile",
    "historical_without_date": "A historical reading needs an explicit date",
    "prescriber_not_identified": "The prescribing clinician could not be identified",
    "not_explicit_new_order": "Discussion does not establish a new order",
    "polarity_not_stated": "Whether this fact was affirmed or denied is unclear",
    "experiencer_unknown": "The person this statement concerns is unknown",
    "concept_text_missing": "The clinical name was not captured",
    "source_role_unknown": "The speaker's role is unknown",
    "historical_condition": "A historical or resolved condition needs clinician review",
    "condition_temporality_unclear": "The condition's current status is unclear",
    "allergy_resolution_stated": "Reported allergy resolution requires clinician confirmation",
    "temporality_unclear": "The time or current status of the statement is unclear",
    "polarity_kind_mismatch": "The assertion type disagrees with whether it was affirmed or denied",
    "clinician_not_identified": "A clinician has not been identified as supporting this assessment",
    "order_temporality_unclear": "It is unclear whether this is a current or future order",
    "allergen_not_stated": "The allergen was not stated",
    "terminology_release_out_of_period": "The code release does not cover the encounter date",
    "vital_not_supported": "This measurement stays in the ledger in version 1",
    "value_missing_or_unparseable": "The measurement value is missing or cannot be read safely",
    "measurement_date_not_day_precision": "The measurement needs a stated date including the day",
    "vital_temporality_unclear": "The measurement time is unclear",
}


def _reasons(outcome: MappingOutcome, validation: ValidationRun) -> tuple[str, ...]:
    codes = (*outcome.review_reasons, *outcome.consistency.failures)
    reasons = [_REASONS.get(code, code.replace("_", " ").capitalize()) for code in codes]
    reasons.extend(
        failure.value.replace("_", " ").capitalize() for failure in outcome.evidence.failures
    )
    for name, title in (("reduced_schema", "Extracted form"), ("evidence", "Evidence")):
        if getattr(outcome.lanes, name) == LaneState.FAIL:
            reasons.append(f"{title} checks failed")
    if outcome.resource:
        result = validation.for_resource(outcome.resource)
        for name, title in (
            ("base_r4", "FHIR rules"),
            ("us_core_profile", "US Core profile"),
            ("terminology", "Medical coding"),
        ):
            lane = getattr(result, name)
            if lane.state == LaneState.FAIL:
                reasons.append(f"{title} checks failed")
                reasons.extend(
                    issue.text or issue.diagnostics or issue.code
                    for issue in lane.issues
                    if issue.severity in ("error", "fatal")
                )
            elif lane.state == LaneState.NOT_CHECKED and name != "terminology":
                reasons.append(f"{title} not checked: {lane.reason or 'checker unavailable'}")
    if not reasons:
        if outcome.export_state == "pending_validation":
            reasons.append("Validation must be completed for this record")
        elif outcome.export_state == "needs_review":
            reasons.append("Clinician review is required")
        elif outcome.export_state == "validation_failed":
            reasons.append("Validation checks failed")
    return tuple(dict.fromkeys(_text(reason) for reason in reasons))


def _section(outcome: MappingOutcome) -> Literal["S", "O", "A", "P", "D"]:
    if (outcome.ledger or {}).get("experiencer") == "family_member":
        return "A"
    if outcome.kind in ("symptom", "allergy_statement", "allergy_denial"):
        return "S"
    if outcome.kind == "vital_sign":
        return "O"
    if outcome.kind in (
        "clinician_assessed_problem",
        "clinician_ruled_out_problem",
        "patient_reported_condition",
    ):
        return "A"
    if outcome.kind == "explicit_new_order":
        return "P"
    return "D"


def note_items(
    outcomes: Sequence[MappingOutcome], transcript: Transcript, validation: ValidationRun
) -> tuple[NoteItem, ...]:
    items: list[NoteItem] = []
    counts: dict[str, int] = {}
    for outcome in outcomes:
        section = _section(outcome)
        counts[section] = counts.get(section, 0) + 1
        items.append(
            NoteItem(
                id=outcome.id,
                kind=outcome.kind,
                export_state=outcome.export_state,
                fact=_fact(outcome),
                receipts=_receipts(outcome, transcript),
                label=_label(outcome),
                reasons=_reasons(outcome, validation),
                in_ledger=outcome.ledger is not None,
                section=section,
                reference=f"{section}{counts[section]}",
            )
        )
    return tuple(items)


def render_note(items: Sequence[NoteItem], metadata: NoteMetadata, *, export_summary: str) -> str:
    lines = ["# Auris visit summary", ""]
    if metadata.fictional_demo:
        lines.extend(
            ["**FICTIONAL DEMO — no real patient data; scripted times, no recording.**", ""]
        )
    lines.extend(
        [
            SCOPE,
            "",
            f"Model: {_text(metadata.model_id)} · Prompt: {_text(metadata.prompt_version)} · "
            f"Run: {_text(metadata.run_timestamp)}",
            "",
            (
                "Evidence cites turn numbers for untimed transcripts; no audio time is inferred."
                if any("[turn " in item.receipts for item in items)
                else "Evidence times use aligned audio offsets or segment starts; "
                "segment times are approximate."
            ),
            "",
            "Review entries refer to the item labels below (S, O, A, P and D).",
            "",
            _text(export_summary),
            "",
        ]
    )
    sections = (
        (
            "S — Subjective (quoted from the transcript; symptoms are not FHIR records in v1)",
            "S",
        ),
        ("O — Objective", "O"),
        ("A — Assessment", "A"),
        ("P — Plan", "P"),
    )
    for title, section in sections:
        lines.extend([f"## {title}", ""])
        lines.extend([item.line() for item in items if item.section == section] or ["not captured"])
        lines.append("")
    lines.extend(["## Needs review — not validated", ""])
    lines.extend([item.review_line() for item in items if item.reasons] or ["not captured"])
    lines.extend(["", "## Discussed, not captured as records", ""])
    lines.extend(
        [item.line() for item in items if item.in_ledger and item.section == "D"]
        or ["not captured"]
    )
    return "\n".join(lines) + "\n"
