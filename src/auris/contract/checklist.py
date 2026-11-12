"""Profile checklist generator (PLAN Appendix A2, Slice 1 task zero).

Reads the StructureDefinition snapshots from the cached US Core 6.1.0
package and produces, per claimed profile, every element that is
required (min >= 1), fixed/pattern-valued, Must Support, slice-defining
or sliced, bound with required/extensible strength, or carrying an
invariant. The US Core and FHIR R4 core packages are CC0-1.0 (their
package.json `license` fields, verified in Step 1), so the generated
derivatives may be committed.

Two kinds of snapshot boilerplate are omitted so the checklist stays a
contract, not a dump: the generic `ele-1`/`ext-1` invariants that FHIR
attaches to every element, and the `url`-discriminated slicing
definitions on `extension`/`modifierExtension` elements (named
extension slices such as `extension:assertedDate` are kept).
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from auris.config import FhirPackagePin, FrozenConfig
from auris.setup import fhir_packages

CLAIMED_PROFILES: tuple[str, ...] = (
    "us-core-vital-signs",
    "us-core-blood-pressure",
    "us-core-heart-rate",
    "us-core-respiratory-rate",
    "us-core-body-temperature",
    "us-core-body-weight",
    "us-core-body-height",
    "us-core-pulse-oximetry",
    "us-core-condition-encounter-diagnosis",
    "us-core-condition-problems-health-concerns",
    "us-core-medicationrequest",
    "us-core-allergyintolerance",
    "us-core-patient",
    "us-core-practitioner",
    "us-core-encounter",
)

ELEMENT_SOURCES: frozenset[str] = frozenset(
    {"extracted", "intake", "fixed", "mapper-policy", "not-emitted", "unresolved"}
)


class ChecklistError(Exception):
    """The checklist cannot be generated from the cached packages."""


class _ChecklistModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class TypeInfo(_ChecklistModel):
    code: str
    profile: list[str] = Field(default_factory=list)
    target_profile: list[str] = Field(default_factory=list)


class BindingInfo(_ChecklistModel):
    strength: str
    value_set: str | None = None


class InvariantInfo(_ChecklistModel):
    key: str
    severity: str
    human: str
    expression: str | None = None


class DiscriminatorInfo(_ChecklistModel):
    type: str
    path: str


class SlicingInfo(_ChecklistModel):
    discriminator: list[DiscriminatorInfo] = Field(default_factory=list)
    rules: str | None = None
    ordered: bool | None = None


class FixedValue(_ChecklistModel):
    kind: str  # the ElementDefinition key, e.g. "fixedCode"
    value: Any


class ElementChecklist(_ChecklistModel):
    id: str
    path: str
    slice_name: str | None = None
    min: int
    max: str
    types: list[TypeInfo] = Field(default_factory=list)
    fixed: FixedValue | None = None
    pattern: FixedValue | None = None
    binding: BindingInfo | None = None
    invariants: list[InvariantInfo] = Field(default_factory=list)
    must_support: bool = False
    slicing: SlicingInfo | None = None
    short: str | None = None

    @property
    def cardinality(self) -> str:
        return f"{self.min}..{self.max}"

    @property
    def is_required(self) -> bool:
        return self.min >= 1

    @property
    def has_fixed_or_pattern(self) -> bool:
        return self.fixed is not None or self.pattern is not None

    @property
    def needs_source(self) -> bool:
        """Required/fixed elements must have an entry in element_sources."""
        return self.is_required or self.has_fixed_or_pattern


class ProfileChecklist(_ChecklistModel):
    id: str
    name: str | None = None
    title: str | None = None
    url: str
    version: str | None = None
    fhir_version: str | None = None
    type: str
    base_definition: str | None = None
    elements: list[ElementChecklist] = Field(default_factory=list)

    def element(self, element_id: str) -> ElementChecklist | None:
        for element in self.elements:
            if element.id == element_id:
                return element
        return None

    def invariants(self) -> list[tuple[str, InvariantInfo]]:
        rows: list[tuple[str, InvariantInfo]] = []
        for element in self.elements:
            for invariant in element.invariants:
                rows.append((element.id, invariant))
        return rows

    def must_support_elements(self) -> list[ElementChecklist]:
        return [element for element in self.elements if element.must_support]


class ChecklistDocument(_ChecklistModel):
    fhir_version: str
    source_package_id: str
    source_package_version: str
    source_package_sha256: str
    source_package_license: str
    claimed_profiles: list[str]
    profiles: list[ProfileChecklist]


def _parse_types(raw: Any) -> list[TypeInfo]:
    types: list[TypeInfo] = []
    for entry in raw or []:
        types.append(
            TypeInfo(
                code=str(entry.get("code", "")),
                profile=[str(p) for p in entry.get("profile", [])],
                target_profile=[str(p) for p in entry.get("targetProfile", [])],
            )
        )
    return types


def _parse_fixed(element: dict[str, Any], prefix: str) -> FixedValue | None:
    for key in sorted(element):
        if key.startswith(prefix) and len(key) > len(prefix):
            return FixedValue(kind=key, value=element[key])
    return None


def _parse_binding(element: dict[str, Any]) -> BindingInfo | None:
    binding = element.get("binding")
    if not isinstance(binding, dict) or "strength" not in binding:
        return None
    return BindingInfo(
        strength=str(binding["strength"]),
        value_set=binding.get("valueSet"),
    )


GENERIC_INVARIANT_KEYS: frozenset[str] = frozenset({"ele-1", "ext-1"})


def _parse_invariants(element: dict[str, Any]) -> list[InvariantInfo]:
    invariants: list[InvariantInfo] = []
    for constraint in element.get("constraint", []) or []:
        if str(constraint.get("key", "")) in GENERIC_INVARIANT_KEYS:
            continue
        invariants.append(
            InvariantInfo(
                key=str(constraint.get("key", "")),
                severity=str(constraint.get("severity", "")),
                human=str(constraint.get("human", "")),
                expression=constraint.get("expression"),
            )
        )
    return invariants


def _parse_slicing(element: dict[str, Any]) -> SlicingInfo | None:
    slicing = element.get("slicing")
    if not isinstance(slicing, dict):
        return None
    return SlicingInfo(
        discriminator=[
            DiscriminatorInfo(type=str(d.get("type", "")), path=str(d.get("path", "")))
            for d in slicing.get("discriminator", []) or []
        ],
        rules=slicing.get("rules"),
        ordered=slicing.get("ordered"),
    )


def _is_extension_slicing(element: ElementChecklist) -> bool:
    leaf = element.path.rsplit(".", 1)[-1]
    return leaf in {"extension", "modifierExtension"} and element.slicing is not None


def _should_include(element: ElementChecklist) -> bool:
    if element.is_required or element.has_fixed_or_pattern or element.must_support:
        return True
    if element.invariants or element.slice_name:
        return True
    if element.slicing is not None and not _is_extension_slicing(element):
        return True
    return element.binding is not None and element.binding.strength in (
        "required",
        "extensible",
    )


def parse_element(element: dict[str, Any]) -> ElementChecklist:
    """Parse one snapshot ElementDefinition into the checklist shape."""
    return ElementChecklist(
        id=str(element.get("id", element.get("path", ""))),
        path=str(element.get("path", "")),
        slice_name=element.get("sliceName"),
        min=int(element.get("min") or 0),
        max=str(element.get("max", "")),
        types=_parse_types(element.get("type")),
        fixed=_parse_fixed(element, "fixed"),
        pattern=_parse_fixed(element, "pattern"),
        binding=_parse_binding(element),
        invariants=_parse_invariants(element),
        must_support=bool(element.get("mustSupport", False)),
        slicing=_parse_slicing(element),
        short=element.get("short"),
    )


def parse_structure_definition(structure_definition: dict[str, Any]) -> ProfileChecklist:
    """Build a profile checklist from a StructureDefinition snapshot."""
    snapshot = structure_definition.get("snapshot") or {}
    raw_elements = snapshot.get("element")
    if not raw_elements:
        raise ChecklistError(
            f"StructureDefinition {structure_definition.get('id')!r} has no snapshot elements"
        )
    elements = [
        parsed for parsed in (parse_element(raw) for raw in raw_elements) if _should_include(parsed)
    ]
    return ProfileChecklist(
        id=str(structure_definition.get("id", "")),
        name=structure_definition.get("name"),
        title=structure_definition.get("title"),
        url=str(structure_definition.get("url", "")),
        version=structure_definition.get("version"),
        fhir_version=structure_definition.get("fhirVersion"),
        type=str(structure_definition.get("type", "")),
        base_definition=structure_definition.get("baseDefinition"),
        elements=elements,
    )


def load_profile_checklists(
    config: FrozenConfig,
    cache_dir: Path,
    profile_ids: tuple[str, ...] = CLAIMED_PROFILES,
) -> ChecklistDocument:
    """Load the claimed profiles from the cached US Core package tarball."""
    pin = config.fhir.package(config.fhir.us_core_package_id)
    if pin is None:  # pragma: no cover - config validation guarantees this
        raise ChecklistError("US Core package is not pinned")
    tarball = fhir_packages.package_path(cache_dir, pin)
    if not tarball.is_file():
        raise ChecklistError(f"{tarball} not found; run `auris setup fhir` first")
    actual = fhir_packages.sha256_file(tarball)
    if actual != pin.sha256:
        raise fhir_packages.ChecksumMismatchError(
            f"cached {tarball} has SHA-256 {actual}, expected {pin.sha256}; refusing to use it"
        )
    profiles: list[ProfileChecklist] = []
    for profile_id in profile_ids:
        member = f"package/StructureDefinition-{profile_id}.json"
        raw = fhir_packages.read_package_member(tarball, member)
        profiles.append(parse_structure_definition(json.loads(raw)))
    return ChecklistDocument(
        fhir_version=config.fhir.version,
        source_package_id=pin.id,
        source_package_version=pin.version,
        source_package_sha256=pin.sha256,
        source_package_license=pin.license,
        claimed_profiles=list(profile_ids),
        profiles=profiles,
    )


# ---------------------------------------------------------------------------
# Element sources (hand-maintained element_sources.toml)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ElementSource:
    source: str
    note: str = ""


@dataclass(frozen=True)
class OpenQuestion:
    profile: str
    element: str
    reason: str


@dataclass(frozen=True)
class ElementSources:
    by_profile: dict[str, dict[str, ElementSource]] = field(default_factory=dict)
    open_questions: list[OpenQuestion] = field(default_factory=list)

    def source_for(self, profile_id: str, element_id: str) -> ElementSource | None:
        return self.by_profile.get(profile_id, {}).get(element_id)


def load_element_sources(path: Path) -> ElementSources:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    by_profile: dict[str, dict[str, ElementSource]] = {}
    for profile_id, profile_table in (raw.get("profiles") or {}).items():
        entries: dict[str, ElementSource] = {}
        for element_id, entry in (profile_table.get("elements") or {}).items():
            source = str(entry.get("source", ""))
            if source not in ELEMENT_SOURCES:
                raise ChecklistError(
                    f"{path}: {profile_id} {element_id} has unknown source {source!r}"
                )
            entries[element_id] = ElementSource(source=source, note=str(entry.get("note", "")))
        by_profile[profile_id] = entries
    open_questions = [
        OpenQuestion(
            profile=str(row.get("profile", "")),
            element=str(row.get("element", "")),
            reason=str(row.get("reason", "")),
        )
        for row in raw.get("open_questions") or []
    ]
    return ElementSources(by_profile=by_profile, open_questions=open_questions)


def coverage_problems(document: ChecklistDocument, sources: ElementSources) -> list[str]:
    """Every required/fixed element needs a source; `unresolved` needs a reason."""
    problems: list[str] = []
    open_question_keys = {(q.profile, q.element) for q in sources.open_questions}
    for question in sources.open_questions:
        if not question.reason.strip():
            problems.append(f"open question {question.profile} {question.element} has no reason")
    unresolved: set[tuple[str, str]] = set()
    for profile in document.profiles:
        known_ids = {element.id for element in profile.elements}
        for element_id in sources.by_profile.get(profile.id, {}):
            if element_id not in known_ids:
                problems.append(f"{profile.id}: source entry for unknown element {element_id}")
        for element in profile.elements:
            entry = sources.source_for(profile.id, element.id)
            if element.needs_source and entry is None:
                problems.append(f"{profile.id}: {element.id} has no element source")
            if entry is not None and entry.source == "unresolved":
                unresolved.add((profile.id, element.id))
                if (profile.id, element.id) not in open_question_keys:
                    problems.append(
                        f"{profile.id}: {element.id} is unresolved but not listed"
                        " in [open_questions] with a reason"
                    )
    for key in sorted(open_question_keys - unresolved):
        problems.append(f"open question {key[0]} {key[1]} has no unresolved entry")
    return problems


# ---------------------------------------------------------------------------
# Discrepancy checks against docs/fhir-reference.md and PLAN Appendix A3
#
# Each expectation below states a fact exactly as docs/fhir-reference.md
# states it. A generated fact that disagrees is reported; nothing here
# edits the plan or the reference — amending them is the reviewer's call
# (PLAN A2: "The matrix below is amended if the checklist disagrees").
# ---------------------------------------------------------------------------


def _codes_in(value: Any) -> set[str]:
    """Collect every string under a `code` key in a fixed/pattern value."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "code" and isinstance(item, str):
                found.add(item)
            found |= _codes_in(item)
    elif isinstance(value, list):
        for item in value:
            found |= _codes_in(item)
    elif isinstance(value, str):
        found.add(value)
    return found


def _fixed_or_pattern_codes(element: ElementChecklist | None) -> set[str]:
    if element is None:
        return set()
    codes: set[str] = set()
    for fixed in (element.fixed, element.pattern):
        if fixed is not None:
            codes |= _codes_in(fixed.value)
    return codes


@dataclass
class _Expectations:
    document: ChecklistDocument
    problems: list[str] = field(default_factory=list)

    def profile(self, profile_id: str) -> ProfileChecklist | None:
        for profile in self.document.profiles:
            if profile.id == profile_id:
                return profile
        self.problems.append(f"profile {profile_id} missing from the checklist")
        return None

    def cardinality(self, profile_id: str, element_id: str, expected: str) -> None:
        profile = self.profile(profile_id)
        element = profile.element(element_id) if profile else None
        actual = element.cardinality if element else "absent"
        if actual != expected:
            self.problems.append(
                f"{profile_id} `{element_id}`: fhir-reference says {expected},"
                f" snapshot says {actual}"
            )

    def fixed_code(self, profile_id: str, element_id: str, expected: str) -> None:
        profile = self.profile(profile_id)
        element = profile.element(element_id) if profile else None
        codes = _fixed_or_pattern_codes(element)
        if expected not in codes:
            self.problems.append(
                f"{profile_id} `{element_id}`: fhir-reference expects fixed/pattern"
                f" code {expected!r}, snapshot has {sorted(codes) or 'none'}"
            )

    def types_exact(self, profile_id: str, element_id: str, expected: list[str]) -> None:
        profile = self.profile(profile_id)
        element = profile.element(element_id) if profile else None
        actual = [t.code for t in element.types] if element else []
        if actual != expected:
            self.problems.append(
                f"{profile_id} `{element_id}`: fhir-reference says types"
                f" {expected}, snapshot says {actual}"
            )

    def binding_strength(self, profile_id: str, element_id: str, expected: str) -> None:
        profile = self.profile(profile_id)
        element = profile.element(element_id) if profile else None
        binding = element.binding if element else None
        actual = binding.strength if binding else "none"
        if actual != expected:
            value_set = binding.value_set if binding and binding.value_set else "-"
            self.problems.append(
                f"{profile_id} `{element_id}`: fhir-reference says {expected}"
                f" binding, snapshot says {actual} (value set {value_set})"
            )

    def must_support(self, profile_id: str, element_id: str, expected: bool) -> None:
        profile = self.profile(profile_id)
        element = profile.element(element_id) if profile else None
        actual = element.must_support if element else False
        if actual != expected:
            self.problems.append(
                f"{profile_id} `{element_id}`: fhir-reference lists it as"
                f" {'Must Support' if expected else 'not Must Support'},"
                f" snapshot says mustSupport={actual}"
            )

    def element_present(self, profile_id: str, element_id: str) -> None:
        profile = self.profile(profile_id)
        if profile is not None and profile.element(element_id) is None:
            self.problems.append(
                f"{profile_id} `{element_id}`: named in fhir-reference but no"
                " such element exists in the snapshot checklist"
            )

    def invariant_severity(self, profile_id: str, key: str, severity: str) -> None:
        profile = self.profile(profile_id)
        if profile is None:
            return
        matches = [inv for _, inv in profile.invariants() if inv.key == key]
        if not matches:
            self.problems.append(f"{profile_id}: invariant {key} not found in the snapshot")
        elif matches[0].severity != severity:
            self.problems.append(
                f"{profile_id}: invariant {key} severity is"
                f" {matches[0].severity!r}, fhir-reference says {severity!r}"
            )


def find_discrepancies(document: ChecklistDocument) -> list[str]:
    """Compare generated facts with docs/fhir-reference.md / PLAN A3."""
    check = _Expectations(document)
    vitals = "us-core-vital-signs"
    # Vital-signs base (fhir-reference "Observation — vital signs").
    check.cardinality(vitals, "Observation.status", "1..1")
    check.cardinality(vitals, "Observation.category", "1..*")
    check.cardinality(vitals, "Observation.category:VSCat", "1..1")
    check.fixed_code(
        vitals,
        "Observation.category:VSCat.coding.system",
        "http://terminology.hl7.org/CodeSystem/observation-category",
    )
    check.fixed_code(vitals, "Observation.category:VSCat.coding.code", "vital-signs")
    check.cardinality(vitals, "Observation.code", "1..1")
    check.cardinality(vitals, "Observation.subject", "1..1")
    check.cardinality(vitals, "Observation.effective[x]", "1..1")
    for key in ("vs-1", "vs-2", "vs-3", "obs-3", "obs-6", "obs-7"):
        check.invariant_severity(vitals, key, "error")
    # Blood pressure.
    bp = "us-core-blood-pressure"
    check.fixed_code(bp, "Observation.code", "85354-9")
    check.cardinality(bp, "Observation.component", "2..*")
    check.cardinality(bp, "Observation.component:systolic", "1..1")
    check.cardinality(bp, "Observation.component:diastolic", "1..1")
    check.fixed_code(bp, "Observation.component:systolic.code", "8480-6")
    check.fixed_code(bp, "Observation.component:diastolic.code", "8462-4")
    # Heart rate.
    check.fixed_code("us-core-heart-rate", "Observation.code", "8867-4")
    # Condition profiles.
    enc = "us-core-condition-encounter-diagnosis"
    check.cardinality(enc, "Condition.category", "1..*")
    check.cardinality(enc, "Condition.category:us-core", "1..1")
    check.fixed_code(enc, "Condition.category:us-core", "encounter-diagnosis")
    check.cardinality(enc, "Condition.code", "1..1")
    check.cardinality(enc, "Condition.subject", "1..1")
    check.cardinality(enc, "Condition.clinicalStatus", "0..1")
    check.cardinality(enc, "Condition.encounter", "0..1")
    prob = "us-core-condition-problems-health-concerns"
    check.cardinality(prob, "Condition.category:us-core", "1..*")
    check.cardinality(prob, "Condition.code", "1..1")
    check.cardinality(prob, "Condition.subject", "1..1")
    check.cardinality(prob, "Condition.clinicalStatus", "0..1")
    # MedicationRequest.
    med = "us-core-medicationrequest"
    for element_id in (
        "MedicationRequest.status",
        "MedicationRequest.intent",
        "MedicationRequest.medication[x]",
        "MedicationRequest.subject",
    ):
        check.cardinality(med, element_id, "1..1")
    check.cardinality(med, "MedicationRequest.requester", "0..1")
    check.cardinality(med, "MedicationRequest.authoredOn", "0..1")
    check.invariant_severity(med, "us-core-21", "error")
    # AllergyIntolerance.
    allergy = "us-core-allergyintolerance"
    check.cardinality(allergy, "AllergyIntolerance.code", "1..1")
    check.cardinality(allergy, "AllergyIntolerance.patient", "1..1")
    check.cardinality(allergy, "AllergyIntolerance.reaction.manifestation", "1..*")
    check.cardinality(allergy, "AllergyIntolerance.clinicalStatus", "0..1")
    check.cardinality(allergy, "AllergyIntolerance.verificationStatus", "0..1")
    check.invariant_severity(allergy, "ait-1", "error")
    check.invariant_severity(allergy, "ait-2", "error")
    # Intake anchors (fhir-reference "Intake anchors").
    patient = "us-core-patient"
    check.cardinality(patient, "Patient.identifier", "1..*")
    check.cardinality(patient, "Patient.identifier.system", "1..1")
    check.cardinality(patient, "Patient.identifier.value", "1..1")
    check.cardinality(patient, "Patient.name", "1..*")
    check.cardinality(patient, "Patient.gender", "1..1")
    check.invariant_severity(patient, "us-core-6", "error")
    practitioner = "us-core-practitioner"
    check.cardinality(practitioner, "Practitioner.identifier", "1..*")
    check.cardinality(practitioner, "Practitioner.name", "1..*")
    check.cardinality(practitioner, "Practitioner.name.family", "1..1")
    encounter = "us-core-encounter"
    check.cardinality(encounter, "Encounter.status", "1..1")
    check.cardinality(encounter, "Encounter.class", "1..1")
    check.cardinality(encounter, "Encounter.type", "1..*")
    check.cardinality(encounter, "Encounter.subject", "1..1")
    # fhir-reference: every vital profile's snapshot allows dateTime or
    # Period for effective[x]; Auris always emits effectiveDateTime.
    for profile_id in (
        vitals,
        bp,
        "us-core-heart-rate",
        "us-core-respiratory-rate",
        "us-core-body-temperature",
        "us-core-body-weight",
        "us-core-body-height",
        "us-core-pulse-oximetry",
    ):
        check.types_exact(profile_id, "Observation.effective[x]", ["dateTime", "Period"])
    # fhir-reference: body temperature, weight and height bind
    # valueQuantity.code as required to their closed UCUM unit sets
    # (ucum-bodytemp / ucum-bodyweight / ucum-bodylength).
    for profile_id in (
        "us-core-body-temperature",
        "us-core-body-weight",
        "us-core-body-height",
    ):
        check.binding_strength(profile_id, "Observation.value[x]:valueQuantity.code", "required")
    # fhir-reference: reasonCode/reasonReference are not Must Support in
    # the 6.1.0 snapshot.
    check.must_support(med, "MedicationRequest.reasonCode", False)
    check.must_support(med, "MedicationRequest.reasonReference", False)
    # fhir-reference: onset[x]/abatement[x] are Must Support choice
    # elements; the snapshot does not restrict their types.
    for profile_id in (enc, prob):
        check.element_present(profile_id, "Condition.onset[x]")
        check.element_present(profile_id, "Condition.abatement[x]")
        check.must_support(profile_id, "Condition.onset[x]", True)
        check.must_support(profile_id, "Condition.abatement[x]", True)
    # fhir-reference lists the vital-signs invariants as vs-1..3 and
    # obs-3, obs-6, obs-7.
    vitals_profile = check.profile(vitals)
    if vitals_profile is not None:
        found = {
            inv.key for _, inv in vitals_profile.invariants() if inv.key.startswith(("vs-", "obs-"))
        }
        expected = {"vs-1", "vs-2", "vs-3", "obs-3", "obs-6", "obs-7"}
        if found != expected:
            check.problems.append(
                f"{vitals}: fhir-reference lists invariants {sorted(expected)},"
                f" snapshot also carries {sorted(found - expected)}"
                f" (missing: {sorted(expected - found)})"
            )
    return check.problems


# ---------------------------------------------------------------------------
# Rendering and output files
# ---------------------------------------------------------------------------


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _short_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _format_types(types: list[TypeInfo]) -> str:
    rendered: list[str] = []
    for type_info in types:
        targets = type_info.target_profile or type_info.profile
        if targets:
            names = ", ".join(_short_url(target) for target in targets)
            rendered.append(f"{type_info.code}({names})")
        else:
            rendered.append(type_info.code)
    return "<br>".join(rendered)


def _format_fixed(element: ElementChecklist) -> str:
    parts: list[str] = []
    for label, fixed in (("fixed", element.fixed), ("pattern", element.pattern)):
        if fixed is not None:
            value = json.dumps(fixed.value, sort_keys=True, ensure_ascii=False)
            parts.append(f"{label}: `{value}`")
    return "<br>".join(parts)


def _format_binding(element: ElementChecklist) -> str:
    if element.binding is None:
        return ""
    value_set = element.binding.value_set or ""
    if value_set:
        return f"{element.binding.strength}: {_short_url(value_set.split('|')[0])}"
    return element.binding.strength


def _format_slicing(element: ElementChecklist) -> str:
    parts: list[str] = []
    if element.slice_name:
        parts.append(f"slice `{element.slice_name}`")
    if element.slicing is not None:
        for discriminator in element.slicing.discriminator:
            parts.append(f"sliced by {discriminator.type} `{discriminator.path}`")
        if element.slicing.rules:
            parts.append(f"rules: {element.slicing.rules}")
    return "<br>".join(parts)


def render_markdown(
    document: ChecklistDocument,
    sources: ElementSources,
    *,
    discrepancies: list[str],
    package_pins: list[FhirPackagePin],
) -> str:
    """Render docs/profile-checklist.md (deterministic; no timestamps)."""
    lines: list[str] = [
        "# US Core 6.1.0 profile checklist (generated)",
        "",
        "Generated by `auris checklist` from the StructureDefinition snapshots in",
        f"`{document.source_package_id}#{document.source_package_version}`"
        f" (SHA-256 `{document.source_package_sha256}`).",
        "Do not edit by hand; regenerate instead. Machine-readable form:",
        "`src/auris/contract/checklist.json`. Element sources come from the",
        "hand-maintained `src/auris/contract/element_sources.toml` (PLAN A1/A3).",
        "",
        "An element appears here if it is required (min ≥ 1), carries a fixed or",
        "pattern value, is Must Support, defines or is a slice, has a required or",
        "extensible binding, or carries an invariant. Source values:",
        "`extracted` (from the transcript via the mapper), `intake` (supplied by the",
        "A1 intake contract), `fixed` (value fixed by the profile / mapper code",
        "table), `mapper-policy` (Auris policy value), `not-emitted` (never written",
        "in v1), `unresolved` (open question, listed in the sources file).",
        "",
        "## Packages and licences",
        "",
        "| Package | Version | Licence (package.json) | SHA-256 |",
        "|---|---|---|---|",
    ]
    for pin in package_pins:
        lines.append(f"| {pin.id} | {pin.version} | {pin.license} | `{pin.sha256}` |")
    lines += [
        "",
        "Generated derivatives are committed only because `hl7.fhir.r4.core` and",
        "`hl7.fhir.us.core` are CC0-1.0. Packages are fetched into the local",
        "cache at setup and never bundled (PLAN A8).",
        "",
        "## Discrepancies with PLAN/fhir-reference",
        "",
    ]
    if discrepancies:
        lines += [f"- {_md(problem)}" for problem in discrepancies]
    else:
        lines.append(
            "None found by the automated checks in `checklist.py`"
            " (`find_discrepancies`), which encode the cardinalities, fixed codes"
            " and invariant severities stated in `docs/fhir-reference.md`."
        )
    lines += ["", "## Claimed profiles", ""]
    for profile_id in document.claimed_profiles:
        lines.append(f"- [{profile_id}](#{profile_id})")
    lines.append("")
    for profile in document.profiles:
        lines += [
            f"## {profile.id}",
            "",
            f"- Canonical: `{profile.url}`",
            f"- Resource type: `{profile.type}`"
            + (f"; base: `{profile.base_definition}`" if profile.base_definition else ""),
            "",
            "| Element | Card. | Type | Fixed / pattern | Binding | MS | Slicing | Source |",
            "|---|---:|---|---|---|---|---|---|",
        ]
        for element in profile.elements:
            entry = sources.source_for(profile.id, element.id)
            source = entry.source if entry else ("—" if not element.needs_source else "MISSING")
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{_md(element.id)}`",
                        element.cardinality,
                        _md(_format_types(element.types)),
                        _md(_format_fixed(element)),
                        _md(_format_binding(element)),
                        "yes" if element.must_support else "",
                        _md(_format_slicing(element)),
                        f"`{source}`" if entry else source,
                    ]
                )
                + " |"
            )
        invariants = profile.invariants()
        if invariants:
            lines += [
                "",
                "Invariants:",
                "",
                "| Element | Key | Severity | Human | Expression |",
                "|---|---|---|---|---|",
            ]
            for element_id, invariant in invariants:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            f"`{_md(element_id)}`",
                            f"`{invariant.key}`",
                            invariant.severity,
                            _md(invariant.human),
                            f"`{_md(invariant.expression or '')}`",
                        ]
                    )
                    + " |"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_checklist_json(document: ChecklistDocument, path: Path) -> None:
    payload = document.model_dump(mode="json", exclude_none=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def load_checklist_json(path: Path) -> ChecklistDocument:
    return ChecklistDocument.model_validate(json.loads(path.read_text()))
