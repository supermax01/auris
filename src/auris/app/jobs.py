"""Writing the note: run the existing pipeline for a visit in the background.

Each visit gets a private folder under ``<data>/runs/<visit id>/`` holding its
transcript, intake and one output folder per attempt. A single worker runs one
note at a time (the 16 GB memory budget allows one model at once). The
pipeline itself is the unchanged `auris run` command in its own process, so a
model failure cannot take the app down and memory is returned when it ends.

Progress is read from the files the pipeline writes, in order:
``extraction.json`` (facts found), ``validation.json`` (records checked), then
``review-inputs.json`` and ``note.md`` (note drafted).
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from auris._storage import write_private_text
from auris.app.store import VisitState, VisitStore
from auris.app.visit_intake import build_intake
from auris.transcript import Transcript

# A runner takes (transcript, intake, output folder, log file) and returns an exit code.
Runner = Callable[[Path, Path, Path, Path], int]

STAGES: tuple[tuple[str, str], ...] = (
    ("transcript", "Transcript received"),
    ("facts", "Finding the facts"),
    ("checks", "Checking each fact and record"),
    ("note", "Drafting the visit note"),
)
INTERRUPTED = "Auris stopped while writing this note. Try again."


def subprocess_runner(cwd: Path) -> Runner:
    """The real pipeline: `python -m auris run` in its own process."""

    def run(transcript: Path, intake: Path, out: Path, log: Path) -> int:
        with log.open("ab") as handle:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "auris",
                    "run",
                    "--transcript",
                    str(transcript),
                    "--intake",
                    str(intake),
                    "--out",
                    str(out),
                ],
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=handle,
                check=False,
            )
        return completed.returncode

    return run


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _complete(out: Path) -> bool:
    return (out / "review-inputs.json").is_file() and (out / "note.md").is_file()


def _failure_message(log: Path) -> str:
    """The pipeline's own one-line error, never transcript text."""
    try:
        lines = log.read_text("utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for line in reversed(lines):
        for prefix in ("error: ", "extraction failed: "):
            if line.startswith(prefix):
                return line.removeprefix(prefix)[:300]
    return "the note could not be written; see the run log on this computer"


class NoteJobs:
    def __init__(self, data_dir: Path, store: VisitStore, runner: Runner) -> None:
        self.runs = data_dir / "runs"
        self.store = store
        self.runner = runner
        self.queue: queue.Queue[str] = queue.Queue()
        self.lock = threading.Lock()
        self.running: str | None = None
        self.worker = threading.Thread(target=self._work, name="auris-notes", daemon=True)

    def start(self) -> None:
        self.recover()
        self.worker.start()

    # --- Folders ---------------------------------------------------------

    def folder(self, visit_id: str) -> Path:
        if not visit_id.isalnum():
            raise ValueError("invalid visit id")
        return self.runs / visit_id

    def _status_path(self, visit_id: str) -> Path:
        return self.folder(visit_id) / "job.json"

    def _read_status(self, visit_id: str) -> dict | None:
        try:
            return json.loads(self._status_path(visit_id).read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def _write_status(self, visit_id: str, status: dict) -> None:
        write_private_text(self._status_path(visit_id), json.dumps(status, indent=2) + "\n")

    def output(self, visit_id: str) -> Path | None:
        """The finished output folder of the visit's latest attempt, if any."""
        status = self._read_status(visit_id)
        if not status or status.get("state") != "done":
            return None
        out = self.folder(visit_id) / status["attempt"]
        return out if _complete(out) else None

    # --- Submitting ------------------------------------------------------

    def submit(self, visit_id: str, transcript: Transcript) -> dict:
        """Save the transcript and intake, then queue the note for writing."""
        visit = self.store.get(visit_id)
        if visit is None:
            raise KeyError(visit_id)
        clinician = self.store.clinician()
        if not clinician:
            raise ValueError("enter your name before writing a note")
        if visit.state not in (VisitState.SCHEDULED, VisitState.LISTENING, VisitState.FAILED):
            raise ValueError(f"a note cannot be written for a visit that is {visit.state}")
        folder = self.folder(visit_id)
        self.runs.mkdir(mode=0o700, exist_ok=True)
        write_private_text(folder / "transcript.json", transcript.model_dump_json(indent=2) + "\n")
        build_intake(visit, clinician).save(folder / "intake.json")
        previous = self._read_status(visit_id)
        attempt = f"attempt-{(previous or {}).get('attempts', 0) + 1}"
        status = {
            "state": "queued",
            "attempt": attempt,
            "attempts": (previous or {}).get("attempts", 0) + 1,
            "queued_at": _stamp(),
            "error": None,
        }
        self._write_status(visit_id, status)
        self.store.move(visit_id, VisitState.WRITING)
        self.queue.put(visit_id)
        return self.status(visit_id)

    # --- Status ----------------------------------------------------------

    def status(self, visit_id: str) -> dict | None:
        status = self._read_status(visit_id)
        if status is None:
            return None
        out = self.folder(visit_id) / status["attempt"]
        done = {
            "transcript": True,
            "facts": (out / "extraction.json").is_file(),
            "checks": (out / "validation.json").is_file(),
            "note": _complete(out),
        }
        busy_found = False
        stages = []
        for key, label in STAGES:
            if done[key]:
                state = "done"
            elif status["state"] == "running" and not busy_found:
                state, busy_found = "busy", True
            else:
                state = "waiting"
            stages.append({"key": key, "label": label, "state": state})
        return {
            "state": status["state"],
            "error": status.get("error"),
            "stages": stages,
            "attempt": status["attempt"],
            "position": self._position(visit_id) if status["state"] == "queued" else None,
        }

    def _position(self, visit_id: str) -> int:
        with self.queue.mutex:
            waiting = list(self.queue.queue)
        ahead = (1 if self.running else 0) + (waiting.index(visit_id) if visit_id in waiting else 0)
        return ahead

    # --- Working ---------------------------------------------------------

    def recover(self) -> None:
        """A note that was being written when the app closed is finished or failed."""
        for visit in self.store.in_state(VisitState.WRITING):
            status = self._read_status(visit.id) or {}
            out = self.folder(visit.id) / status.get("attempt", "missing")
            if _complete(out):
                self._write_status(visit.id, {**status, "state": "done", "error": None})
                self.store.move(visit.id, VisitState.READY)
            else:
                self._write_status(visit.id, {**status, "state": "failed", "error": INTERRUPTED})
                self.store.move(visit.id, VisitState.FAILED, failure=INTERRUPTED)

    def _work(self) -> None:
        while True:
            visit_id = self.queue.get()
            try:
                self.run_one(visit_id)
            finally:
                self.queue.task_done()

    def run_one(self, visit_id: str) -> None:
        folder = self.folder(visit_id)
        status = self._read_status(visit_id) or {}
        out = folder / status.get("attempt", "attempt-1")
        log = folder / "job.log"
        with self.lock:
            self.running = visit_id
        self._write_status(visit_id, {**status, "state": "running", "started_at": _stamp()})
        try:
            code = self.runner(folder / "transcript.json", folder / "intake.json", out, log)
        except Exception:  # The runner must never take the worker down.
            code = -1
        finally:
            with self.lock:
                self.running = None
        finished = {**status, "finished_at": _stamp()}
        if code == 0 and _complete(out):
            self._write_status(visit_id, {**finished, "state": "done", "error": None})
            self.store.move(visit_id, VisitState.READY)
        else:
            error = _failure_message(log)
            self._write_status(visit_id, {**finished, "state": "failed", "error": error})
            self.store.move(visit_id, VisitState.FAILED, failure=error)
