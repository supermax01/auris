"""Verify pinned terminology archives and rebuild the local index (setup only)."""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

from auris.config import TerminologyConfig, TerminologyPin
from auris.terminology.index import build_index

Fetcher = Callable[[str, Path], None]


def default_cache_dir() -> Path:
    return Path(os.environ.get("AURIS_CACHE", "cache")) / "terminology"


def _fetch(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "auris-setup"})
    with urllib.request.urlopen(request, timeout=180) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def verify_archive(path: Path, pin: TerminologyPin) -> None:
    sha256, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha256.update(chunk)
            md5.update(chunk)
    if sha256.hexdigest() != pin.sha256:
        raise ValueError(f"SHA-256 mismatch for {pin.filename}; refusing archive")
    if pin.md5 and md5.hexdigest() != pin.md5:
        raise ValueError(f"published MD5 mismatch for {pin.filename}; refusing archive")


def ensure_archive(
    pin: TerminologyPin,
    cache: Path,
    *,
    fetch: bool = False,
    fetcher: Fetcher | None = None,
) -> Path:
    destination = cache / pin.filename
    if destination.exists():
        verify_archive(destination, pin)
        return destination
    if not fetch:
        raise ValueError(
            f"missing {pin.filename}\nSource: {pin.url}\n"
            "Fetch outside the sandbox: auris setup terminology --fetch --cache "
            f"{shlex.quote(str(cache))}"
        )
    cache.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=cache, suffix=".download")
    os.close(fd)
    temporary = Path(name)
    try:
        (fetcher or _fetch)(pin.url, temporary)
        verify_archive(temporary, pin)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def setup_terminology(
    config: TerminologyConfig,
    cache: Path,
    *,
    fetch: bool = False,
    fetcher: Fetcher | None = None,
) -> Path:
    paths: list[Path] = []
    problems: list[str] = []
    for pin in (config.rxnorm, config.icd10cm):
        try:
            paths.append(ensure_archive(pin, cache, fetch=fetch, fetcher=fetcher))
        except ValueError as exc:
            problems.append(str(exc))
    if problems:
        raise ValueError("\n".join(problems))
    metadata = {
        "rxnorm": config.rxnorm.model_dump(mode="json"),
        "icd10cm": config.icd10cm.model_dump(mode="json"),
        "icd10cm_valid_from": str(config.icd10cm.valid_from),
        "icd10cm_valid_to": str(config.icd10cm.valid_to),
    }
    return build_index(*paths, cache / "index.sqlite", metadata=metadata)
