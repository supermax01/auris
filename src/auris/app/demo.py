"""A fictional clinic day for the demo mode. Every name and reason is invented.

All demo visits start scheduled: a visit only shows review or export once a
real note has been written for it (in the demo, from the fictional transcript).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from auris.app.store import VisitStore

DEMO_DAY: tuple[tuple[str, str, str], ...] = (
    ("08:30", "Ellis Varga", "Knee pain after a fall"),
    ("08:45", "Jun Okafor", "Blood pressure check"),
    ("09:00", "Mara Quill", "Cough for four days"),
    ("09:15", "Ines Morrow", "Follow-up on a rash"),
    ("09:30", "Kofi Aldane", "Repeat prescription"),
    ("09:45", "Ruth Calder", "Headaches"),
    ("10:00", "Sami Ortega", "Sore throat"),
    ("10:15", "Teodor Brann", "Annual check"),
    ("10:30", "Lena Voss", "Back pain"),
)


def demo_transcript(root: Path) -> Path:
    """The repository's fictional demo transcript (all participants fictional)."""
    return root / "examples" / "transcript.json"


def seed_demo_day(store: VisitStore, day: date) -> bool:
    """Fill an empty day with the fictional schedule; never touch real visits."""
    if store.visits_on(day):
        return False
    for time, patient, reason in DEMO_DAY:
        store.add_visit(patient, reason, day, time, fictional=True)
    return True
