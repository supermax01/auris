"""Append-only test-use audit, shared across output directories, POSIX locked."""

import fcntl
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from auris.datasets.splits import PublicSplit
from auris.eval.gold import Dataset


def register_test_use(
    path: Path,
    dataset: Dataset | PublicSplit,
    run_id: str,
    action: Literal["run", "score"],
    *,
    confirmed: bool = False,
    locked_run: bool = False,
) -> dict:
    if dataset.split != "test":
        return {"locked_test": False, "confirmed_rerun": False}
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if stat.S_IMODE(path.parent.stat().st_mode) != 0o700:
        raise ValueError("test ledger directory must have permissions 0700")
    if path.exists() and stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("test ledger must have permissions 0600")
    if path.is_symlink():
        raise ValueError("test ledger cannot be a symlink")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        entries = [json.loads(line) for line in handle if line.strip()]
        hashes = sorted(e.transcript_sha256 for e in dataset.encounters)
        related = [
            e
            for e in entries
            if e["action"] in ("run", "score")
            and (e["split_id"] == dataset.split_id or set(e["transcript_hashes"]) & set(hashes))
        ]
        own = [e for e in related if e["run_id"] == run_id]
        if action == "score" and not any(e["action"] == "run" for e in own):
            raise ValueError("locked test run has no reservation in the test ledger")
        repeated = bool(related) if action == "run" else any(e["action"] == "score" for e in own)
        refusal = None
        if action == "run" and not (locked_run or confirmed):
            refusal = "locked test extraction requires --locked-test-run"
        if repeated and not confirmed:
            refusal = "locked test already used; explicit --confirm-locked-test-rerun required"
        entry = {
            "schema_version": "1",
            "split_id": dataset.split_id,
            "dataset_id": dataset.id,
            "transcript_hashes": hashes,
            "run_id": run_id,
            "action": f"{action}_refused" if refusal else action,
            "locked_run": locked_run,
            "refusal": refusal,
            "confirmed_rerun": confirmed,
            "repeated": repeated,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        if refusal:
            raise ValueError(refusal)
    return {"locked_test": True, "confirmed_rerun": confirmed, "repeated": repeated}
