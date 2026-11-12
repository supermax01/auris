"""A3 vital-sign builder: no unit conversion, padding, or invented dates."""

from __future__ import annotations

import math
import re
from datetime import datetime
from decimal import Decimal

from pydantic import TypeAdapter, ValidationError

from auris.assertions import Temporality, VitalSignAssertion
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
_NUMBER = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
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


def build_vital(
    assertion: VitalSignAssertion,
    identifier: str,
    anchors: Anchors,
    unit_sets: UnitSets,
) -> BuildResult:
    profile = vital_profile(assertion.concept_text)
    if profile is None:
        return BuildResult(ledger_only=True, reasons=("vital_not_supported",))
    reasons: list[str] = []
    notes: list[str] = []
    bp = profile == "us-core-blood-pressure"
    quantity_path = (
        "Observation.component:systolic.value[x]" if bp else "Observation.value[x]:valueQuantity"
    )
    unit_definition = element(profile, f"{quantity_path}.code")
    fixed_unit = unit_definition.get("fixed", {}).get("value")
    spoken = assertion.unit_text
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

    values: list[int | float] = []
    if bp:
        parts = re.fullmatch(
            rf"\s*({_NUMBER})\s*(?:/|over)\s*({_NUMBER})\s*", assertion.value_text or ""
        )
        if parts:
            parsed = [_number(part) for part in parts.groups()]
            if all(value is not None for value in parsed):
                values = [value for value in parsed if value is not None]
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
                    "unit": unit,
                    "system": fixed(profile, f"Observation.component:{name}.value[x].system"),
                    "code": unit,
                },
            }
            for name, value in zip(("systolic", "diastolic"), values, strict=True)
        ]
    else:
        resource["valueQuantity"] = {
            "value": values[0],
            "unit": unit,
            "system": fixed(profile, f"{quantity_path}.system"),
            "code": unit,
        }
    return BuildResult(profile=profile, resource=resource, notes=tuple(notes))
