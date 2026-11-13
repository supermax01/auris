"""Third-clinician tasks and append-only decision histories, without model output."""

import hmac
import json
import secrets
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, model_validator

from auris._storage import write_private_text
from auris.annotation.comparison import align, differences, validate_pair
from auris.annotation.models import (
    AnnotationDocument,
    AnnotationFact,
    Assignment,
    ImportantFact,
    Selection,
    Timing,
)
from auris.annotation.service import AnnotationSession, TimingRequest, private_directory
from auris.annotation.timing import EncounterClock
from auris.eval.gold import Identifier, Record, canonical_json
from auris.transcript import SpeakerRole, Transcript

type Answer = AnnotationFact | ImportantFact | SpeakerRole | None


class AdjudicationAssignment(Record):
    schema_version: Literal["adjudication-assignment-1"] = "adjudication-assignment-1"
    assignment: Assignment
    adjudicator_id: Identifier
    clinically_trained: Literal[True]
    a: dict[str, AnnotationDocument]
    b: dict[str, AnnotationDocument]
    tasks: dict[str, tuple["Task", ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def independent(self) -> Self:
        validate_pair(self.a, self.b, self.assignment)
        if self.adjudicator_id in {d.annotator_id for d in (*self.a.values(), *self.b.values())}:
            raise ValueError("adjudicator must be a third independent clinically trained person")
        expected = {key: build_tasks(self.a[key], self.b[key])[0] for key in self.a}
        if self.tasks and self.tasks != expected:
            raise ValueError("adjudication task list differs from independent answers")
        object.__setattr__(self, "tasks", expected)
        return self


class Task(Record):
    id: Identifier
    category: Literal["fact", "outside", "role"]
    attributes: tuple[str, ...]
    a: Answer
    b: Answer
    segment_id: str | None = None


class DecisionRequest(Record):
    encounter_id: Identifier
    task_id: Identifier
    choice: Literal["a", "b", "correct"]
    reason: str = Field(min_length=1)
    # Null is an explicit dismissal, including for an unmatched fact.
    corrected: Answer = None

    @model_validator(mode="after")
    def reason_required(self) -> Self:
        if not self.reason.strip():
            raise ValueError("every adjudication decision requires a reason")
        if self.choice != "correct" and self.corrected is not None:
            raise ValueError("accept A/B cannot supply a correction")
        return self


class Decision(Record):
    sequence: int = Field(ge=1)
    task_id: Identifier
    who: Identifier
    when: datetime
    choice: Literal["a", "b", "correct"]
    reason: str = Field(min_length=1)
    before: Task
    after: Answer


class AdjudicationRecord(Record):
    schema_version: Literal["adjudication-1"] = "adjudication-1"
    encounter_id: Identifier
    assignment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    adjudicator_id: Identifier
    status: Literal["in_progress", "adjudicated"] = "in_progress"
    completed_at: datetime | None = None
    timing: Timing = Field(default_factory=Timing)
    decisions: tuple[Decision, ...] = ()


def assignment_hash(assignment: AdjudicationAssignment) -> str:
    from auris.eval.gold import digest

    return digest(canonical_json(assignment.model_dump(mode="json")).encode())


def build_tasks(
    a: AnnotationDocument,
    b: AnnotationDocument,
) -> tuple[
    tuple[Task, ...], tuple[AnnotationFact, ...], tuple[ImportantFact, ...], dict[str, SpeakerRole]
]:
    """Each changed pair lists every differing attribute side by side."""
    tasks: list[Task] = []
    agreed: list[AnnotationFact] = []
    outside: list[ImportantFact] = []

    def task(
        category: Literal["fact", "outside", "role"],
        left: Answer,
        right: Answer,
        attrs: tuple[str, ...],
        segment_id: str | None = None,
    ) -> None:
        tasks.append(
            Task(
                id=f"task_{len(tasks) + 1:04d}",
                category=category,
                attributes=attrs,
                a=left,
                b=right,
                segment_id=segment_id,
            )
        )

    matches = align(a.assertions, b.assertions)
    for i, j in matches:
        left, right = a.assertions[i], b.assertions[j]
        changed = differences(left, right)
        if changed:
            task("fact", left, right, changed)
        else:
            agreed.append(left)
    for i, fact in enumerate(a.assertions):
        if i not in {i for i, _ in matches}:
            task("fact", fact, None, ("unmatched",))
    for j, fact in enumerate(b.assertions):
        if j not in {j for _, j in matches}:
            task("fact", None, fact, ("unmatched",))
    # Important facts lack concept keys: exact content carries; overlapping
    # evidence pairs candidates for explicit adjudication, never automatic merge.
    remaining = list(b.out_of_scope)
    from auris.eval.matching import overlap

    for left in a.out_of_scope:
        exact = next(
            (
                f
                for f in remaining
                if f.model_dump(exclude={"id"}) == left.model_dump(exclude={"id"})
            ),
            None,
        )
        if exact is not None:
            outside.append(left)
            remaining.remove(exact)
            continue
        right = next((f for f in remaining if overlap(left.evidence, f.evidence)), None)
        if right is not None:
            remaining.remove(right)
        right_fields = right.model_dump() if right is not None else {}
        changed = tuple(
            k
            for k, v in left.model_dump().items()
            if k != "id" and (right is None or v != right_fields[k])
        )
        task("outside", left, right, changed if right else ("unmatched",))
    for right in remaining:
        task("outside", None, right, ("unmatched",))
    roles = {}
    for key in sorted(a.segment_roles.keys() | b.segment_roles.keys()):
        left, right = a.segment_roles.get(key), b.segment_roles.get(key)
        if left == right and left is not None:
            roles[key] = left
        else:
            task("role", left, right, ("speaker_role",), key)
    return tuple(tasks), tuple(agreed), tuple(outside), roles


def validate_after(task: Task, after: object) -> None:
    if task.category == "role":
        if not isinstance(after, SpeakerRole):
            raise ValueError("a role decision must select a speaker role")
    elif after is not None:
        expected = AnnotationFact if task.category == "fact" else ImportantFact
        if not isinstance(after, expected):
            raise ValueError("correction must have the task's fact type")


def resolved(
    assignment: AdjudicationAssignment,
    record: AdjudicationRecord,
) -> tuple[
    tuple[AnnotationFact, ...], tuple[ImportantFact, ...], dict[str, SpeakerRole], tuple[Task, ...]
]:
    """Replay and verify the entire history, including server-owned before/after."""
    if (
        record.assignment_sha256 != assignment_hash(assignment)
        or record.adjudicator_id != assignment.adjudicator_id
    ):
        raise ValueError("adjudication assignment or identity mismatch")
    tasks, facts, outside, roles = build_tasks(
        assignment.a[record.encounter_id], assignment.b[record.encounter_id]
    )
    by_id = {t.id: t for t in tasks}
    latest = {}
    previous_time = None
    for i, decision in enumerate(record.decisions, 1):
        if (
            decision.sequence != i
            or decision.who != assignment.adjudicator_id
            or decision.task_id not in by_id
            or decision.before != by_id[decision.task_id]
            or decision.when.tzinfo is None
            or not decision.reason.strip()
            or (previous_time is not None and decision.when < previous_time)
        ):
            raise ValueError("invalid adjudication audit history")
        previous_time = decision.when
        task = by_id[decision.task_id]
        if decision.choice != "correct" and decision.after != getattr(task, decision.choice):
            raise ValueError("audit accept decision disagrees with original answer")
        validate_after(task, decision.after)
        latest[task.id] = decision.after
    final_facts, final_outside = list(facts), list(outside)
    for task in tasks:
        if task.id not in latest:
            continue
        after = latest[task.id]
        if task.category == "role":
            roles[task.segment_id] = after
        elif after is not None:
            (final_facts if task.category == "fact" else final_outside).append(after)
    # Independent local ids can collide. Stable output ids do not alter audit originals.
    final_facts = [
        f.model_copy(update={"id": f"gold_{i:04d}"}) for i, f in enumerate(final_facts, 1)
    ]
    final_outside = [
        f.model_copy(update={"id": f"outside_{i:04d}"}) for i, f in enumerate(final_outside, 1)
    ]
    pending = tuple(t for t in tasks if t.id not in latest)
    if record.status == "adjudicated" and pending:
        raise ValueError("adjudicated encounter still has unresolved decisions")
    if record.status == "adjudicated" and (
        record.completed_at is None
        or record.completed_at.tzinfo is None
        or (previous_time is not None and record.completed_at < previous_time)
    ):
        raise ValueError("adjudication completion requires a valid UTC timestamp")
    return tuple(final_facts), tuple(final_outside), roles, pending


def verify_record(
    assignment: AdjudicationAssignment,
    record: AdjudicationRecord,
    transcript: Transcript,
) -> None:
    facts, outside, roles, _ = resolved(assignment, record)
    assignment.a[record.encounter_id].verify(transcript)
    assignment.b[record.encounter_id].verify(transcript)
    # Recheck historical corrections as well as the latest answer.
    for decision in record.decisions:
        if isinstance(decision.after, (AnnotationFact, ImportantFact)):
            for span in decision.after.evidence:
                span.verify(transcript)
    AnnotationDocument.model_validate(
        {
            **assignment.a[record.encounter_id].model_dump(mode="json"),
            "assertions": facts,
            "out_of_scope": outside,
            "segment_roles": roles,
            "status": "done" if record.status == "adjudicated" else "in_progress",
        }
    ).verify(transcript)


class AdjudicationSession:
    """Same loopback/token server and span/lookup code; separate adjudication routes."""

    def __init__(
        self,
        assignment: AdjudicationAssignment,
        *,
        workdir: Path,
        cache: Path,
        manifests: Path,
        guidelines: bytes,
        locked_run: bool = False,
        confirmed: bool = False,
    ) -> None:
        self.adjudication = assignment
        private_directory(workdir)
        human = assignment.assignment.model_copy(update={"annotator_id": assignment.adjudicator_id})
        # Reuse the frozen-membership, guideline and test-ledger checks before
        # opening any transcript. This private helper never serves blind answers.
        self.tool = AnnotationSession(
            human,
            workdir=workdir / "tool",
            cache=cache,
            manifests=manifests,
            guidelines=guidelines,
            locked_run=locked_run,
            confirmed=confirmed,
        )
        self.assignment = human
        self.transcripts = self.tool.transcripts
        self.token = secrets.token_urlsafe(32)
        self.folder = workdir / "adjudications"
        private_directory(self.folder)
        self.records: dict[str, AdjudicationRecord] = {}
        self.clocks: dict[str, EncounterClock] = {}
        self.current: str | None = None
        for key, transcript in self.transcripts.items():
            assignment.a[key].verify(transcript)
            assignment.b[key].verify(transcript)
            path = self._path(key)
            if path.exists():
                if stat.S_IMODE(path.stat().st_mode) != 0o600:
                    raise ValueError("adjudication record must have permissions 0600")
                record = AdjudicationRecord.model_validate_json(path.read_bytes())
                if record.encounter_id != key:
                    raise ValueError("adjudication encounter identity mismatch")
                self._verify(record)
                self.records[key] = record

    def _path(self, key: str) -> Path:
        if key not in self.transcripts:
            raise ValueError("encounter is not assigned")
        path = self.folder / f"{key}.adjudication.json"
        if path.is_symlink():
            raise ValueError("adjudication file cannot be a symlink")
        return path

    def _verify(self, record: AdjudicationRecord) -> None:
        verify_record(self.adjudication, record, self.transcripts[record.encounter_id])
        facts, _, _, _ = resolved(self.adjudication, record)
        self.tool._verify_codes(facts)

    def _write(self, record: AdjudicationRecord) -> None:
        self._verify(record)
        write_private_text(
            self._path(record.encounter_id), canonical_json(record.model_dump(mode="json"))
        )
        self.records[record.encounter_id] = record

    def _snapshot(self, key: str) -> AdjudicationRecord:
        record = self.records[key]
        if record.status == "adjudicated":
            return record
        clock = self.clocks[key]
        now = time.monotonic()
        return record.model_copy(
            update={
                "timing": clock.event(
                    now, focused=clock.focused, idle_seconds=max(0, now - clock.activity_at)
                )
            }
        )

    def _payload(self, key: str) -> dict:
        record = self.records[key]
        facts, outside, roles, pending = resolved(self.adjudication, record)
        original = self.adjudication.a[key]
        return {
            "document": {
                **original.model_dump(mode="json"),
                "annotator_id": self.assignment.annotator_id,
                "status": "done" if record.status == "adjudicated" else "in_progress",
                "timing": record.timing.model_dump(mode="json"),
                "assertions": [f.model_dump(mode="json") for f in facts],
                "out_of_scope": [f.model_dump(mode="json") for f in outside],
                "segment_roles": {k: str(v) for k, v in roles.items()},
            },
            "tasks": [
                t.model_dump(mode="json")
                for t in build_tasks(self.adjudication.a[key], self.adjudication.b[key])[0]
            ],
            "pending_task_ids": [t.id for t in pending],
            "audit": [d.model_dump(mode="json") for d in record.decisions],
        }

    def dispatch(self, method: str, target: str, token: str, body: bytes = b"") -> tuple[int, dict]:
        if not hmac.compare_digest(token.encode(), self.token.encode()):
            return 403, {"error": "session token required"}
        url = urlsplit(target)
        if method == "GET" and url.path == "/api/session":
            _, result = self.tool.dispatch("GET", "/api/session", self.tool.token)
            return 200, {**result, "mode": "adjudication"}
        if method == "GET" and url.path == "/api/lookup":
            return self.tool.dispatch(method, target, self.tool.token)
        if method == "GET" and url.path == "/api/encounter":
            key = parse_qs(url.query).get("id", [""])[0]
            self._path(key)
            if self.current is not None and self.current != key:
                self._write(self._snapshot(self.current))
                self.clocks.pop(self.current, None)
            if key not in self.records:
                self.records[key] = AdjudicationRecord(
                    encounter_id=key,
                    assignment_sha256=assignment_hash(self.adjudication),
                    adjudicator_id=self.assignment.annotator_id,
                )
            if key not in self.clocks:
                self.clocks[key] = EncounterClock(self.records[key].timing, time.monotonic())
            self.current = key
            self._write(self._snapshot(key))
            turns = [
                {
                    "id": s.id,
                    "text": s.text,
                    "role": s.role.value,
                    "turn_number": s.turn_number or i,
                    "start_seconds": s.start_seconds,
                    "end_seconds": s.end_seconds,
                }
                for i, s in enumerate(self.transcripts[key].segments, 1)
            ]
            return 200, {**self._payload(key), "turns": turns}
        if method != "POST" or url.path not in (
            "/api/span",
            "/api/timing",
            "/api/decision",
            "/api/complete",
        ):
            return 404, {"error": "unknown adjudication route"}
        data = json.loads(body)
        key = data.get("encounter_id")
        if key != self.current:
            raise ValueError("open the assigned encounter first")
        if url.path == "/api/span":
            if set(data) != {"encounter_id", "selection"}:
                raise ValueError("expected encounter_id and selection")
            return 200, Selection.model_validate(data["selection"]).span(
                self.transcripts[key]
            ).model_dump(mode="json")
        record = self._snapshot(key)
        if url.path == "/api/timing":
            event = TimingRequest.model_validate(data)
            if record.status != "adjudicated":
                record = record.model_copy(
                    update={
                        "timing": self.clocks[key].event(
                            time.monotonic(), focused=event.focused, idle_seconds=event.idle_seconds
                        )
                    }
                )
        elif url.path == "/api/complete":
            if set(data) != {"encounter_id"}:
                raise ValueError("expected encounter_id only")
            record = record.model_copy(
                update={"status": "adjudicated", "completed_at": datetime.now(UTC)}
            )
        else:
            request = DecisionRequest.model_validate(data)
            tasks = {
                t.id: t for t in build_tasks(self.adjudication.a[key], self.adjudication.b[key])[0]
            }
            if request.task_id not in tasks:
                raise ValueError("unknown adjudication task")
            task = tasks[request.task_id]
            after = (
                request.corrected if request.choice == "correct" else getattr(task, request.choice)
            )
            validate_after(task, after)
            if record.status == "adjudicated":
                self.clocks[key] = EncounterClock(record.timing, time.monotonic())
            decision = Decision(
                sequence=len(record.decisions) + 1,
                task_id=task.id,
                who=self.assignment.annotator_id,
                when=datetime.now(UTC),
                choice=request.choice,
                reason=request.reason.strip(),
                before=task,
                after=after,
            )
            record = record.model_copy(
                update={
                    "status": "in_progress",
                    "completed_at": None,
                    "decisions": (*record.decisions, decision),
                }
            )
        self._write(record)
        return 200, self._payload(key)

    def close(self) -> None:
        if self.current is not None:
            self._write(self._snapshot(self.current))
