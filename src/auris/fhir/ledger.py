"""A3 rows that make no FHIR resource in tier zero."""

from auris.fhir.models import BuildResult


def build_symptom() -> BuildResult:
    return BuildResult(ledger_only=True)


def build_reported_medication() -> BuildResult:
    return BuildResult(ledger_only=True)


def build_allergy_denial() -> BuildResult:
    return BuildResult(
        ledger_only=True, reasons=("snomed_not_supplied", "clinician_confirmation_required")
    )
