"""Small scorer-owned lexical/unit tables; no production mapper imports.

These are spelling equivalences, never physical unit conversions, default-unit
inference, drug synonyms, or parsing of number words/clinical instructions.
"""

import re
import unicodedata
from decimal import Decimal

NUMBER = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"


def normalize(text: str | None) -> str:
    """Preserve signs, decimals and word order; do not infer clinical synonyms."""
    value = unicodedata.normalize("NFKC", text or "").casefold().replace("’", "'").replace("−", "-")
    return " ".join(re.findall(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)|[^\W_]+(?:'[^\W_]+)*|[%/]", value))


def _unit_key(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


VITAL_UNITS = {
    canonical: aliases
    for canonical, aliases in (
        (
            "/min",
            ("beats per minute", "breaths per minute", "per minute", "beats/min", "breaths/min"),
        ),
        ("%", ("percent",)),
        ("[degF]", ("degree fahrenheit", "degrees fahrenheit", "fahrenheit", "°f")),
        ("Cel", ("degree celsius", "degrees celsius", "celsius", "°c")),
        ("[lb_av]", ("lb", "lbs", "pound", "pounds")),
        ("kg", ("kilogram", "kilograms")),
        ("[in_i]", ("in", "inch", "inches")),
        ("cm", ("centimetre", "centimetres", "centimeter", "centimeters")),
        ("mm[Hg]", ("mmhg", "millimetres of mercury", "millimeters of mercury")),
    )
}
_VITAL_UNIT_LOOKUP = {
    _unit_key(alias): canonical
    for canonical, aliases in VITAL_UNITS.items()
    for alias in (canonical, *aliases)
}
_DOSE_UNIT_LOOKUP = {
    alias: canonical
    for canonical, aliases in {
        "mg": ("milligram", "milligrams"),
        "mcg": ("microgram", "micrograms"),
        "g": ("gram", "grams"),
        "mL": ("millilitre", "millilitres", "milliliter", "milliliters"),
        "units": ("unit",),
    }.items()
    for alias in (canonical.casefold(), *aliases)
}


def decimal_text(value: str) -> str:
    number = Decimal(value)
    if number == 0:
        return "0"
    # Fixed-point formatting avoids rounding through Decimal's context.
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def vital_value(text: str | None) -> tuple[str, ...] | None:
    value = unicodedata.normalize("NFKC", text or "").strip().replace("−", "-")
    pair = re.fullmatch(rf"({NUMBER})\s*(?:over|/)\s*({NUMBER})", value, flags=re.IGNORECASE)
    if pair:
        return tuple(decimal_text(n) for n in pair.groups())
    return (decimal_text(value),) if re.fullmatch(NUMBER, value) else None


def vital_unit(text: str | None) -> str | None:
    return _VITAL_UNIT_LOOKUP.get(_unit_key(text or ""))


def normalize_dose(text: str | None) -> str:
    """Normalize numbers and the listed dose units, including within dose prose.

    Unrecognized words remain literal: tablets, schedules, concentrations and
    compound instructions are not interpreted or converted into other doses.
    """
    tokens = normalize(text).split()
    return " ".join(
        decimal_text(token) if re.fullmatch(NUMBER, token) else _DOSE_UNIT_LOOKUP.get(token, token)
        for token in tokens
    )


def dose_amounts(text: str | None) -> tuple[tuple[str, str], ...]:
    """Explicit number/unit pairs only, using v2 spelling equivalences.

    Preserve order and units; never convert or interpret frequency as an amount.
    """
    tokens = normalize_dose(text).split()
    units = set(_DOSE_UNIT_LOOKUP.values())
    return tuple(
        (number, unit)
        for number, unit in zip(tokens, tokens[1:], strict=False)
        if re.fullmatch(NUMBER, number) and unit in units
    )
