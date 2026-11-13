"""Adjudicated stand-off gold; no production terminology dependencies."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from auris.assertions import (
    ASSERTION_MODELS,
    AssertionKind,
    Experiencer,
    MedicationIntent,
    Polarity,
    Temporality,
    parse_assertion,
)
from auris.transcript import SpeakerRole, Transcript

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]+$")]
Split = Literal["dev", "test", "harness-check"]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Span(Record):
    segment_id: str = Field(min_length=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    sha256: Sha256

    @model_validator(mode="after")
    def extent(self) -> Self:
        if self.char_end <= self.char_start:
            raise ValueError("span must be nonempty")
        return self

    def verify(self, transcript: Transcript) -> None:
        segment = transcript.segment_by_id(self.segment_id)
        if segment is None or self.char_end > len(segment.text):
            raise ValueError("gold evidence outside transcript")
        if digest(segment.text[self.char_start : self.char_end].encode()) != self.sha256:
            raise ValueError("gold evidence hash mismatch")


class Code(Record):
    system: str = Field(min_length=1)
    code: str = Field(min_length=1)


class Fact(Record):
    """Reduced-schema fields with stand-off evidence and optional scoring projections.

    Nullable fields have exactly the production schema's meaning. Only fields
    belonging to the selected kind may be populated. Codes are mapper/adjudicator
    outputs, never extractor-generated codes.
    """

    kind: AssertionKind
    concept_text: str | None
    polarity: Polarity | None
    experiencer: Experiencer | None
    temporality: Temporality | None
    source_speaker_role: SpeakerRole | None
    evidence: tuple[Span, ...]
    value_text: str | None = None
    unit_text: str | None = None
    stated_time_text: str | None = None
    medication_intent: MedicationIntent | None = None
    dose_text: str | None = None
    route_text: str | None = None
    frequency_text: str | None = None
    allergen_text: str | None = None
    reaction_manifestation_text: str | None = None
    stated_resolution_text: str | None = None
    codes: tuple[Code, ...] = ()
    # Decimal strings preserve exactness; BP is systolic then diastolic.
    canonical_value: tuple[str, ...] | None = None
    ucum_unit: str | None = None

    @model_validator(mode="after")
    def reduced_schema(self) -> Self:
        # Literal discriminator defaults are undefined; inspect their annotation.
        from typing import get_args

        fields = next(
            m.model_fields
            for m in ASSERTION_MODELS
            if get_args(m.model_fields["kind"].annotation) == (self.kind,)
        )
        clinical_fields = set().union(*(m.model_fields for m in ASSERTION_MODELS))
        data = self.model_dump(mode="json")
        if any(data.get(key) is not None for key in clinical_fields - fields.keys()):
            raise ValueError("field does not belong to assertion kind")
        parse_assertion(
            {
                key: (
                    [{"segment_id": "stand-off", "quote": "placeholder"}]
                    if key == "evidence"
                    else data[key]
                )
                for key in fields
            }
        )
        return self

    def extraction_dict(self, transcript: Transcript) -> dict:
        """Rehydrate gold only for harness tests, never for production inference."""
        from typing import get_args

        fields = next(
            m.model_fields
            for m in ASSERTION_MODELS
            if get_args(m.model_fields["kind"].annotation) == (self.kind,)
        )
        result = {key: self.model_dump(mode="json")[key] for key in fields}
        result["evidence"] = []
        for span in self.evidence:
            span.verify(transcript)
            segment = transcript.segment_by_id(span.segment_id)
            assert segment is not None
            result["evidence"].append(
                {
                    "segment_id": span.segment_id,
                    "quote": segment.text[span.char_start : span.char_end],
                }
            )
        return result


class CriticalAttributes(Record):
    allergy_denial: Literal["none", "specific", "all"] = "none"
    condition_assertion: Literal["none", "present", "absent", "uncertain"] = "none"
    order_authorization: Literal["none", "explicit", "not_ordered"] = "none"


class GoldAssertion(Fact):
    id: Identifier
    gold_concept_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*:[^\s]+$")
    acceptable_texts: tuple[str, ...] = Field(min_length=1)
    evidence: tuple[Span, ...] = Field(min_length=1)
    critical: CriticalAttributes

    @model_validator(mode="after")
    def adjudicated(self) -> Self:
        if any(not text.strip() for text in self.acceptable_texts):
            raise ValueError("acceptable texts must not be blank")
        if self.experiencer in (None, Experiencer.UNKNOWN) or self.polarity is None:
            raise ValueError("gold requires adjudicated experiencer and polarity")
        if self.kind == AssertionKind.ALLERGY_DENIAL and self.critical.allergy_denial == "none":
            raise ValueError("allergy denial scope must be adjudicated")
        if (
            self.kind == AssertionKind.EXPLICIT_NEW_ORDER
            and self.critical.order_authorization != "explicit"
        ):
            raise ValueError("gold order requires explicit authorization")
        if self.critical.allergy_denial != "none" and (
            self.kind != AssertionKind.ALLERGY_DENIAL or self.polarity != Polarity.NEGATED
        ):
            raise ValueError("denial attributes require a negated allergy denial")
        if self.critical.order_authorization == "explicit" and (
            self.kind != AssertionKind.EXPLICIT_NEW_ORDER
            or self.medication_intent != MedicationIntent.NEW_ORDER
            or self.polarity != Polarity.AFFIRMED
            or self.source_speaker_role != SpeakerRole.CLINICIAN
        ):
            raise ValueError("explicit gold order requires affirmed clinician new-order fields")
        condition_kinds = {
            AssertionKind.CLINICIAN_ASSESSED_PROBLEM,
            AssertionKind.CLINICIAN_RULED_OUT_PROBLEM,
            AssertionKind.PATIENT_REPORTED_CONDITION,
        }
        if self.kind in condition_kinds and self.critical.condition_assertion == "none":
            raise ValueError("condition status must be adjudicated")
        if self.critical.condition_assertion == "absent" and self.polarity != Polarity.NEGATED:
            raise ValueError("absent gold condition requires negated polarity")
        return self


class Annotation(Record):
    annotator_ids: tuple[Identifier, ...] = Field(min_length=1)
    adjudicator_id: Identifier
    status: Literal["adjudicated", "harness-authored"]
    independent: bool
    guidelines_version: str = Field(min_length=1)
    # No agreement values are invented when no independent annotation occurred.
    agreement_by_kind: dict[str, dict[str, float]] | None = None


class OutOfScopeFact(Record):
    id: Identifier
    category: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence: tuple[Span, ...] = Field(min_length=1)


class Encounter(Record):
    id: Identifier
    transcript_path: str
    transcript_sha256: Sha256
    intake_path: str
    intake_sha256: Sha256
    split: Split
    source: str = Field(min_length=1)
    license: str = Field(min_length=1)
    fictional: bool
    annotation: Annotation
    assertions: tuple[GoldAssertion, ...]
    out_of_scope: tuple[OutOfScopeFact, ...]
    critical_error_targets: tuple[str, ...] = ()

    @model_validator(mode="after")
    def unique(self) -> Self:
        ids = [a.id for a in self.assertions] + [a.id for a in self.out_of_scope]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate gold fact id")
        if self.split == "harness-check" and not self.fictional:
            raise ValueError("harness-check encounters must be fictional")
        return self


class Dataset(Record):
    schema_version: Literal["1"] = "1"
    id: Identifier
    split_id: Identifier
    split: Split
    declared_kinds: tuple[AssertionKind, ...] = Field(min_length=1)
    clinical_watchlist: tuple[str, ...] = Field(min_length=1)
    encounters: tuple[Encounter, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if len({e.id for e in self.encounters}) != len(self.encounters):
            raise ValueError("duplicate encounter id")
        if len({e.transcript_sha256 for e in self.encounters}) != len(self.encounters):
            raise ValueError("duplicate transcript content")
        if len(set(self.declared_kinds)) != len(self.declared_kinds):
            raise ValueError("duplicate declared kind")
        if any(e.split != self.split for e in self.encounters):
            raise ValueError("one manifest must contain one split")
        return self


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def relative_file(root: Path, path: str) -> Path:
    target = (root / path).resolve()
    if Path(path).is_absolute() or not target.is_relative_to(root.resolve()):
        raise ValueError("dataset paths must remain within the manifest directory")
    return target


def load_dataset(path: Path) -> tuple[Dataset, dict[str, Transcript]]:
    dataset = Dataset.model_validate_json(path.read_text("utf-8"))
    transcripts = {}
    for encounter in dataset.encounters:
        transcript = Transcript.load(relative_file(path.parent, encounter.transcript_path))
        if transcript.content_hash != encounter.transcript_sha256:
            raise ValueError("transcript content hash mismatch")
        if (
            digest(relative_file(path.parent, encounter.intake_path).read_bytes())
            != encounter.intake_sha256
        ):
            raise ValueError("intake hash mismatch")
        for fact in (*encounter.assertions, *encounter.out_of_scope):
            for span in fact.evidence:
                span.verify(transcript)
        transcripts[encounter.id] = transcript
    return dataset, transcripts


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
