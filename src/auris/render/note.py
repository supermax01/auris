"""Plain Python note templates. Clinical text comes only from mapping outcomes."""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from auris.fhir.models import ExportState, LaneState, MappingOutcome
from auris.render.reasons import reason_text
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
    # Stable order within a section (S1, S2, ...); used for sorting, never shown.
    reference: str
    # Who said it and when, in plain words ("patient, 0:13").
    source: str = ""
    approved: bool = False
    dismissed: bool = False
    dismissal: str | None = None

    def line(self) -> str:
        said = f" ({self.source})" if self.source else ""
        return f"- {self.fact}{said} — {self.label}"

    def review_line(self) -> str:
        """The item's open questions (or, once approved, its remaining notes)."""
        return f"  - {'Note' if self.approved else 'To check'}: {'; '.join(self.reasons)}"


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
            "explicit_new_order": "Order, not made into a record",
            "plan_item": "Plan",
            "social_history": "Social history",
            "vital_sign": "Measurement, not made into a record",
        }[outcome.kind]
        if ledger.get("medication_intent") == "discussed":
            prefix = "Discussed medicine (not ordered)"
        elif ledger.get("medication_intent") == "continuing":
            prefix = "Continuing medicine (not a new order)"
        if ledger.get("experiencer") == "family_member":
            prefix = "Family history (not the patient's condition)"
        elif ledger.get("experiencer") == "other":
            prefix += " (about someone else)"
        elif ledger.get("experiencer") in ("unknown", None):
            prefix += " (unclear who this is about)"
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
        fact = f"Draft order: {_code_text(resource['medicationCodeableConcept'])}"
        fact += f" — {dose}" if dose else " — dose not said"
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
        fact = "⚠ Not supported by its quote — " + fact
    return fact


def _source(outcome: MappingOutcome, transcript: Transcript) -> str:
    """Who said it and when, in plain words: "patient, 0:13; clinician, 0:30"."""
    parts = []
    fillers = False
    for item in outcome.evidence.items:
        segment = transcript.segment_by_id(item.segment_id)
        role = segment.role.value if segment else "speaker unknown"
        seconds = (
            item.aligned.audio_start_seconds
            if item.aligned
            else (segment.start_seconds if segment else None)
        )
        if transcript.timing_mode == "untimed":
            when = f"turn {segment.turn_number}" if segment else ""
        else:
            when = f"{int(seconds) // 60}:{int(seconds) % 60:02d}" if seconds is not None else ""
        part = f"{role}, {when}" if when else role
        if part not in parts:
            parts.append(part)
        fillers = fillers or bool(item.aligned and item.aligned.alignment_method == "disfluency-v2")
    return "; ".join(parts) + ("; matched ignoring filler words" if fillers else "")


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
        if item.aligned and item.aligned.alignment_method == "disfluency-v2":
            receipt += " (matched ignoring fillers/repeats)"
        if receipt not in receipts:
            receipts.append(receipt)
    return " ".join(receipts)


def awaiting_approval_only(outcome: MappingOutcome) -> bool:
    """Nothing flagged: only the every-record approval policy remains."""
    return (
        outcome.export_state == "needs_review"
        and outcome.resource is not None
        and not outcome.is_material
        and not outcome.review_reasons
        and not outcome.consistency.failures
        and not outcome.evidence.failures
    )


def _label(outcome: MappingOutcome, approval: tuple[str, str] | None = None) -> str:
    """The item's review and export status in plain words."""
    state = outcome.export_state
    labels = [
        "awaiting approval"
        if awaiting_approval_only(outcome)
        else {
            "needs_review": "⚠ needs review",
            "validation_failed": "⚠ checks failed, needs review",
            "pending_validation": "checks still running",
            "ledger_only": "note only",
            "final_exportable": "in the export file",
        }[state]
    ]
    if approval:
        labels.append(f"approved by {_text(approval[0])} ({_text(approval[1])})")
    if state == "ledger_only" and outcome.review_reasons and not approval:
        labels.append("needs review")
    if "uncoded_text_only" in outcome.mapper_flags:
        concept = (outcome.resource or {}).get("code", {}).get("text", "")
        family = outcome.kind == "allergy_statement" and normalise(concept) in DRUG_FAMILIES
        labels.append("drug family, kept as words" if family else "kept as words, no code")
    if outcome.lanes.export_checks_pass:
        if outcome.lanes.terminology == LaneState.PASS and state == "final_exportable":
            labels.append("validated")
        else:
            labels.append("checked against US Core")
    if outcome.lanes.terminology == LaneState.FAIL:
        labels.append("⚠ code checks failed")
    return " · ".join(labels)


def _reasons(outcome: MappingOutcome, validation: ValidationRun) -> tuple[str, ...]:
    codes = (*outcome.review_reasons, *outcome.consistency.failures)
    reasons = [reason_text(code) for code in codes]
    reasons.extend(reason_text(failure.value) for failure in outcome.evidence.failures)
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
    if not reasons and not awaiting_approval_only(outcome):
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
    outcomes: Sequence[MappingOutcome],
    transcript: Transcript,
    validation: ValidationRun,
    *,
    approvals: dict[str, tuple[str, str]] | None = None,
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
                label=_label(outcome, (approvals or {}).get(outcome.id)),
                reasons=_reasons(outcome, validation),
                in_ledger=outcome.ledger is not None,
                section=section,
                reference=f"{section}{counts[section]}",
                source=_source(outcome, transcript),
                approved=outcome.id in (approvals or {}),
            )
        )
    return tuple(items)


def render_note(
    items: Sequence[NoteItem],
    metadata: NoteMetadata,
    *,
    export_summary: str,
    malformed_item_count: int = 0,
    unresolved_malformed_count: int | None = None,
    dismissed_lines: Sequence[str] = (),
) -> str:
    dismissed = [item for item in items if item.dismissed]
    items = [item for item in items if not item.dismissed]
    lines = ["# Visit note", ""]
    if metadata.fictional_demo:
        lines.extend(
            [
                "**Fictional demo: no real patient data. Times are scripted; "
                "there is no recording.**",
                "",
            ]
        )
    to_check = [item for item in items if item.reasons and not item.approved]
    if to_check:
        noun = "item needs" if len(to_check) == 1 else "items need"
        lines.extend([f"**{len(to_check)} {noun} review**, marked ⚠ or “To check” below.", ""])
    lines.extend([_text(export_summary), ""])
    if malformed_item_count:
        unresolved = (
            malformed_item_count
            if unresolved_malformed_count is None
            else unresolved_malformed_count
        )
        status = "review required" if unresolved else "reviewed and dismissed with reasons"
        lines.extend(
            [
                f"**{malformed_item_count} extracted item(s) could not be read and were left out "
                f"— {status}**",
                "",
            ]
        )

    def block(selected: Sequence[NoteItem]) -> list[str]:
        out = []
        for item in selected:
            out.append(item.line())
            if item.reasons:
                out.append(item.review_line())
        return out or ["Nothing recorded."]

    sections = (
        ("Subjective", "S", "Symptoms and denials stay in this note; they are not exported."),
        ("Objective", "O", None),
        ("Assessment", "A", None),
        ("Plan", "P", None),
    )
    for title, section, hint in sections:
        lines.extend([f"## {title}", ""])
        if hint:
            lines.extend([f"_{hint}_", ""])
        lines.extend(block([item for item in items if item.section == section]))
        lines.append("")
    lines.extend(["## Also discussed", ""])
    lines.extend(["_Kept in this note, not exported as records._", ""])
    lines.extend(block([item for item in items if item.in_ledger and item.section == "D"]))
    if dismissed or dismissed_lines:
        lines.extend(["", "## Dismissed", ""])
        lines.extend(
            f"- {item.fact}"
            + (f" ({item.source})" if item.source else "")
            + f" — {_text(item.dismissal or 'dismissed')}"
            for item in dismissed
        )
        lines.extend(dismissed_lines)
    timing = (
        "Items cite turn numbers because the transcript has no times."
        if items and all(item.source and "turn " in item.source for item in items)
        else "Times are from the transcript and approximate."
    )
    lines.extend(
        [
            "",
            "---",
            "",
            "Written by Auris from the visit transcript. Every item quotes what was said, "
            f"by whom and when. {timing} Records are checked against US Core; medical codes "
            "are not checked against a terminology server.",
            "",
            f"Model {_text(metadata.model_id)} · prompt {_text(metadata.prompt_version)} · "
            f"written {_text(metadata.run_timestamp)}. {SCOPE}",
        ]
    )
    return "\n".join(lines) + "\n"
