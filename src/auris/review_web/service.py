"""Authenticated session dispatch and evidence context; no arbitrary file access."""

import secrets
import threading
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from auris.extract.quarantine import QuarantinedItem
from auris.fhir.models import MappingOutcome
from auris.review_edits import LOCKED_FIELDS, MODELS, CorrectionChecker, unchecked
from auris.review_state import ReviewEvent, ReviewRun
from auris.validation.report import apply_validation


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


class CorrectionTarget(Request):
    target_id: str = Field(min_length=1, max_length=256)
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FactCorrection(CorrectionTarget):
    fields: dict[str, Annotated[str, Field(max_length=4000)] | None] = Field(
        min_length=1, max_length=16
    )


class SpeakerCorrection(CorrectionTarget):
    speaker_role: Literal["clinician", "patient", "other"]


class ReviewSession:
    def __init__(self, directory: Path, *, checker: CorrectionChecker | None = None) -> None:
        self.run = ReviewRun(directory, checker=checker)
        self.token = secrets.token_urlsafe(32)
        self.reviewer: Reviewer | None = None
        self.lock = threading.RLock()
        self.worker: threading.Thread | None = None

    @property
    def busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def dispatch(self, method: str, path: str, token: str, body: bytes) -> tuple[int, dict]:
        with self.lock:
            return self._dispatch(method, path, token, body)

    def _dispatch(self, method: str, path: str, token: str, body: bytes) -> tuple[int, dict]:
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
            state["busy"] = self.busy
            state["edit_schema"] = {
                kind: [name for name in model.model_fields if name not in LOCKED_FIELDS]
                for kind, model in MODELS.items()
            }
            return 200, state
        if method == "POST" and path in (
            "/api/fact",
            "/api/speaker",
            "/api/undo-edit",
            "/api/recheck",
        ):
            if self.reviewer is None:
                return 403, {"error": "enter reviewer name and role first"}
            if self.busy:
                return 409, {"error": "a re-check is running; wait for it to finish"}
            if path == "/api/recheck":
                from auris.review_state import target_digest

                request = CorrectionTarget.model_validate_json(body)
                self.run.load()
                target = self.run.targets.get(request.target_id)
                if target is None or target_digest(target) != request.target_sha256:
                    raise ValueError("item version changed; reload before retrying checks")
                version = self.run.checks.get(request.target_id)
                if version is None or version.status == "complete":
                    raise ValueError("this item has no incomplete re-check")
                key = self.run.version_keys[request.target_id]
                validation = unchecked(self.run.recheck_resources((key,)), "re-checking")
                self.run.layer.versions[key] = version.model_copy(
                    update={
                        "status": "rechecking",
                        "validation": validation,
                        "outcome": apply_validation(version.outcome, validation),
                    }
                )
                self.run.layer.save(self.run.layer_path)
                keys = (key,)
            else:
                request_model = (
                    FactCorrection
                    if path == "/api/fact"
                    else SpeakerCorrection
                    if path == "/api/speaker"
                    else CorrectionTarget
                )
                request = request_model.model_validate_json(body)
                keys = self.run.prepare_correction(
                    **request.model_dump(),
                    reviewer=self.reviewer.name,
                    role=self.reviewer.role,
                    undo=path == "/api/undo-edit",
                )
            if keys:
                self.start_recheck(keys)
            return 202 if keys else 200, self.run.rewrite()
        if method == "POST" and path == "/api/action":
            if self.reviewer is None:
                return 403, {"error": "enter reviewer name and role first"}
            action = Action.model_validate_json(body)
            self.run.load()
            check = self.run.checks.get(action.target_id)
            if self.busy and check and check.status == "rechecking":
                return 409, {"error": "this item is re-checking; wait for it to finish"}
            return 200, self.run.act(
                **action.model_dump(), reviewer=self.reviewer.name, role=self.reviewer.role
            )
        if method == "GET" and path == "/api/outputs":
            self.run.load()
            self.run.rewrite()
            # Fixed generated documents only; no path/query/file-serving endpoint.
            return 200, {
                name: (self.run.directory / name).read_text("utf-8")
                for name in ("note.md", "bundle.json", "validation.json")
            }
        return 404, {"error": "unknown review route"}

    def context(self, target: MappingOutcome | QuarantinedItem) -> list[dict[str, Any]]:
        segments = self.run.transcript.segments
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

    def start_recheck(self, keys: tuple[str, ...]) -> None:
        resources = self.run.recheck_resources(keys)

        def work() -> None:
            try:
                result = self.run.checker.validate(
                    self.run.inputs.intake, self.run.directory, resources
                )
            except Exception:
                result = unchecked(resources, "validator could not finish the re-check")
            with self.lock:
                try:
                    self.run.finish_recheck(keys, result)
                except (OSError, ValueError):
                    # The durable pending projection remains blocked; reopening can retry.
                    pass

        self.worker = threading.Thread(target=work, name="auris-review-check", daemon=True)
        self.worker.start()

    def close(self) -> None:
        self.token = ""
