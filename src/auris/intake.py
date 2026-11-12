"""Intake contract models (PLAN Appendix A1).

Intake context is supplied by the operator, never extracted or
invented: Patient, Practitioner and Encounter anchors at their
US Core 6.1.0 minimums (docs/fhir-reference.md, "Intake anchors").
A run without them refuses to export, so every minimum below is a
required field whose absence or blankness fails validation with a
message naming the missing element.

These are intake *forms*, not full FHIR resources: they carry exactly
the A1 minimums plus optional client-assigned resource ids, so a
later step can build the anchor resources and references from them.
Auris never modifies the intake anchors.

Binding choices, from fhir-reference.md: enums are used only where
the binding is required (Patient.gender; Encounter.status takes the
R4 EncounterStatus required value set, which fhir-reference.md does
not enumerate; checked against the cached R4 4.0.1 CodeSystem).
Encounter.class and Encounter.type have extensible bindings: this
form requires a class code, while type accepts a code and/or text.
The subject form is a local ``Patient/<id>`` reference, not a general
FHIR Reference parser. Full FHIR validation belongs to later lanes.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from auris._storage import write_private_text

Gender = Literal["male", "female", "other", "unknown"]

EncounterStatus = Literal[
    "planned",
    "arrived",
    "triaged",
    "in-progress",
    "onleave",
    "finished",
    "cancelled",
    "entered-in-error",
    "unknown",
]


def _require_nonblank(value: str, field: str) -> str:
    if not value.strip():
        raise ValueError(f"{field} must not be blank")
    return value


NonblankText = Annotated[str, AfterValidator(lambda value: _require_nonblank(value, "text"))]

# dateTime.value regex, hl7.fhir.r4.core#4.0.1 (CC0-1.0). Preserve the
# supplied precision and timezone: a date must never acquire midnight.
_DATETIME = re.compile(
    r"([0-9]([0-9]([0-9][1-9]|[1-9]0)|[1-9]00)|[1-9]000)"
    r"(-(0[1-9]|1[0-2])(-(0[1-9]|[1-2][0-9]|3[0-1])"
    r"(T([01][0-9]|2[0-3]):[0-5][0-9]:([0-5][0-9]|60)"
    r"(\.[0-9]+)?(Z|(\+|-)((0[0-9]|1[0-3]):[0-5][0-9]|14:00)))?)?)?"
)


def _check_datetime(value: str) -> str:
    if not _DATETIME.fullmatch(value):
        raise ValueError(
            "expected an R4 dateTime: partial date or timestamp with seconds and timezone"
        )
    if len(value) >= 10:
        date.fromisoformat(value[:10])  # Also reject impossible calendar dates.
    return value


IntakeDateTime = Annotated[str, AfterValidator(_check_datetime)]


class _IntakeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Identifier(_IntakeModel):
    """An identifier with both parts present (US Core: system+value 1..1)."""

    system: str = Field(min_length=1)
    value: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_nonblank(self) -> Identifier:
        _require_nonblank(self.system, "identifier.system")
        _require_nonblank(self.value, "identifier.value")
        return self


class PatientName(_IntakeModel):
    """Patient name: family and/or given (US Core us-core-6).

    The profile also allows a data-absent-reason extension instead of
    a name; the intake form does not accept that, because intake is
    operator-supplied context and A1 requires a name.
    """

    family: NonblankText | None = None
    given: tuple[NonblankText, ...] | None = None

    @model_validator(mode="after")
    def _check_family_or_given(self) -> PatientName:
        has_family = self.family is not None and bool(self.family.strip())
        has_given = self.given is not None and any(part.strip() for part in self.given)
        if self.given is not None and len(self.given) == 0:
            raise ValueError("patient name.given must be omitted (null) rather than empty")
        if not has_family and not has_given:
            raise ValueError(
                "patient name must have family and/or given (US Core us-core-6, PLAN A1)"
            )
        return self


class PractitionerName(_IntakeModel):
    """Practitioner name: family is required (US Core name.family 1..1)."""

    family: str = Field(min_length=1)
    given: tuple[NonblankText, ...] | None = None

    @model_validator(mode="after")
    def _check_family(self) -> PractitionerName:
        _require_nonblank(self.family, "practitioner name.family")
        if self.given is not None and len(self.given) == 0:
            raise ValueError("practitioner name.given must be omitted (null) rather than empty")
        return self


class Coding(_IntakeModel):
    """A code with its system. ``code`` is required: a display-only
    Coding cannot populate Encounter.class in a later step."""

    system: NonblankText | None = None
    code: str = Field(min_length=1)
    display: NonblankText | None = None

    @model_validator(mode="after")
    def _check_code(self) -> Coding:
        _require_nonblank(self.code, "coding.code")
        return self


class CodeableConcept(_IntakeModel):
    """Codings and/or free text; an entirely empty concept is refused."""

    coding: tuple[Coding, ...] = ()
    text: NonblankText | None = None

    @model_validator(mode="after")
    def _check_not_empty(self) -> CodeableConcept:
        has_text = self.text is not None and bool(self.text.strip())
        if not self.coding and not has_text:
            raise ValueError("codeable concept must have at least one coding or text")
        return self


class Reference(_IntakeModel):
    reference: str = Field(min_length=1)
    display: NonblankText | None = None

    @model_validator(mode="after")
    def _check_reference(self) -> Reference:
        _require_nonblank(self.reference, "reference.reference")
        return self


class Period(_IntakeModel):
    """Encounter period; Auris requires ``start`` (PLAN A1).

    Keep the original R4 dateTime strings. Compare endpoints when both
    are full timestamps or have the same date precision. Mixed precision
    and leap-second ordering are left to the later R4 validator.
    """

    start: IntakeDateTime
    end: IntakeDateTime | None = None

    @model_validator(mode="after")
    def _check_order(self) -> Period:
        if self.end is None:
            return self
        reversed_period = False
        if len(self.start) == len(self.end) and len(self.start) <= 10:
            reversed_period = self.end < self.start
        elif "T" in self.start and "T" in self.end:
            try:
                reversed_period = datetime.fromisoformat(self.end) < datetime.fromisoformat(
                    self.start
                )
            except ValueError:
                pass  # R4 allows leap seconds; datetime does not.
        if reversed_period:
            raise ValueError("period.end must be >= period.start")
        return self


class PatientIntake(_IntakeModel):
    """US Core Patient minimums (PLAN A1)."""

    id: str | None = None
    identifier: tuple[Identifier, ...] = Field(min_length=1)
    name: tuple[PatientName, ...] = Field(min_length=1)
    gender: Gender

    @model_validator(mode="after")
    def _check_id(self) -> PatientIntake:
        if self.id is not None:
            _require_nonblank(self.id, "patient.id")
        return self


class PractitionerIntake(_IntakeModel):
    """US Core Practitioner minimums (PLAN A1)."""

    id: str | None = None
    identifier: tuple[Identifier, ...] = Field(min_length=1)
    name: tuple[PractitionerName, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_id(self) -> PractitionerIntake:
        if self.id is not None:
            _require_nonblank(self.id, "practitioner.id")
        return self


class EncounterIntake(_IntakeModel):
    """US Core Encounter minimums plus Auris-required period.start (A1)."""

    id: str | None = None
    status: EncounterStatus
    class_: Coding = Field(alias="class")
    type: tuple[CodeableConcept, ...] = Field(min_length=1)
    subject: Reference
    period: Period

    @model_validator(mode="after")
    def _check_subject_and_id(self) -> EncounterIntake:
        if self.id is not None:
            _require_nonblank(self.id, "encounter.id")
        if (
            not self.subject.reference.startswith("Patient/")
            or not self.subject.reference[8:].strip()
        ):
            raise ValueError("encounter.subject must reference the intake Patient (Patient/<id>)")
        return self


class Intake(_IntakeModel):
    """The complete intake contract for one run (PLAN A1).

    All three anchors are required. When the Patient carries a
    client-assigned id, Encounter.subject must point at exactly that
    Patient, so the anchors cannot silently disagree.
    """

    patient: PatientIntake
    practitioner: PractitionerIntake
    encounter: EncounterIntake

    @model_validator(mode="after")
    def _check_subject_matches_patient(self) -> Intake:
        if self.patient.id is not None:
            expected = f"Patient/{self.patient.id}"
            if self.encounter.subject.reference != expected:
                raise ValueError(
                    f"encounter.subject {self.encounter.subject.reference!r} does not "
                    f"reference the intake patient {expected!r}"
                )
        return self

    def to_json(self) -> str:
        payload = self.model_dump(mode="json", by_alias=True, exclude_unset=True)
        return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Intake:
        return cls.model_validate_json(text)

    def save(self, path: str | Path) -> None:
        """Save at mode 0600 in a new or existing 0700 workdir (A8)."""
        write_private_text(path, self.to_json())


def load_intake(path: str | Path) -> Intake:
    """Load and validate an intake JSON file (PLAN A1)."""
    return Intake.from_json(Path(path).read_text(encoding="utf-8"))
