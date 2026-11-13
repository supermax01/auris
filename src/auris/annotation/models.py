"""Independent annotation documents; deliberately separate from adjudicated gold."""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal, Self

from pydantic import Field, model_validator

from auris.assertions import AssertionKind, MedicationIntent, Polarity
from auris.datasets.splits import PublicSplit
from auris.eval.gold import GoldAssertion, Identifier, OutOfScopeFact, Record, Sha256, Span, digest
from auris.transcript import SpeakerRole, Transcript


class Assignment(Record):
    schema_version: Literal["assignment-1"] = "assignment-1"
    annotator_id: Identifier
    dataset: PublicSplit
    encounter_ids: tuple[Identifier, ...] = Field(min_length=1)
    guidelines_sha256: Sha256

    @model_validator(mode="after")
    def membership(self) -> Self:
        if len(set(self.encounter_ids)) != len(self.encounter_ids):
            raise ValueError("duplicate assignment encounter")
        if not set(self.encounter_ids) <= {e.id for e in self.dataset.encounters}:
            raise ValueError("assignment encounter outside split")
        return self


class AnnotationFact(GoldAssertion):
    notes: str = ""

    @model_validator(mode="after")
    def annotation_semantics(self) -> Self:
        if self.gold_concept_id.startswith("auris:") and not re.fullmatch(
            r"auris:[a-z0-9]+(?:-[a-z0-9]+)*", self.gold_concept_id
        ):
            raise ValueError("use a lowercase, hyphen-separated auris: slug")
        if self.canonical_value is not None:
            if self.kind != AssertionKind.VITAL_SIGN:
                raise ValueError("canonical measurements belong only to vital signs")
            try:
                if not self.canonical_value or not all(
                    Decimal(value).is_finite() for value in self.canonical_value
                ):
                    raise ValueError("canonical values must be finite decimal strings")
            except InvalidOperation:
                raise ValueError("canonical values must be decimal strings") from None
        if self.ucum_unit is not None and self.kind != AssertionKind.VITAL_SIGN:
            raise ValueError("canonical units belong only to vital signs")
        condition_kinds = {
            AssertionKind.CLINICIAN_ASSESSED_PROBLEM,
            AssertionKind.CLINICIAN_RULED_OUT_PROBLEM,
            AssertionKind.PATIENT_REPORTED_CONDITION,
        }
        if self.kind not in condition_kinds and self.critical.condition_assertion != "none":
            raise ValueError("condition status belongs only to condition kinds")
        if (
            self.kind not in (AssertionKind.EXPLICIT_NEW_ORDER, AssertionKind.REPORTED_MEDICATION)
            and self.critical.order_authorization != "none"
        ):
            raise ValueError("order authorization belongs only to medication kinds")
        if self.kind == AssertionKind.ALLERGY_DENIAL:
            specific = self.critical.allergy_denial == "specific"
            if specific != bool(self.allergen_text and self.allergen_text.strip()):
                raise ValueError("specific denial needs an allergen; general NKA leaves it blank")
        if self.kind in (
            AssertionKind.CLINICIAN_ASSESSED_PROBLEM,
            AssertionKind.CLINICIAN_RULED_OUT_PROBLEM,
        ):
            if self.source_speaker_role != SpeakerRole.CLINICIAN:
                raise ValueError("clinician problem requires clinician source role")
            expected = (
                Polarity.NEGATED
                if self.kind == AssertionKind.CLINICIAN_RULED_OUT_PROBLEM
                else Polarity.AFFIRMED
            )
            if self.polarity != expected:
                raise ValueError("problem kind contradicts polarity")
            status = "absent" if expected == Polarity.NEGATED else "present"
            if self.critical.condition_assertion != status:
                raise ValueError("problem kind contradicts critical condition status")
        if (
            self.kind == AssertionKind.REPORTED_MEDICATION
            and self.medication_intent == MedicationIntent.NEW_ORDER
        ):
            raise ValueError("reported medication cannot be a new order")
        if self.kind == AssertionKind.ALLERGY_STATEMENT and self.polarity != Polarity.AFFIRMED:
            raise ValueError("allergy statement must be affirmed; use allergy denial")
        if self.kind == AssertionKind.PATIENT_REPORTED_CONDITION:
            expected = "absent" if self.polarity == Polarity.NEGATED else "present"
            if self.critical.condition_assertion != expected:
                raise ValueError("reported condition contradicts critical status")
        return self


class ImportantFact(OutOfScopeFact):
    notes: str = ""


class Timing(Record):
    active_seconds: float = Field(default=0, ge=0)
    wall_seconds: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if self.active_seconds > self.wall_seconds + 0.001:
            raise ValueError("active time exceeds wall time")
        return self


class AnnotationDocument(Record):
    schema_version: Literal["annotation-1"] = "annotation-1"
    annotator_id: Identifier
    encounter_id: Identifier
    transcript_sha256: Sha256
    guidelines_sha256: Sha256
    started_at: datetime
    updated_at: datetime
    status: Literal["in_progress", "done"] = "in_progress"
    timing: Timing = Field(default_factory=Timing)
    assertions: tuple[AnnotationFact, ...] = ()
    out_of_scope: tuple[ImportantFact, ...] = ()
    # ACI source tags may be swapped. Human role decisions are explicit and separate.
    segment_roles: dict[str, SpeakerRole] = Field(default_factory=dict)
    notes: str = ""

    @model_validator(mode="after")
    def consistent(self) -> Self:
        ids = [f.id for f in (*self.assertions, *self.out_of_scope)]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate annotation fact id")
        if self.started_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("annotation timestamps require timezone")
        if self.updated_at < self.started_at:
            raise ValueError("annotation update predates start")
        return self

    def verify(self, transcript: Transcript) -> None:
        if self.transcript_sha256 != transcript.content_hash:
            raise ValueError("annotation transcript hash mismatch")
        if not set(self.segment_roles) <= {s.id for s in transcript.segments}:
            raise ValueError("unknown segment in role decisions")
        if self.status == "done" and set(self.segment_roles) != {s.id for s in transcript.segments}:
            raise ValueError("confirm a role for every turn before marking done")
        for fact in (*self.assertions, *self.out_of_scope):
            for span in fact.evidence:
                span.verify(transcript)
                segment = transcript.segment_by_id(span.segment_id)
                assert segment is not None
                if not segment.text[span.char_start : span.char_end].strip():
                    raise ValueError("evidence must include words, not only whitespace")


class Selection(Record):
    segment_id: str
    char_start: int = Field(ge=0, strict=True)
    char_end: int = Field(gt=0, strict=True)
    text: str = Field(min_length=1)
    sha256: Sha256

    def span(self, transcript: Transcript) -> Span:
        segment = transcript.segment_by_id(self.segment_id)
        if segment is None or not 0 <= self.char_start < self.char_end <= len(segment.text):
            raise ValueError("selection outside one transcript turn")
        text = segment.text[self.char_start : self.char_end]
        if not text.strip() or text != self.text or digest(text.encode("utf-8")) != self.sha256:
            raise ValueError("selection text/hash mismatch")
        return Span.model_validate(self.model_dump(exclude={"text"}))
