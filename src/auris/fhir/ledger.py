"""Fact kinds that make no FHIR resource without SNOMED CT; they stay in the note."""

from auris.fhir.models import BuildResult
from auris.reason_codes import CheckReason


def build_symptom() -> BuildResult:
    return BuildResult(ledger_only=True)


def build_plan_item() -> BuildResult:
    """Tests, referrals, follow-up and advice: kept for review, never a record."""
    return BuildResult(ledger_only=True)


def build_social_history() -> BuildResult:
    """Living situation, work, smoking, alcohol, drugs: kept for review, no record."""
    return BuildResult(ledger_only=True)


def build_reported_medication() -> BuildResult:
    return BuildResult(ledger_only=True)


def build_allergy_denial() -> BuildResult:
    return BuildResult(
        ledger_only=True,
        reasons=(CheckReason.SNOMED_NOT_SUPPLIED, CheckReason.CLINICIAN_CONFIRMATION_REQUIRED),
    )
