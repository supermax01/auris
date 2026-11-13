"""Small allowlisted API over prepared transcripts and one annotator's own work."""

import hmac
import json
import secrets
import sqlite3
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import Field

from auris._storage import write_private_text
from auris.annotation.models import (
    AnnotationDocument,
    AnnotationFact,
    Assignment,
    ImportantFact,
    Selection,
)
from auris.annotation.pilot import load_prepared
from auris.annotation.timing import EncounterClock
from auris.assertions import ASSERTION_MODELS
from auris.datasets.splits import PublicSplit
from auris.eval.gold import Record, canonical_json, digest
from auris.eval.ledger import register_test_use
from auris.terminology.index import TerminologyIndex
from auris.terminology.text import ICD10CM, RXNORM, normalise
from auris.transcript import SpeakerRole, Transcript


class SaveRequest(Record):
    encounter_id: str
    status: Literal["in_progress", "done"]
    assertions: tuple[AnnotationFact, ...]
    out_of_scope: tuple[ImportantFact, ...]
    segment_roles: dict[str, SpeakerRole]
    notes: str


class TimingRequest(Record):
    encounter_id: str
    focused: bool = Field(strict=True)
    idle_seconds: float = Field(ge=0)


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("annotation workdir cannot be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise ValueError("annotation workdir must have permissions 0700")


class AnnotationSession:
    def __init__(
        self,
        assignment: Assignment,
        *,
        cache: Path,
        manifests: Path,
        workdir: Path,
        guidelines: bytes,
        locked_run: bool = False,
        confirmed: bool = False,
    ) -> None:
        # Validate against the coordinator's frozen membership, so relabelling a
        # copied test manifest as dev cannot bypass the lock.
        frozen = PublicSplit.model_validate_json(
            (manifests / f"{assignment.dataset.id}-{assignment.dataset.split}.json").read_text(
                "utf-8"
            )
        )
        if frozen != assignment.dataset:
            raise ValueError("assignment disagrees with frozen split")
        if digest(guidelines) != assignment.guidelines_sha256:
            raise ValueError("guideline version changed; coordinator must issue a new assignment")
        register_test_use(
            cache / "eval" / "test-ledger.jsonl",
            frozen,
            f"annotation-{assignment.annotator_id}-{secrets.token_hex(12)}",
            "run",
            locked_run=locked_run,
            confirmed=confirmed,
        )
        self.assignment = assignment
        self.transcripts = {
            key: load_prepared(cache, frozen, key) for key in assignment.encounter_ids
        }
        private_directory(workdir)
        self.folder = workdir / assignment.annotator_id
        private_directory(self.folder)
        self.token = secrets.token_urlsafe(32)
        self.index_path = cache / "terminology" / "index.sqlite"
        self.documents: dict[str, AnnotationDocument] = {}
        self.clocks: dict[str, EncounterClock] = {}
        self.current: str | None = None
        # Only exact assigned filenames are read; run outputs have no reader.
        for key, transcript in self.transcripts.items():
            path = self._path(key)
            if path.exists():
                if stat.S_IMODE(path.stat().st_mode) != 0o600:
                    raise ValueError("annotation file must have permissions 0600")
                doc = AnnotationDocument.model_validate_json(path.read_text("utf-8"))
                self._verify(doc, transcript)
                self.documents[key] = doc

    def _path(self, key: str) -> Path:
        if key not in self.transcripts:
            raise ValueError("encounter is not assigned")
        path = self.folder / f"{key}.annotation.json"
        if path.is_symlink():
            raise ValueError("annotation file cannot be a symlink")
        return path

    def _verify(self, doc: AnnotationDocument, transcript: Transcript) -> None:
        if (
            doc.annotator_id != self.assignment.annotator_id
            or doc.encounter_id not in self.transcripts
            or self.transcripts[doc.encounter_id] != transcript
            or doc.guidelines_sha256 != self.assignment.guidelines_sha256
        ):
            raise ValueError("annotation identity or guideline mismatch")
        doc.verify(transcript)

    def _write(self, doc: AnnotationDocument) -> None:
        self._verify(doc, self.transcripts[doc.encounter_id])
        write_private_text(
            self._path(doc.encounter_id), canonical_json(doc.model_dump(mode="json"))
        )
        self.documents[doc.encounter_id] = doc

    def _snapshot(self, key: str) -> AnnotationDocument:
        doc, clock = self.documents[key], self.clocks[key]
        if doc.status == "done":
            return doc.model_copy(update={"updated_at": datetime.now(UTC)})
        now = time.monotonic()
        timing = clock.event(
            now, focused=clock.focused, idle_seconds=max(0, now - clock.activity_at)
        )
        return doc.model_copy(update={"timing": timing, "updated_at": datetime.now(UTC)})

    def open_encounter(self, key: str) -> dict:
        if key not in self.transcripts:
            raise ValueError("encounter is not assigned")
        if self.current is not None and self.current != key:
            self._write(self._snapshot(self.current))
            old = self.clocks.pop(self.current)
            old.focused = False
        transcript = self.transcripts[key]
        if key not in self.documents:
            now = datetime.now(UTC)
            self.documents[key] = AnnotationDocument(
                annotator_id=self.assignment.annotator_id,
                encounter_id=key,
                transcript_sha256=transcript.content_hash,
                guidelines_sha256=self.assignment.guidelines_sha256,
                started_at=now,
                updated_at=now,
            )
        if key not in self.clocks:
            self.clocks[key] = EncounterClock(self.documents[key].timing, time.monotonic())
        self.current = key
        self._write(self._snapshot(key))
        # Only dialogue/roles/times; source notes, intakes and inference artifacts
        # are never included in this response.
        return {
            "document": self.documents[key].model_dump(mode="json"),
            "turns": [
                {
                    "id": s.id,
                    "text": s.text,
                    "role": s.role.value,
                    "turn_number": s.turn_number or i,
                    "start_seconds": s.start_seconds,
                    "end_seconds": s.end_seconds,
                }
                for i, s in enumerate(transcript.segments, 1)
            ],
        }

    def _current(self, key: str) -> None:
        if key != self.current:
            raise ValueError("open the assigned encounter first")

    def lookup(self, text: str, system: str) -> list[dict]:
        if system not in (RXNORM, ICD10CM) or not 1 <= len(text.strip()) <= 200:
            raise ValueError("choose RxNorm or ICD-10-CM and a query of 1–200 characters")
        try:
            with TerminologyIndex(self.index_path) as index:
                # Search official local rows literally. Do not use the mapper's
                # hand-authored drug aliases, coding choices or fuzzy ranking
                # to shape the independent human concept key (PLAN A9).
                tokens = normalise(text).split()
                if not tokens:
                    return []
                clauses = " AND ".join("normalised LIKE ?" for _ in tokens)
                rows = index.connection.execute(
                    "SELECT DISTINCT system, code, display, tty FROM terms "
                    "WHERE system=? AND assigned_code=code "
                    "AND tty IN ('IN', 'SCD', 'SBD', 'billable') "
                    f"AND (code=? OR ({clauses})) "
                    "ORDER BY code=? DESC, length(display), code, display LIMIT 20",
                    (system, text.strip(), *(f"%{token}%" for token in tokens), text.strip()),
                ).fetchall()
                return [
                    {
                        "system": row["system"],
                        "code": row["code"],
                        "display": row["display"],
                        "term_type": row["tty"],
                    }
                    for row in rows
                ]
        except sqlite3.Error:
            raise ValueError(
                "local terminology index unavailable; ask coordinator to prepare it"
            ) from None

    def _verify_codes(self, facts: tuple[AnnotationFact, ...]) -> None:
        if not any(f.codes or not f.gold_concept_id.startswith("auris:") for f in facts):
            return
        try:
            with TerminologyIndex(self.index_path) as index:
                for fact in facts:
                    if not fact.codes:
                        if not fact.gold_concept_id.startswith("auris:"):
                            raise ValueError("uncoded concepts require an auris: slug")
                        continue
                    if len(fact.codes) != 1:
                        raise ValueError("choose one clearly fitting local concept")
                    code = fact.codes[0]
                    if (
                        code.system not in (RXNORM, ICD10CM)
                        or fact.gold_concept_id != f"{code.system}:{code.code}"
                    ):
                        raise ValueError("concept id must match the selected local code")
                    row = index.connection.execute(
                        "SELECT 1 FROM terms WHERE system=? AND code=? AND assigned_code=code "
                        "AND tty IN ('IN', 'SCD', 'SBD', 'billable')",
                        (code.system, code.code),
                    ).fetchone()
                    if row is None:
                        raise ValueError("code is not an eligible local index row")
        except sqlite3.Error:
            raise ValueError("local terminology index unavailable") from None

    def dispatch(self, method: str, target: str, token: str, body: bytes = b"") -> tuple[int, dict]:
        if not hmac.compare_digest(token.encode("utf-8"), self.token.encode("utf-8")):
            return 403, {"error": "session token required"}
        url = urlsplit(target)
        query = parse_qs(url.query)
        if method == "GET" and url.path == "/api/session":
            return 200, {
                "annotator_id": self.assignment.annotator_id,
                "encounters": self.assignment.encounter_ids,
                "guidelines_sha256": self.assignment.guidelines_sha256,
                "fields": {
                    str(m.model_fields["kind"].annotation.__args__[0]): list(m.model_fields)
                    for m in ASSERTION_MODELS
                },
            }
        if method == "GET" and url.path == "/api/encounter":
            return 200, self.open_encounter(query.get("id", [""])[0])
        if method == "GET" and url.path == "/api/lookup":
            return 200, {
                "candidates": self.lookup(query.get("q", [""])[0], query.get("system", [""])[0])
            }
        if method == "POST" and url.path in ("/api/span", "/api/save", "/api/timing"):
            data = json.loads(body)
            if url.path == "/api/span":
                if not isinstance(data, dict) or set(data) != {"encounter_id", "selection"}:
                    raise ValueError("expected encounter_id and selection")
                self._current(data["encounter_id"])
                span = Selection.model_validate(data["selection"]).span(
                    self.transcripts[data["encounter_id"]]
                )
                return 200, span.model_dump(mode="json")
            if url.path == "/api/timing":
                event = TimingRequest.model_validate(data)
                self._current(event.encounter_id)
                if self.documents[event.encounter_id].status == "done":
                    return 200, {
                        "document": self.documents[event.encounter_id].model_dump(mode="json")
                    }
                clock = self.clocks[event.encounter_id]
                timing = clock.event(
                    time.monotonic(), focused=event.focused, idle_seconds=event.idle_seconds
                )
                doc = self.documents[event.encounter_id].model_copy(
                    update={"timing": timing, "updated_at": datetime.now(UTC)}
                )
            else:
                request = SaveRequest.model_validate(data)
                self._current(request.encounter_id)
                self._verify_codes(request.assertions)
                doc = self._snapshot(request.encounter_id)
                if doc.status == "done" and request.status == "in_progress":
                    self.clocks[request.encounter_id] = EncounterClock(doc.timing, time.monotonic())
                doc = AnnotationDocument.model_validate(
                    {**doc.model_dump(mode="json"), **request.model_dump(mode="json")}
                )
            self._write(doc)
            return 200, {"document": doc.model_dump(mode="json")}
        return 404, {"error": "unknown annotation route"}

    def close(self) -> None:
        if self.current is not None:
            self._write(self._snapshot(self.current))
