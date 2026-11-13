"""Authenticated session dispatch and evidence context; no arbitrary file access."""

import secrets
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from auris.extract.quarantine import QuarantinedItem
from auris.fhir.models import MappingOutcome
from auris.review_state import ReviewEvent, ReviewRun


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Reviewer(Request):
    name: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=200)


class Action(Request):
    target_id: str
    target_sha256: str
    action: Literal["approve", "dismiss", "undo"]
    reason: str = Field(default="", max_length=4000)


class ReviewSession:
    def __init__(self, directory: Path) -> None:
        self.run = ReviewRun(directory)
        self.token = secrets.token_urlsafe(32)
        self.reviewer: Reviewer | None = None

    def dispatch(self, method: str, path: str, token: str, body: bytes) -> tuple[int, dict]:
        if not secrets.compare_digest(token, self.token):
            return 403, {"error": "session token required"}
        if method == "POST" and path == "/api/reviewer":
            reviewer = Reviewer.model_validate_json(body)
            # Use the same nonblank attribution checks as durable receipts.
            ReviewEvent.nonblank(reviewer.name)
            ReviewEvent.nonblank(reviewer.role)
            if self.reviewer is not None and self.reviewer != reviewer:
                raise ValueError("reviewer already set for this session; restart to change it")
            self.reviewer = reviewer
            return 200, {"reviewer": reviewer.model_dump()}
        if method == "GET" and path == "/api/state":
            self.run.load()
            state = self.run.rewrite()
            state["reviewer"] = self.reviewer.model_dump() if self.reviewer else None
            state["evidence"] = {t.id: self.context(t) for t in self.run.targets.values()}
            state["quarantines"] = [
                {**item.review_item(), "errors": [e.model_dump() for e in item.errors]}
                for item in self.run.quarantines
            ]
            state["fictional"] = self.run.inputs.note_metadata.fictional_demo
            return 200, state
        if method == "POST" and path == "/api/action":
            if self.reviewer is None:
                return 403, {"error": "enter reviewer name and role first"}
            action = Action.model_validate_json(body)
            return 200, self.run.act(
                **action.model_dump(), reviewer=self.reviewer.name, role=self.reviewer.role
            )
        if method == "GET" and path == "/api/outputs":
            # Fixed generated documents only; no path/query/file-serving endpoint.
            return 200, {
                name: (self.run.directory / name).read_text("utf-8")
                for name in ("note.md", "bundle.json", "validation.json")
            }
        return 404, {"error": "unknown review route"}

    def context(self, target: MappingOutcome | QuarantinedItem) -> list[dict[str, Any]]:
        segments = self.run.inputs.transcript.segments
        if isinstance(target, MappingOutcome):
            evidence = [item.model_dump(mode="json") for item in target.evidence.items]
        else:
            raw = target.raw_item
            evidence = raw.get("evidence", []) if isinstance(raw, dict) else []
            if not isinstance(evidence, list):
                evidence = []
        results = []
        for item in evidence:
            if not isinstance(item, dict) or not isinstance(item.get("quote"), str):
                continue
            index = next(
                (i for i, s in enumerate(segments) if s.id == item.get("segment_id")), None
            )
            neighbors = []
            if index is not None:
                for segment in segments[max(0, index - 1) : index + 2]:
                    seconds = segment.start_seconds
                    receipt = (
                        f"turn {segment.turn_number}"
                        if seconds is None
                        else f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"
                    )
                    neighbors.append(
                        {
                            "id": segment.id,
                            "text": segment.text,
                            "role": segment.role.value,
                            "receipt": receipt,
                            "cited": segment.id == item.get("segment_id"),
                        }
                    )
            aligned = item.get("aligned") or {}
            cited = segments[index] if index is not None else None
            seconds = aligned.get("audio_start_seconds")
            if seconds is None and cited:
                seconds = cited.start_seconds
            stamp = (
                f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"
                if seconds is not None
                else f"turn {cited.turn_number}"
                if cited
                else "turn unknown"
            )
            results.append(
                {
                    "quote": item["quote"],
                    "segment_id": item.get("segment_id"),
                    "neighbors": neighbors,
                    "alignment": aligned.get("alignment_method"),
                    "failure": item.get("failure"),
                    "audio_start_seconds": aligned.get("audio_start_seconds"),
                    "turn_number": aligned.get("turn_number"),
                    "receipt": f"[{stamp} · {cited.role.value if cited else 'unknown'}]",
                }
            )
        return results

    def close(self) -> None:
        self.token = ""  # No resources or background work survive the server.
