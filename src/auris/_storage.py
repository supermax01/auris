"""Private writes for transcript and intake JSON (PLAN A8)."""

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any


def append_private_jsonl(path: Path, entry: dict[str, Any]) -> None:
    """Append one durable attributed event, using the quarantine log protections."""
    path.parent.mkdir(mode=0o700, exist_ok=True)
    if path.parent.is_symlink() or stat.S_IMODE(path.parent.stat().st_mode) != 0o700:
        raise ValueError("review workdir must have permissions 0700 and not be a symlink")
    payload = (json.dumps(entry, allow_nan=False) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("review log must be a regular file with permissions 0600")
        # One append write per entry. A short write is an error, never a valid receipt.
        if os.write(descriptor, payload) != len(payload):
            raise OSError("incomplete review log append")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def prepare_output_directory(path: Path) -> None:
    """Create missing output parents privately; reject reuse before expensive work."""
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("output workdir must be a directory, not a symlink")
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise ValueError("output workdir must have permissions 0700 (PLAN A8)")
    if any(path.iterdir()):
        raise ValueError("output directory must be empty for a new extraction run")


def write_private_text(path: str | Path, text: str) -> None:
    """Write UTF-8 at 0600, atomically, inside a 0700 workdir.

    Create the immediate workdir if absent. Refuse a shared directory
    rather than changing permissions on an operator's existing directory.
    """
    destination = Path(path)
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    if destination.parent.is_symlink() or stat.S_IMODE(destination.parent.stat().st_mode) != 0o700:
        raise ValueError("output workdir must have permissions 0700 (PLAN A8)")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
