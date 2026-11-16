"""Build the pipeline's intake (patient, clinician, encounter) from a visit.

The desktop app collects only a name, a reason and a time, so the intake
carries exactly that: the patient's and clinician's names, gender "unknown",
local UUID identifiers and the visit's start time. Nothing is invented.
"""

from __future__ import annotations

import uuid
from datetime import datetime, tzinfo

from auris.app.store import Visit
from auris.intake import Intake

UUID_SYSTEM = "urn:ietf:rfc:3986"
_TITLES = {"dr", "dr.", "doctor", "prof", "prof.", "mr", "mr.", "mrs", "mrs.", "ms", "ms.", "mx"}


def _split(name: str) -> tuple[str, tuple[str, ...] | None]:
    parts = [part for part in name.split() if part.lower() not in _TITLES] or name.split()
    family = parts[-1]
    given = tuple(parts[:-1]) or None
    return family, given


def _identifier(kind: str, value: str) -> dict[str, str]:
    stable = uuid.uuid5(uuid.NAMESPACE_URL, f"auris:{kind}:{value}")
    return {"system": UUID_SYSTEM, "value": f"urn:uuid:{stable}"}


def build_intake(visit: Visit, clinician: str, *, tz: tzinfo | None = None) -> Intake:
    """``tz`` fixes the visit's time zone (pass UTC so every machine builds identical
    records); by default it is this machine's local zone."""
    patient_family, patient_given = _split(visit.patient)
    clinician_family, clinician_given = _split(clinician)
    start = datetime.combine(visit.day, datetime.strptime(visit.time, "%H:%M").time())
    start = start.replace(tzinfo=tz) if tz is not None else start.astimezone()
    start_text = start.isoformat(timespec="seconds")
    patient_id = f"patient-{visit.id[:12]}"
    payload = {
        "fictional": visit.fictional,
        "patient": {
            "id": patient_id,
            "identifier": [_identifier("patient", visit.id)],
            "name": [{"family": patient_family, "given": patient_given}],
            "gender": "unknown",
        },
        "practitioner": {
            "id": "practitioner-local",
            "identifier": [_identifier("practitioner", clinician)],
            "name": [{"family": clinician_family, "given": clinician_given}],
        },
        "encounter": {
            "id": f"encounter-{visit.id[:12]}",
            "status": "finished",
            "class": {
                "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                "code": "AMB",
                "display": "ambulatory",
            },
            "type": [{"text": visit.reason or "Office visit"}],
            "subject": {"reference": f"Patient/{patient_id}", "display": visit.patient},
            "period": {"start": start_text},
        },
    }
    for name in (payload["patient"]["name"][0], payload["practitioner"]["name"][0]):
        if name["given"] is None:
            del name["given"]
    return Intake.model_validate(payload)
