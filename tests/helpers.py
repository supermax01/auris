"""Test-only helpers (never imported by production code)."""

import io
import json
import tarfile
from typing import Any


def make_fake_package_bytes(package_json: dict[str, Any], members: dict[str, bytes]) -> bytes:
    """Build a tiny package-shaped tarball in memory. Fictional content only."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tarball:
        payload = {"package/package.json": json.dumps(package_json).encode(), **members}
        for name, data in payload.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tarball.addfile(info, io.BytesIO(data))
    return buffer.getvalue()
