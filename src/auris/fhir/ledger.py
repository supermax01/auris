"""A3 rows that make no FHIR resource in tier zero."""

from auris.fhir.models import BuildResult
from auris.reason_codes import CheckReason


def build_symptom() -> BuildResult:
    return BuildResult(ledger_only=True)


def build_reported_medication() -> BuildResult:
    return BuildResult(ledger_only=True)


def build_allergy_denial() -> BuildResult:
    return BuildResult(
        ledger_only=True,
        reasons=(CheckReason.SNOMED_NOT_SUPPLIED, CheckReason.CLINICIAN_CONFIRMATION_REQUIRED),
    )
