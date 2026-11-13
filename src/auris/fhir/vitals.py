"""A3 vital-sign builder: no unit conversion, padding, or invented dates."""

from __future__ import annotations

import math
import re
from datetime import datetime
from decimal import Decimal

from pydantic import TypeAdapter, ValidationError

from auris.assertions import Temporality, VitalSignAssertion
from auris.consistency import numeric_tokens
from auris.evidence import AssertionEvidenceResult, EvidenceLane
from auris.fhir.common import Anchors, base
from auris.fhir.contracts import UnitSets, element, fixed
from auris.fhir.models import BuildResult
from auris.intake import IntakeDateTime
from auris.terminology import normalise
from auris.transcript import SpeakerRole

_PROFILES = {
    "blood pressure": "blood-pressure",
    "bp": "blood-pressure",
    "heart rate": "heart-rate",
    "pulse": "heart-rate",
    "respiratory rate": "respiratory-rate",
    "respiration rate": "respiratory-rate",
    "temperature": "body-temperature",
    "body temperature": "body-temperature",
    "weight": "body-weight",
    "body weight": "body-weight",
    "height": "body-height",
    "body height": "body-height",
    "spo2": "pulse-oximetry",
    "pulse oximetry": "pulse-oximetry",
    "oxygen saturation": "pulse-oximetry",
}
# Spoken forms map to an exact UCUM code, never to a converted value.
_UNIT_NAMES = {
    "mmhg": "mm[Hg]",
    "millimeters of mercury": "mm[Hg]",
    "millimetres of mercury": "mm[Hg]",
    "beats per minute": "/min",
    "bpm": "/min",
    "per minute": "/min",
    "breaths per minute": "/min",
    "percent": "%",
    "per cent": "%",
    "celsius": "Cel",
    "degrees celsius": "Cel",
    "°c": "Cel",
    "fahrenheit": "[degF]",
    "degrees fahrenheit": "[degF]",
    "°f": "[degF]",
    "kilogram": "kg",
    "kilograms": "kg",
    "gram": "g",
    "grams": "g",
    "pound": "[lb_av]",
    "pounds": "[lb_av]",
    "lb": "[lb_av]",
    "lbs": "[lb_av]",
    "centimeter": "cm",
    "centimeters": "cm",
    "centimetre": "cm",
    "centimetres": "cm",
    "inch": "[in_i]",
    "inches": "[in_i]",
}
# Quantity.unit is for people; Quantity.code remains the exact UCUM spelling.
_UNIT_DISPLAYS = {
    "mm[Hg]": "mmHg",
    "Cel": "°C",
    "[degF]": "°F",
    "[lb_av]": "lb",
    "[in_i]": "in",
}
_NUMBER = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
_BP_PAIR = rf"(?P<systolic>{_NUMBER})\s*(?:/|over|on)\s*(?P<diastolic>{_NUMBER})"
BP_SPLIT_FIELDS_FLAG = "bp_parsed_from_value_and_unit"
_DATETIME = TypeAdapter(IntakeDateTime)


def vital_profile(text: str | None) -> str | None:
    suffix = {normalise(k): v for k, v in _PROFILES.items()}.get(normalise(text or ""))
    return f"us-core-{suffix}" if suffix else None


def vital_synonyms(text: str) -> tuple[str, ...]:
    """Reuse the mapper's verified vital-name groups for evidence support."""
    profile = vital_profile(text)
    return tuple(name for name in _PROFILES if profile and vital_profile(name) == profile)


def _number(text: str | None) -> int | float | None:
    if text is None or not re.fullmatch(_NUMBER, text.strip()):
        return None
    decimal = Decimal(text.strip())
    if decimal == decimal.to_integral_value():
        return int(decimal)
    value = float(decimal)
    if not math.isfinite(value) or Decimal(str(value)) != decimal:
        return None
    return value


def _date(text: str) -> str | None:
    value = text.strip()
    try:
        parsed = _DATETIME.validate_python(value)
        return parsed if len(parsed) >= 10 else None
    except ValidationError:
        pass
    for format_ in ("%B %d, %Y", "%B %d %Y", "%d %B %Y", "%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(value, format_).date().isoformat()
        except ValueError:
            continue
    return None


def _bp_values(text: str | None) -> list[int | float]:
    parts = re.fullmatch(rf"\s*{_BP_PAIR}\s*", text or "", re.IGNORECASE)
    if parts:
        values = [_number(part) for part in parts.groups()]
        if all(value is not None for value in values):
            return [value for value in values if value is not None]
    return []


def _split_bp_fields(
    assertion: VitalSignAssertion, evidence: AssertionEvidenceResult
) -> tuple[list[int | float], str | None] | None:
    """Recover a split BP pair only from numeric tokens in one aligned quote."""
    if not assertion.value_text or not assertion.unit_text or evidence.lane != EvidenceLane.PASS:
        return None
    combined = f"{assertion.value_text} {assertion.unit_text}"
    parts = re.fullmatch(
        rf"\s*(?P<pair>{_BP_PAIR})(?:\s+(?P<unit>\S.*?))?\s*", combined, re.IGNORECASE
    )
    if parts is None:
        return None
    values = _bp_values(parts["pair"])
    numbers = frozenset((parts["systolic"], parts["diastolic"]))
    if not values or not any(
        item.aligned is not None and numbers.issubset(numeric_tokens(item.aligned.aligned_text))
        for item in evidence.items
    ):
        return None
    return values, parts["unit"]


def build_vital(
    assertion: VitalSignAssertion,
    identifier: str,
    anchors: Anchors,
    unit_sets: UnitSets,
    evidence: AssertionEvidenceResult,
    *,
    unit_unusable: bool = False,
) -> BuildResult:
    profile = vital_profile(assertion.concept_text)
    if profile is None:
        return BuildResult(ledger_only=True, reasons=("vital_not_supported",))
    reasons: list[str] = []
    notes: list[str] = []
    flags: list[str] = []
    bp = profile == "us-core-blood-pressure"
    quantity_path = (
        "Observation.component:systolic.value[x]" if bp else "Observation.value[x]:valueQuantity"
    )
    unit_definition = element(profile, f"{quantity_path}.code")
    fixed_unit = unit_definition.get("fixed", {}).get("value")
    spoken = assertion.unit_text
    values = _bp_values(assertion.value_text) if bp else []
    if bp and not values:
        recovered = _split_bp_fields(assertion, evidence)
        if recovered is not None:
            values, spoken = recovered
            notes.append("systolic/diastolic parsed from value and unit text")
            flags.append(BP_SPLIT_FIELDS_FLAG)
    if unit_unusable:
        # A commentary-bearing unit is not a spoken unit or permission to
        # default one. Recover only a known unit directly after these values.
        spoken = _unit_from_quote(assertion, evidence, unit_sets, bp=bp)
        if spoken is None:
            reasons.append("unit_missing")
    if spoken is None:
        unit = fixed_unit
        if unit is None:
            reasons.append("unit_missing")
    else:
        # Preserve case-sensitive UCUM spelling; aliases are only spoken words.
        unit = _UNIT_NAMES.get(spoken.strip().casefold(), spoken.strip())
        if fixed_unit and unit != fixed_unit:
            reasons.append("unit_not_in_profile_set")
    if not fixed_unit and unit is not None:
        set_id = unit_definition["binding"]["value_set"].split("|")[0].rsplit("/", 1)[-1]
        if unit not in unit_sets[set_id]:
            reasons.append("unit_not_in_profile_set")

    if bp:
        if len(values) != 2:
            reasons.append("bp_component_missing")
    else:
        value = _number(assertion.value_text)
        if value is None:
            reasons.append("value_missing_or_unparseable")
        else:
            values = [value]

    effective = None
    if assertion.stated_time_text:
        effective = _date(assertion.stated_time_text)
        if effective is None:
            reasons.append(
                "historical_without_date"
                if assertion.temporality == Temporality.PAST
                else "measurement_date_not_day_precision"
            )
    elif (
        assertion.temporality == Temporality.CURRENT
        and assertion.source_speaker_role == SpeakerRole.CLINICIAN
    ):
        effective = _date(anchors.encounter["period"]["start"])
        if effective is None:
            reasons.append("measurement_date_not_day_precision")
        else:
            notes.append(
                "effectiveDateTime inherited from Encounter.period.start (in-visit reading)"
            )
    else:
        reasons.append("historical_without_date")
    if assertion.temporality in (
        None,
        Temporality.UNCLEAR,
        Temporality.FUTURE,
        Temporality.RESOLVED,
    ):
        reasons.append("vital_temporality_unclear")
    if reasons:
        return BuildResult(profile=profile, reasons=tuple(dict.fromkeys(reasons)))

    resource = base("Observation", identifier, profile)
    resource.update(
        {
            "status": "preliminary",
            "category": [
                {
                    "coding": [
                        {
                            "system": fixed(profile, "Observation.category:VSCat.coding.system"),
                            "code": fixed(profile, "Observation.category:VSCat.coding.code"),
                        }
                    ]
                }
            ],
            "subject": anchors.reference("patient"),
            "encounter": anchors.reference("encounter"),
            "effectiveDateTime": effective,
        }
    )
    if profile == "us-core-pulse-oximetry":
        resource["code"] = {
            "coding": [
                fixed(profile, "Observation.code.coding:PulseOx"),
                fixed(profile, "Observation.code.coding:O2Sat"),
            ]
        }
    else:
        resource["code"] = fixed(profile, "Observation.code")
    if bp:
        resource["component"] = [
            {
                "code": fixed(profile, f"Observation.component:{name}.code"),
                "valueQuantity": {
                    "value": value,
                    "unit": _UNIT_DISPLAYS.get(unit, unit),
                    "system": fixed(profile, f"Observation.component:{name}.value[x].system"),
                    "code": unit,
                },
            }
            for name, value in zip(("systolic", "diastolic"), values, strict=True)
        ]
    else:
        resource["valueQuantity"] = {
            "value": values[0],
            "unit": _UNIT_DISPLAYS.get(unit, unit),
            "system": fixed(profile, f"{quantity_path}.system"),
            "code": unit,
        }
    return BuildResult(profile=profile, resource=resource, flags=tuple(flags), notes=tuple(notes))


def _unit_from_quote(
    assertion: VitalSignAssertion,
    evidence: AssertionEvidenceResult,
    unit_sets: UnitSets,
    *,
    bp: bool,
) -> str | None:
    if evidence.lane != EvidenceLane.PASS:
        return None
    expected = _bp_values(assertion.value_text) if bp else [_number(assertion.value_text)]
    if not expected or None in expected:
        return None
    # Reuse exactly the mapper's existing spelling table and profile unit sets.
    aliases = {unit: unit for units in unit_sets.values() for unit in units}
    aliases.update({"mm[Hg]": "mm[Hg]", "/min": "/min"})
    aliases.update(_UNIT_NAMES)
    found = set()
    pattern = _BP_PAIR if bp else rf"(?P<value>{_NUMBER})"
    for item in evidence.items:
        if item.aligned is None:
            continue
        quote = item.aligned.aligned_text
        for match in re.finditer(rf"(?<![\w.+-]){pattern}(?![\d.])", quote, re.IGNORECASE):
            values = _bp_values(match.group()) if bp else [_number(match.group())]
            if values != expected:
                continue
            tail = quote[match.end() :].lstrip()
            for alias, code in sorted(aliases.items(), key=lambda pair: -len(pair[0])):
                # UCUM codes remain case-sensitive, spoken aliases do not.
                flags = re.IGNORECASE if alias in _UNIT_NAMES else 0
                if re.match(re.escape(alias) + r"(?![\w/\[\]])", tail, flags):
                    found.add(code)
                    break
    return next(iter(found)) if len(found) == 1 else None
