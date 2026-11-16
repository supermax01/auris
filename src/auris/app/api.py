"""The desktop app's JSON API. Every request carries the session token.

Review routes are passed to the existing review service (`ReviewSession`),
one per visit, after this API's own token check; the review service keeps
every rule it has (exact-version approval, re-checks, refusals).
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from collections import Counter
from collections.abc import Callable
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field

from auris.app.attention import annotate
from auris.app.jobs import NoteJobs
from auris.app.store import Visit, VisitState, VisitStore
from auris.review_web.service import ReviewSession
from auris.transcript import Transcript

VISIT = re.compile(r"^/api/visits/([0-9a-f]{32})(?:/([a-z-]+)(?:/([a-z-]+))?)?$")
REVIEW_READS = frozenset({"state", "outputs"})
REVIEW_WRITES = frozenset({"action", "fact", "speaker", "undo-edit", "recheck"})


class ClinicianIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)


class VisitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient: str = Field(min_length=1, max_length=120)
    reason: str = Field(default="", max_length=200)
    day: date | None = None
    time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class AppSession:
    """One running engine: the store, the note jobs, the token and the demo flag."""

    def __init__(
        self,
        store: VisitStore,
        *,
        jobs: NoteJobs | None = None,
        demo: bool = False,
        demo_transcript: Path | None = None,
        token: str | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.store = store
        self.jobs = jobs
        self.demo = demo
        self.demo_transcript = demo_transcript
        self.token = token or secrets.token_urlsafe(32)
        self._today = today
        self._reviews: dict[str, ReviewSession] = {}
        # Whether every record of a ready visit is decided, kept until the next review write.
        self._complete: dict[str, bool] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            for review in self._reviews.values():
                review.close()
            self._reviews.clear()

    # --- Dispatch --------------------------------------------------------

    def dispatch(self, method: str, path: str, token: str, body: bytes) -> tuple[int, dict]:
        if not secrets.compare_digest(token.encode(), self.token.encode()):
            return 403, {"error": "session token required"}
        url = urlsplit(path)
        query = parse_qs(url.query)
        if method == "GET" and url.path == "/api/session":
            return 200, {
                "clinician": self.store.clinician(),
                "today": self._today().isoformat(),
                "demo": self.demo,
            }
        if method == "POST" and url.path == "/api/clinician":
            data = ClinicianIn.model_validate(json.loads(body or b"{}"))
            return 200, {"clinician": self.store.set_clinician(data.name)}
        if method == "GET" and url.path == "/api/visits":
            day = date.fromisoformat(query.get("day", [self._today().isoformat()])[0])
            visits = self.store.visits_on(day)
            counts = Counter(visit.state.value for visit in visits)
            return 200, {
                "day": day.isoformat(),
                "visits": [
                    {**visit.to_api(), "review_complete": self._review_complete(visit)}
                    for visit in visits
                ],
                "counts": {state.value: counts.get(state.value, 0) for state in VisitState},
            }
        if method == "POST" and url.path == "/api/visits":
            data = VisitIn.model_validate(json.loads(body or b"{}"))
            visit = self.store.add_visit(
                data.patient, data.reason, data.day or self._today(), data.time
            )
            return 201, {"visit": visit.to_api()}
        match = VISIT.match(url.path)
        if match:
            return self._visit(method, match.group(1), match.group(2), match.group(3), body)
        return 404, {"error": "not found"}

    # --- One visit -------------------------------------------------------

    def _visit(
        self, method: str, visit_id: str, action: str | None, sub: str | None, body: bytes
    ) -> tuple[int, dict]:
        visit = self.store.get(visit_id)
        if visit is None:
            return 404, {"error": "visit not found"}
        if action is None and method == "GET":
            job = self.jobs.status(visit_id) if self.jobs else None
            return 200, {"visit": visit.to_api(), "job": job}
        if self.jobs is None:
            return 503, {"error": "note writing is not available in this engine"}
        if method == "POST" and action == "transcript":
            transcript = Transcript.model_validate_json(body)
            if not any(segment.text.strip() for segment in transcript.segments):
                raise ValueError("the transcript has no speech to write a note from")
            return 202, {"visit": visit.to_api(), "job": self.jobs.submit(visit_id, transcript)}
        if method == "POST" and action == "demo-transcript":
            if not (self.demo and visit.fictional and self.demo_transcript):
                return 403, {"error": "the fictional transcript is only for demo visits"}
            transcript = Transcript.load(self.demo_transcript)
            return 202, {"visit": visit.to_api(), "job": self.jobs.submit(visit_id, transcript)}
        if method == "POST" and action == "retry":
            if visit.state != VisitState.FAILED:
                return 422, {"error": "only a failed note can be written again"}
            transcript = Transcript.load(self.jobs.folder(visit_id) / "transcript.json")
            return 202, {"visit": visit.to_api(), "job": self.jobs.submit(visit_id, transcript)}
        if action == "review" and sub in REVIEW_READS | REVIEW_WRITES:
            return self._review(method, visit_id, visit.state, sub, body)
        if method == "POST" and action == "export":
            return self._export(visit_id, visit.state)
        if method == "POST" and action == "reopen":
            return 200, {"visit": self.store.move(visit_id, VisitState.READY).to_api()}
        return 404, {"error": "not found"}

    def _review_session(self, visit_id: str) -> ReviewSession | None:
        with self._lock:
            review = self._reviews.get(visit_id)
            if review is None:
                output = self.jobs.output(visit_id) if self.jobs else None
                clinician = self.store.clinician()
                if output is None or not clinician:
                    return None
                review = ReviewSession(output)
                payload = json.dumps({"name": clinician, "role": "clinician"}).encode()
                review.dispatch("POST", "/api/reviewer", review.token, payload)
                self._reviews[visit_id] = review
            return review

    def _review(
        self, method: str, visit_id: str, state: VisitState, route: str, body: bytes
    ) -> tuple[int, dict]:
        if state not in (VisitState.READY, VisitState.EXPORTED):
            return 409, {"error": "this visit has no note to review yet"}
        expected = "GET" if route in REVIEW_READS else "POST"
        if method != expected:
            return 405, {"error": "method not allowed"}
        if route in REVIEW_WRITES and state == VisitState.EXPORTED:
            return 409, {"error": "reopen the visit before changing it; its export is final"}
        review = self._review_session(visit_id)
        if review is None:
            return 409, {"error": "this visit's note is not available"}
        if route in REVIEW_WRITES:
            self._complete.pop(visit_id, None)
        status, result = review.dispatch(method, f"/api/{route}", review.token, body)
        if route == "state" and status == 200:
            self._complete[visit_id] = bool(result.get("ready"))
            result = annotate(result)
        return status, result

    def _review_complete(self, visit: Visit) -> bool:
        """True when a ready visit has every record decided (it can be exported)."""
        if visit.state != VisitState.READY:
            return visit.state == VisitState.EXPORTED
        if visit.id not in self._complete:
            review = self._review_session(visit.id)
            if review is None:
                return False
            _, state = review.dispatch("GET", "/api/state", review.token, b"")
            self._complete[visit.id] = bool(state.get("ready"))
        return self._complete[visit.id]

    def _export(self, visit_id: str, state: VisitState) -> tuple[int, dict]:
        if state != VisitState.READY:
            return 409, {"error": f"a visit that is {state} cannot be exported"}
        review = self._review_session(visit_id)
        if review is None:
            return 409, {"error": "this visit's note is not available"}
        _, review_state = review.dispatch("GET", "/api/state", review.token, b"")
        if not review_state.get("ready"):
            message = review_state.get("ready_message") or "every item needs a decision first"
            return 422, {"error": message}
        visit = self.store.move(visit_id, VisitState.EXPORTED)
        _, outputs = review.dispatch("GET", "/api/outputs", review.token, b"")
        return 200, {"visit": visit.to_api(), "outputs": outputs}
