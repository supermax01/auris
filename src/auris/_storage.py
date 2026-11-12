"""Private writes for transcript and intake JSON (PLAN A8)."""

import os
import stat
import tempfile
from pathlib import Path


def write_private_text(path: str | Path, text: str) -> None:
    """Write UTF-8 at 0600, atomically, inside a 0700 workdir.

    Create the immediate workdir if absent. Refuse a shared directory
    rather than changing permissions on an operator's existing directory.
    """
    destination = Path(path)
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    if stat.S_IMODE(destination.parent.stat().st_mode) != 0o700:
        raise ValueError("output workdir must have permissions 0700 (PLAN A8)")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            handle.write(text)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
