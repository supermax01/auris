"""Verify the official validator and seed its cache from pinned tarballs only."""

from __future__ import annotations

import json
import os
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from auris.config import FrozenConfig, ValidatorConfig
from auris.setup.fhir_packages import (
    Fetcher,
    FhirPackageError,
    _urllib_fetch,
    ensure_package,
    sha256_bytes,
    sha256_file,
)


def default_cache_dir() -> Path:
    return Path(os.environ.get("AURIS_CACHE", Path.cwd() / "cache")) / "validator"


def verify_jar(pin: ValidatorConfig, cache: Path) -> Path:
    path = cache / pin.filename
    if not pin.pinned:
        raise FhirPackageError("validator is unpinned")
    if not path.is_file():
        raise FhirPackageError("validator JAR missing; run auris setup validator --fetch")
    if path.stat().st_size != pin.bytes or sha256_file(path) != pin.sha256:
        raise FhirPackageError("validator JAR size/SHA-256 mismatch; refusing to use it")
    return path


def ensure_jar(
    pin: ValidatorConfig, cache: Path, *, fetch: bool = False, fetcher: Fetcher | None = None
) -> Path:
    path = cache / pin.filename
    if not pin.pinned:
        raise FhirPackageError("validator is unpinned")
    if not path.exists() and fetch:
        data = (fetcher or _urllib_fetch)(pin.url)
        if len(data) != pin.bytes or sha256_bytes(data) != pin.sha256:
            raise FhirPackageError("downloaded validator JAR size/SHA-256 mismatch")
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as handle:
            partial = Path(handle.name)
            handle.write(data)
        try:
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
    return verify_jar(pin, cache)


def _no_fetch(url: str) -> bytes:
    raise FhirPackageError(f"pinned package missing ({url}); run auris setup fhir first")


def _extract_package(archive: Path, destination: Path) -> None:
    """Reject links/traversal; core also ships openapi/ and other/ assets."""
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            name = PurePosixPath(member.name)
            if (
                name.is_absolute()
                or ".." in name.parts
                or not name.parts
                or not (member.isfile() or member.isdir())
            ):
                raise FhirPackageError("unsafe FHIR package archive member")
        # Resource validation uses package/. Root OpenAPI/image assets in the
        # R4 archive are not validator inputs; preserve the pinned payload.
        tar.extractall(
            destination,
            members=[m for m in members if PurePosixPath(m.name).parts[0] == "package"],
            filter="data",
        )


def seed_packages(config: FrozenConfig, package_cache: Path, home: Path) -> Path:
    """Rebuild the dedicated Java cache; never copy the online probe's cache.

    Verify all inputs before replacing an existing seed. Setup and validation
    must not run concurrently against this dedicated home.
    """
    packages = [
        ensure_package(pin, package_cache, fetcher=_no_fetch) for pin in config.fhir.packages
    ]
    for package in packages:
        pin, metadata = package.pin, package.package_json
        if pin.bytes is not None and package.path.stat().st_size != pin.bytes:
            raise FhirPackageError(f"size mismatch for {pin.id}#{pin.version}")
        if (metadata.get("name"), metadata.get("version"), metadata.get("license")) != (
            pin.id,
            pin.version,
            pin.license,
        ):
            raise FhirPackageError(f"package identity/license mismatch for {pin.id}#{pin.version}")
    parent = home / ".fhir"
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / "packages"
    with tempfile.TemporaryDirectory(prefix="seed-", dir=parent) as temporary:
        staging = Path(temporary) / "packages"
        staging.mkdir()
        for package in packages:
            folder = staging / f"{package.pin.id}#{package.pin.version}"
            _extract_package(package.path, folder)
        # Exact format/version observed from validator 7.0.5's cache manager.
        (staging / "packages.ini").write_bytes(b"[cache]\r\nversion = 4\r\n\r\n")
        manifest = {f"{p.pin.id}#{p.pin.version}": p.pin.sha256 for p in packages}
        (staging / "auris-pins.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
        backup = Path(temporary) / "previous"
        if destination.exists():
            destination.rename(backup)
        try:
            staging.rename(destination)
        except OSError:
            if backup.exists():
                backup.rename(destination)
            raise
    return destination


def verify_seed(config: FrozenConfig, home: Path) -> None:
    """Require the setup receipt and every pinned package before starting Java."""
    cache = home / ".fhir" / "packages"
    expected = {f"{p.id}#{p.version}": p.sha256 for p in config.fhir.packages}
    try:
        actual = json.loads((cache / "auris-pins.json").read_text())
        if actual != expected or not (cache / "packages.ini").is_file():
            raise ValueError("incomplete or stale seed")
        for key in expected:
            if not (cache / key / "package" / "package.json").is_file():
                raise ValueError("missing seeded package")
    except (OSError, ValueError) as exc:
        raise FhirPackageError(
            "validator package cache missing/stale; run auris setup validator"
        ) from exc


def setup_validator(
    config: FrozenConfig,
    cache: Path,
    package_cache: Path,
    *,
    fetch: bool = False,
    fetcher: Fetcher | None = None,
) -> Path:
    ensure_jar(config.validator, cache, fetch=fetch, fetcher=fetcher)
    return seed_packages(config, package_cache, cache / "home")
