"""Local SQLite store for the desktop app: the day's visits and the clinician.

Patient names are identifiable data. The store lives in the app's private data
folder; the database file is created with owner-only permissions and nothing
in it leaves the machine.
"""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path

SCHEMA_VERSION = 2
DATABASE_NAME = "auris.sqlite3"
TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class VisitState(StrEnum):
    SCHEDULED = "scheduled"
    LISTENING = "listening"
    WRITING = "writing"
    FAILED = "failed"
    READY = "ready"
    EXPORTED = "exported"


# Every allowed move. A visit never reaches export without passing review; a
# failed note can be tried again; an exported visit can be reopened (its export
# is then out of date).
TRANSITIONS: dict[VisitState, frozenset[VisitState]] = {
    VisitState.SCHEDULED: frozenset({VisitState.LISTENING, VisitState.WRITING}),
    VisitState.LISTENING: frozenset({VisitState.WRITING, VisitState.SCHEDULED}),
    VisitState.WRITING: frozenset({VisitState.READY, VisitState.FAILED}),
    VisitState.FAILED: frozenset({VisitState.WRITING, VisitState.SCHEDULED}),
    VisitState.READY: frozenset({VisitState.EXPORTED}),
    VisitState.EXPORTED: frozenset({VisitState.READY}),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS visits (
    id TEXT PRIMARY KEY,
    patient TEXT NOT NULL CHECK (length(trim(patient)) > 0),
    reason TEXT NOT NULL DEFAULT '',
    day TEXT NOT NULL,
    time TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    fictional INTEGER NOT NULL DEFAULT 0,
    failure TEXT
);
CREATE INDEX IF NOT EXISTS visits_by_day ON visits (day, time);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_COLUMNS = "id, patient, reason, day, time, state, fictional, failure"


@dataclass(frozen=True)
class Visit:
    id: str
    patient: str
    reason: str
    day: date
    time: str
    state: VisitState
    fictional: bool = False
    failure: str | None = None

    def to_api(self) -> dict[str, str | bool | None]:
        return {
            "id": self.id,
            "patient": self.patient,
            "reason": self.reason,
            "day": self.day.isoformat(),
            "time": self.time,
            "state": self.state.value,
            "fictional": self.fictional,
            "failure": self.failure,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clean(text: str, field: str, *, required: bool, limit: int) -> str:
    value = " ".join(text.split())
    if required and not value:
        raise ValueError(f"{field} is required")
    if len(value) > limit:
        raise ValueError(f"{field} must be at most {limit} characters")
    return value


class VisitStore:
    """One clinician's visits on this machine."""

    def __init__(self, directory: Path) -> None:
        if not directory.exists():
            directory.mkdir(parents=True, mode=0o700)
        self.directory = directory
        self.path = directory / DATABASE_NAME
        if not self.path.exists():
            os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        with self._db() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError("this data was written by a newer version of Auris")
            if version == 1:
                db.execute("ALTER TABLE visits ADD COLUMN fictional INTEGER NOT NULL DEFAULT 0")
                db.execute("ALTER TABLE visits ADD COLUMN failure TEXT")
            db.executescript(_SCHEMA)
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _visit(row: tuple) -> Visit:
        return Visit(
            id=row[0],
            patient=row[1],
            reason=row[2],
            day=date.fromisoformat(row[3]),
            time=row[4],
            state=VisitState(row[5]),
            fictional=bool(row[6]),
            failure=row[7],
        )

    def add_visit(
        self, patient: str, reason: str, day: date, time: str, *, fictional: bool = False
    ) -> Visit:
        patient = _clean(patient, "patient", required=True, limit=120)
        reason = _clean(reason, "reason", required=False, limit=200)
        if not TIME.match(time):
            raise ValueError("time must be HH:MM, 24-hour")
        visit = Visit(uuid.uuid4().hex, patient, reason, day, time, VisitState.SCHEDULED, fictional)
        stamp = _now()
        with self._db() as db:
            db.execute(
                "INSERT INTO visits (id, patient, reason, day, time, state, created_at, "
                "updated_at, fictional) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    visit.id,
                    patient,
                    reason,
                    day.isoformat(),
                    time,
                    visit.state,
                    stamp,
                    stamp,
                    int(fictional),
                ),
            )
        return visit

    def get(self, visit_id: str) -> Visit | None:
        with self._db() as db:
            row = db.execute(f"SELECT {_COLUMNS} FROM visits WHERE id = ?", (visit_id,)).fetchone()
        return self._visit(row) if row else None

    def visits_on(self, day: date) -> list[Visit]:
        with self._db() as db:
            rows = db.execute(
                f"SELECT {_COLUMNS} FROM visits WHERE day = ? ORDER BY time, created_at",
                (day.isoformat(),),
            ).fetchall()
        return [self._visit(row) for row in rows]

    def in_state(self, state: VisitState) -> list[Visit]:
        with self._db() as db:
            rows = db.execute(
                f"SELECT {_COLUMNS} FROM visits WHERE state = ? ORDER BY day, time", (state,)
            ).fetchall()
        return [self._visit(row) for row in rows]

    def move(self, visit_id: str, to: VisitState, *, failure: str | None = None) -> Visit:
        """Change state along an allowed move; only a failed visit carries a failure."""
        with self._db() as db:
            row = db.execute("SELECT state FROM visits WHERE id = ?", (visit_id,)).fetchone()
            if row is None:
                raise KeyError(visit_id)
            current = VisitState(row[0])
            if to not in TRANSITIONS[current]:
                raise ValueError(f"a visit cannot move from {current} to {to}")
            db.execute(
                "UPDATE visits SET state = ?, failure = ?, updated_at = ? "
                "WHERE id = ? AND state = ?",
                (to, failure if to == VisitState.FAILED else None, _now(), visit_id, current),
            )
        visit = self.get(visit_id)
        assert visit is not None
        return visit

    def clinician(self) -> str | None:
        with self._db() as db:
            row = db.execute("SELECT value FROM settings WHERE key = 'clinician'").fetchone()
        return row[0] if row else None

    def set_clinician(self, name: str) -> str:
        name = _clean(name, "name", required=True, limit=80)
        with self._db() as db:
            db.execute(
                "INSERT INTO settings VALUES ('clinician', ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (name,),
            )
        return name
