"""Reduced assertion schema: the fixed fact form (PLAN section 5 step 5, A3, A5).

One pydantic model per assertion kind in the A3 export matrix. The
extraction model fills in only these fields and must quote the
transcript for every assertion. There are deliberately **no code
fields anywhere**: the LLM never emits codes (PLAN section 5 step 6);
coding is a later, deterministic step.

Every clinical field is nullable (PLAN A5): anything unsaid stays
empty (``null``) and is flagged downstream rather than invented.
In schema v1 fields are *required but nullable* — the key must be present, its
value may be ``null`` — so constrained decoding always emits the
complete form. Blank clinical text is normalised to ``null``.
Classification fields are also nullable; their enums carry explicit
abstention values (``unknown`` / ``unclear``) and ``null`` means the
same abstention. The only non-nullable parts are the discriminator
``kind`` and ``evidence`` (at least one quote, always).

Schema v2 permits kind-specific detail keys to be omitted. Its plain
``properties``/``required`` form retains nullable detail values and all required
classifications, including medication intent. The parser fills only absent
detail keys with null before validating through the unchanged v1 models.
Concept text stays required: a missing name must remain explicit abstention.

``extraction_json_schema()`` exports the JSON Schema used for
constrained decoding, reduced to the lowest-common subset of
llama.cpp GBNF and XGrammar: no ``$ref`` (all definitions inlined),
no ``pattern``, every array bounded with ``maxItems``, enums for
every closed set, and ``anyOf`` used only in the nullable form
``{"anyOf": [<schema>, {"type": "null"}]}``. ``check_schema_subset``
verifies those properties and is exercised by the tests.

The nine disjoint object variants use ``oneOf`` with a singleton
``kind`` enum on each branch. This checks structure, not clinical
consistency or export eligibility; A3 mapping and consistency checks
must still refuse contradictory or incomplete classifications.
The limits (64 assertions per response, 8 evidence spans per assertion)
are decoding bounds, not permission to silently truncate extraction.
"""

from __future__ import annotations

import copy
import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from auris.extract.profiles import PropertyOrder, SchemaVersion
from auris.transcript import SpeakerRole

MAX_EVIDENCE_ITEMS = 8
MAX_ASSERTIONS = 64
OPTIONAL_DETAIL_FIELDS = frozenset(
    {
        "value_text",
        "unit_text",
        "stated_time_text",
        "dose_text",
        "route_text",
        "frequency_text",
        "allergen_text",
        "reaction_manifestation_text",
        "stated_resolution_text",
    }
)


class AssertionKind(StrEnum):
    """The nine assertion kinds: the rows of the A3 export matrix."""

    VITAL_SIGN = "vital_sign"
    SYMPTOM = "symptom"
    CLINICIAN_ASSESSED_PROBLEM = "clinician_assessed_problem"
    CLINICIAN_RULED_OUT_PROBLEM = "clinician_ruled_out_problem"
    PATIENT_REPORTED_CONDITION = "patient_reported_condition"
    EXPLICIT_NEW_ORDER = "explicit_new_order"
    REPORTED_MEDICATION = "reported_medication"
    ALLERGY_STATEMENT = "allergy_statement"
    ALLERGY_DENIAL = "allergy_denial"


class Polarity(StrEnum):
    AFFIRMED = "affirmed"
    NEGATED = "negated"


class Experiencer(StrEnum):
    PATIENT = "patient"
    FAMILY_MEMBER = "family_member"
    OTHER = "other"
    UNKNOWN = "unknown"


class Temporality(StrEnum):
    CURRENT = "current"
    PAST = "past"
    RESOLVED = "resolved"
    FUTURE = "future"
    UNCLEAR = "unclear"


class MedicationIntent(StrEnum):
    NEW_ORDER = "new_order"
    CONTINUING = "continuing"
    HISTORICAL = "historical"
    DISCUSSED = "discussed"
    UNCLEAR = "unclear"


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


# A clinical text field: verbatim words from the transcript, or null
# when unsaid. Blank strings count as unsaid.
VerbatimText = Annotated[str | None, BeforeValidator(_blank_to_none)]


class _AssertionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceItem(_AssertionModel):
    """One supporting quote: a segment id plus the quoted words (A5)."""

    segment_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_nonblank(self) -> EvidenceItem:
        if not self.segment_id.strip():
            raise ValueError("evidence segment_id must not be blank")
        if not self.quote.strip():
            raise ValueError("evidence quote must not be blank")
        return self


class AssertionBase(_AssertionModel):
    """Fields common to every assertion kind.

    ``kind`` is narrowed to a single-value ``Literal`` by each
    subclass, which is what the discriminated union keys on.
    """

    kind: AssertionKind
    concept_text: VerbatimText
    polarity: Polarity | None
    experiencer: Experiencer | None
    temporality: Temporality | None
    source_speaker_role: SpeakerRole | None
    evidence: tuple[EvidenceItem, ...] = Field(min_length=1, max_length=MAX_EVIDENCE_ITEMS)


class VitalSignAssertion(AssertionBase):
    """A stated vital-sign measurement. Value and unit stay verbatim
    (e.g. "128 over 82") so no arithmetic or unit conversion happens
    at extraction time; a later deterministic step parses them."""

    kind: Literal["vital_sign"]
    value_text: VerbatimText
    unit_text: VerbatimText
    stated_time_text: VerbatimText


class SymptomAssertion(AssertionBase):
    """A stated symptom. No FHIR resource in v1 (A3); ledger only."""

    kind: Literal["symptom"]


class ClinicianAssessedProblemAssertion(AssertionBase):
    kind: Literal["clinician_assessed_problem"]


class ClinicianRuledOutProblemAssertion(AssertionBase):
    kind: Literal["clinician_ruled_out_problem"]


class PatientReportedConditionAssertion(AssertionBase):
    kind: Literal["patient_reported_condition"]


class _MedicationAssertionBase(AssertionBase):
    """Medication fields, all verbatim: Auris never invents a dose,
    route or frequency that was not said (PLAN section 8)."""

    medication_intent: MedicationIntent | None
    dose_text: VerbatimText
    route_text: VerbatimText
    frequency_text: VerbatimText


class ExplicitNewOrderAssertion(_MedicationAssertionBase):
    """An explicit new spoken order. Mapped later to a *draft*
    MedicationRequest only; never activated or signed (A3)."""

    kind: Literal["explicit_new_order"]


class ReportedMedicationAssertion(_MedicationAssertionBase):
    """A reported, continuing, historical or merely discussed
    medication. Never exported as an order in v1 (A3)."""

    kind: Literal["reported_medication"]


class AllergyStatementAssertion(AssertionBase):
    kind: Literal["allergy_statement"]
    allergen_text: VerbatimText
    reaction_manifestation_text: VerbatimText
    stated_resolution_text: VerbatimText


class AllergyDenialAssertion(AssertionBase):
    """An allergy denial. ``allergen_text`` is null for a general
    "no known allergies" statement and names the substance for a
    specific denial ("not allergic to penicillin")."""

    kind: Literal["allergy_denial"]
    allergen_text: VerbatimText


Assertion = Annotated[
    VitalSignAssertion
    | SymptomAssertion
    | ClinicianAssessedProblemAssertion
    | ClinicianRuledOutProblemAssertion
    | PatientReportedConditionAssertion
    | ExplicitNewOrderAssertion
    | ReportedMedicationAssertion
    | AllergyStatementAssertion
    | AllergyDenialAssertion,
    Field(discriminator="kind"),
]

ASSERTION_MODELS: tuple[type[AssertionBase], ...] = (
    VitalSignAssertion,
    SymptomAssertion,
    ClinicianAssessedProblemAssertion,
    ClinicianRuledOutProblemAssertion,
    PatientReportedConditionAssertion,
    ExplicitNewOrderAssertion,
    ReportedMedicationAssertion,
    AllergyStatementAssertion,
    AllergyDenialAssertion,
)


class ExtractionResult(_AssertionModel):
    """The complete extractor output for one transcript (or chunk)."""

    assertions: tuple[Assertion, ...] = Field(max_length=MAX_ASSERTIONS)


def _check_version(version: SchemaVersion) -> None:
    if version not in ("1", "2"):
        raise ValueError("unsupported extraction schema version")


def _with_optional_nulls(data: Any) -> Any:
    """Normalize only v2 details belonging to this kind; never fill classifications."""
    if not isinstance(data, dict):
        return data
    for model in ASSERTION_MODELS:
        if get_args(model.model_fields["kind"].annotation) == (data.get("kind"),):
            return {
                **dict.fromkeys(OPTIONAL_DETAIL_FIELDS & model.model_fields.keys()),
                **data,
            }
    return data


def parse_extraction_json(text: str, *, schema_version: SchemaVersion = "1") -> ExtractionResult:
    """Parse either wire contract into the unchanged canonical v1 models."""
    _check_version(schema_version)
    if schema_version == "2":
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get("assertions"), list):
            data["assertions"] = [_with_optional_nulls(a) for a in data["assertions"]]
        text = json.dumps(data, allow_nan=False)
    return ExtractionResult.model_validate_json(text, strict=True)


def parse_assertion(data: dict[str, Any], *, schema_version: SchemaVersion = "1") -> AssertionBase:
    """Parse one assertion dict through the discriminated union."""
    _check_version(schema_version)
    wrapper = ExtractionResult.model_validate(
        {"assertions": [_with_optional_nulls(data) if schema_version == "2" else data]}
    )
    return wrapper.assertions[0]


def _inline_refs(node: Any, defs: dict[str, Any], active: frozenset[str]) -> Any:
    """Expand every ``$ref`` to ``$defs`` in place, refusing recursion."""
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            prefix = "#/$defs/"
            if not isinstance(ref, str) or not ref.startswith(prefix):
                raise ValueError(f"unsupported $ref in generated schema: {ref!r}")
            name = ref[len(prefix) :]
            if name in active:
                raise ValueError(f"recursive $ref in generated schema: {ref!r}")
            if name not in defs:
                raise ValueError(f"unresolved $ref in generated schema: {ref!r}")
            expanded = _inline_refs(copy.deepcopy(defs[name]), defs, active | {name})
            siblings = {
                key: _inline_refs(value, defs, active)
                for key, value in node.items()
                if key not in ("$ref", "discriminator")
            }
            return {**expanded, **siblings}
        expanded = {
            key: _inline_refs(value, defs, active)
            for key, value in node.items()
            if key != "discriminator"
        }
        if "const" in expanded:
            expanded["enum"] = [expanded.pop("const")]
        return expanded
    if isinstance(node, list):
        return [_inline_refs(item, defs, active) for item in node]
    return node


def extraction_json_schema(
    schema_version: SchemaVersion = "1", *, property_order: PropertyOrder = "canonical"
) -> dict[str, Any]:
    """JSON Schema for constrained decoding of :class:`ExtractionResult`.

    Reduced to the lowest-common subset described in the module
    docstring. Raises ValueError if the generated schema cannot be
    reduced (e.g. a recursive model is introduced later).
    """
    _check_version(schema_version)
    if property_order not in ("canonical", "evidence-last"):
        raise ValueError("unsupported extraction property order")
    raw = ExtractionResult.model_json_schema()
    defs = raw.pop("$defs", {})
    schema = _inline_refs(raw, defs, frozenset())
    if schema_version == "2":
        for variant in schema["properties"]["assertions"]["items"]["oneOf"]:
            variant["required"] = [
                name for name in variant["required"] if name not in OPTIONAL_DETAIL_FIELDS
            ]
    if property_order == "evidence-last":
        for variant in schema["properties"]["assertions"]["items"]["oneOf"]:
            properties = variant["properties"]
            variant["properties"] = {
                **{name: value for name, value in properties.items() if name != "evidence"},
                "evidence": properties["evidence"],
            }
    violations = check_schema_subset(schema)
    if violations:
        raise ValueError(
            "generated schema is outside the constrained-decoding subset: " + "; ".join(violations)
        )
    return schema


def write_extraction_json_schema(
    path: str | Path,
    *,
    schema_version: SchemaVersion = "1",
    property_order: PropertyOrder = "canonical",
) -> None:
    schema = extraction_json_schema(schema_version, property_order=property_order)
    text = json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
    Path(path).write_text(text, encoding="utf-8")


def _is_nullable_any_of(branches: Any) -> bool:
    """The one allowed ``anyOf`` form: ``[<schema>, {"type": "null"}]``."""
    if not isinstance(branches, list) or len(branches) != 2:
        return False
    return (
        isinstance(branches[0], dict)
        and bool(branches[0])
        and branches[0].get("type") != "null"
        and branches[1] == {"type": "null"}
    )


def check_schema_subset(schema: Any) -> list[str]:
    """Return subset violations in ``schema`` (empty list means clean).

    Walks the whole schema and reports: any ``$ref`` or leftover
    ``$defs``; any ``pattern``; any array without ``maxItems``; any
    ``anyOf`` that is not the nullable form documented above.
    """
    violations: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{path}/{key}"
                if key == "$ref":
                    violations.append(f"{child}: $ref is not in the subset")
                elif key == "$defs":
                    violations.append(f"{child}: $defs must be inlined away")
                elif key == "pattern":
                    violations.append(f"{child}: pattern is not in the subset")
                elif key == "anyOf" and not _is_nullable_any_of(value):
                    violations.append(f"{child}: anyOf is not the nullable form")
                walk(value, child)
            if node.get("type") == "array":
                bound = node.get("maxItems")
                if type(bound) is not int or bound < 0:
                    violations.append(f"{path}: array has no non-negative integer maxItems bound")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}/{index}")

    walk(schema, "$")
    return violations
