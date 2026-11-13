"""Scorer v3 attribute/field diagnostics, independent of pipeline hygiene."""

import re
from collections.abc import Sequence

from auris.eval.critical import CriticalFinding, _anchored, patient_record_crossing
from auris.eval.gold import GoldAssertion
from auris.eval.matching import concept_equal
from auris.eval.normalization import dose_amounts
from auris.eval.predictions import Prediction

NONCRITICAL_ERRORS = ("attribute_errors", "field_errors")
_VALUE_FIELDS = ("value_text", "unit_text", "dose_text")
_NULLABLE_FIELDS = (*_VALUE_FIELDS, "route_text", "frequency_text", "stated_time_text")
_COMMENTARY = re.compile(
    r"[()]|\b(?:implied|not\s+explicitly\s+stated|presumably|likely|context)\b", re.IGNORECASE
)


def findings(prediction: Prediction, gold: Sequence[GoldAssertion]) -> dict[str, CriticalFinding]:
    result = {}
    concepts = [g for g in gold if concept_equal(prediction, g)]
    related = [g for g in concepts if _anchored(prediction, g)] or concepts
    if (
        related
        and all(prediction.experiencer != g.experiencer for g in related)
        and not all(patient_record_crossing(prediction.experiencer, g.experiencer) for g in related)
    ):
        result["attribute_errors"] = CriticalFinding(
            tuple(related),
            f"experiencer {prediction.experiencer!r} vs gold "
            f"{sorted({g.experiencer for g in related}, key=str)!r}; "
            "no established patient/non-patient crossing",
        )
    reasons = []
    for field in _NULLABLE_FIELDS:
        text = getattr(prediction, field)
        if text is None:
            continue
        if text.strip().casefold() in {"null", "none", "n/a", "unknown"}:
            reasons.append(f"{field}: literal null string {text!r}")
        elif field in _VALUE_FIELDS and _COMMENTARY.search(text):
            reasons.append(f"{field}: commentary {text!r}")
        elif field == "dose_text" and not dose_amounts(text):
            reasons.append(f"dose_text: no explicit number/unit amount in {text!r}")
    if reasons:
        result["field_errors"] = CriticalFinding(tuple(related), "; ".join(reasons))
    return result
