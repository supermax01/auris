"""Explicit setup-only download and verification of a frozen local GGUF."""

import os
import shutil
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

from auris.config import ExtractorCandidate
from auris.setup.fhir_packages import sha256_file


def _fetch(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "auris-setup"})
    with urllib.request.urlopen(request, timeout=180) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def verify_model(candidate: ExtractorCandidate, path: Path) -> None:
    if candidate.bytes is not None and path.stat().st_size != candidate.bytes:
        raise ValueError("extractor model size mismatch; refusing model")
    if sha256_file(path) != candidate.sha256:
        raise ValueError("extractor model SHA-256 mismatch; refusing model")


def setup_model(
    candidate: ExtractorCandidate,
    cache: Path,
    *,
    fetch: bool = False,
    fetcher: Callable[[str, Path], None] | None = None,
) -> Path:
    destination = cache / candidate.gguf_filename
    if destination.exists():
        verify_model(candidate, destination)
        return destination
    url = f"https://huggingface.co/{candidate.repo}/resolve/{candidate.revision}/{candidate.gguf_filename}"
    if not fetch:
        raise ValueError(
            "pinned extractor model missing; run auris setup model "
            f"--model {candidate.id} --fetch\n"
            f"Source: {url}"
        )
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=cache, suffix=".download")
    os.close(fd)
    temporary = Path(name)
    try:
        (fetcher or _fetch)(url, temporary)
        verify_model(candidate, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
