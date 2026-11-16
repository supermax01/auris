"""Setup-only download of the local FHIR test server.

HAPI FHIR's official image keeps the server in one layer as ``/app/main.war``.
The registry serves layers by SHA-256 digest, so the layer is fetched by its
pinned digest, checked, and only the WAR is kept (checked against its own
pin). Docker is not needed; Java 21 (already required by the validator) runs it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

from auris.config import FhirServerConfig
from auris.setup.fhir_packages import sha256_file

REGISTRY = "https://registry-1.docker.io/v2"
AUTH = "https://auth.docker.io/token?service=registry.docker.io&scope=repository:{image}:pull"
BlobFetcher = Callable[[FhirServerConfig, Path], None]


def default_cache_dir() -> Path:
    from auris.cli import _repo_root

    return Path(os.environ.get("AURIS_CACHE", _repo_root() / "cache")) / "fhir-server"


def war_path(pin: FhirServerConfig, cache: Path) -> Path:
    return cache / f"{pin.image.replace('/', '--')}--{pin.tag}" / "main.war"


def _open(url: str, token: str | None = None):
    headers = {"User-Agent": "auris-setup"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=300)


def fetch_layer(pin: FhirServerConfig, destination: Path) -> None:
    """Download the pinned layer; the registry addresses it by content digest."""
    with _open(AUTH.format(image=pin.image)) as response:
        token = json.load(response)["token"]
    with _open(f"{REGISTRY}/{pin.image}/blobs/{pin.layer_digest}", token) as response:
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output)


def verify_war(path: Path, pin: FhirServerConfig) -> None:
    if path.stat().st_size != pin.war_bytes or sha256_file(path) != pin.war_sha256:
        raise ValueError("FHIR test server file does not match its pin; refusing it")


def setup_fhir_server(
    pin: FhirServerConfig,
    cache: Path,
    *,
    fetch: bool = False,
    fetcher: BlobFetcher = fetch_layer,
) -> Path:
    destination = war_path(pin, cache)
    if destination.exists():
        verify_war(destination, pin)
        return destination
    if not fetch:
        raise ValueError(
            "local FHIR test server missing; run auris setup fhir-server --fetch\n"
            f"Source: {pin.image}:{pin.tag} layer {pin.layer_digest}"
        )
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as scratch:
        layer = Path(scratch) / "layer.tar.gz"
        fetcher(pin, layer)
        if "sha256:" + sha256_file(layer) != pin.layer_digest:
            raise ValueError("FHIR test server layer does not match its digest; refusing it")
        staged = Path(scratch) / "main.war"
        with tarfile.open(layer, "r:gz") as archive:
            member = archive.getmember(pin.member)
            if not member.isfile():
                raise ValueError("FHIR test server layer has no server file")
            source = archive.extractfile(member)
            assert source is not None
            with source, staged.open("wb") as output:
                shutil.copyfileobj(source, output)
        verify_war(staged, pin)
        staged.replace(destination)
    return destination


def digest_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()
