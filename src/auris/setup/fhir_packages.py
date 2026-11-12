"""Download and verify pinned FHIR packages (PLAN A6/A8, Step 1a).

FHIR packages are gzip tarballs served by the official package registry.
This is a setup-time step: it is the only Auris code path in this slice
that uses the network. Everything downloaded is verified against the
SHA-256 pinned in ``config/frozen.toml``; a mismatching file is refused.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auris.config import FhirPackagePin

# Base URLs: the primary registry serves /<id>/<version> from its root;
# the fallback serves the same API under /packages (verified 2026-11-12).
REGISTRIES: tuple[str, ...] = (
    "https://packages.fhir.org",
    "https://packages2.fhir.org/packages",
)

Fetcher = Callable[[str], bytes]
"""Fetch one URL and return its body. Injectable so tests stay offline."""


class FhirPackageError(Exception):
    """Base error for FHIR package setup failures."""


class ChecksumMismatchError(FhirPackageError):
    """A package file does not match its pinned SHA-256."""


def default_cache_dir() -> Path:
    """Return the FHIR package cache directory.

    ``./cache/fhir-packages/`` by default; the ``AURIS_CACHE`` env var
    overrides the cache root (``$AURIS_CACHE/fhir-packages/``).
    """
    override = os.environ.get("AURIS_CACHE")
    root = Path(override) if override else Path.cwd() / "cache"
    return root / "fhir-packages"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_urls(pin: FhirPackagePin, registries: Sequence[str] = REGISTRIES) -> list[str]:
    return [f"{base.rstrip('/')}/{pin.id}/{pin.version}" for base in registries]


def package_path(cache_dir: Path, pin: FhirPackagePin) -> Path:
    return cache_dir / pin.filename


def _urllib_fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "auris-setup"})
    with urllib.request.urlopen(request, timeout=180) as response:  # noqa: S310 - pinned hosts
        return response.read()


@dataclass(frozen=True)
class EnsuredPackage:
    """A package present in the cache and verified against its pin."""

    pin: FhirPackagePin
    path: Path
    downloaded: bool
    package_json: dict[str, Any]


def ensure_package(
    pin: FhirPackagePin,
    cache_dir: Path,
    *,
    fetcher: Fetcher | None = None,
    registries: Sequence[str] = REGISTRIES,
) -> EnsuredPackage:
    """Make sure one pinned package is in the cache and matches its SHA-256.

    A file already in the cache is verified, never re-downloaded. A
    checksum mismatch — cached or freshly downloaded — is refused.
    Registry fallback applies to transport errors only: bytes that
    download successfully but fail verification are never accepted.
    """
    if not pin.pinned:
        raise FhirPackageError(f"refusing to fetch unpinned package {pin.id}#{pin.version}")
    destination = package_path(cache_dir, pin)
    downloaded = False
    if destination.is_file():
        actual = sha256_file(destination)
        if actual != pin.sha256:
            raise ChecksumMismatchError(
                f"cached {destination} has SHA-256 {actual}, expected {pin.sha256};"
                " refusing to use it (delete the cache file to re-fetch)"
            )
    else:
        fetch = fetcher if fetcher is not None else _urllib_fetch
        errors: list[str] = []
        data: bytes | None = None
        for url in package_urls(pin, registries):
            try:
                data = fetch(url)
                break
            except Exception as exc:  # transport error: try the next registry
                errors.append(f"{url}: {exc}")
        if data is None:
            raise FhirPackageError(
                f"could not download {pin.id}#{pin.version}: " + "; ".join(errors)
            )
        actual = sha256_bytes(data)
        if actual != pin.sha256:
            raise ChecksumMismatchError(
                f"downloaded {pin.id}#{pin.version} has SHA-256 {actual},"
                f" expected {pin.sha256}; refusing to use it"
            )
        cache_dir.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        partial.write_bytes(data)
        partial.replace(destination)
        downloaded = True
    return EnsuredPackage(
        pin=pin,
        path=destination,
        downloaded=downloaded,
        package_json=read_package_json(destination),
    )


def ensure_packages(
    pins: Sequence[FhirPackagePin],
    cache_dir: Path,
    *,
    fetcher: Fetcher | None = None,
    registries: Sequence[str] = REGISTRIES,
) -> list[EnsuredPackage]:
    return [ensure_package(pin, cache_dir, fetcher=fetcher, registries=registries) for pin in pins]


def _open_tarball(path: Path) -> tarfile.TarFile:
    try:
        return tarfile.open(path, "r:gz")
    except tarfile.TarError as exc:
        raise FhirPackageError(f"{path} is not a readable gzip tarball: {exc}") from exc


def read_package_member(path: Path, member_name: str) -> bytes:
    """Read one file from inside a package tarball (no extraction to disk)."""
    candidates = {member_name, f"./{member_name}"}
    with _open_tarball(path) as tarball:
        for member in tarball.getmembers():
            if member.name in candidates and member.isfile():
                extracted = tarball.extractfile(member)
                if extracted is None:
                    break
                return extracted.read()
    raise FhirPackageError(f"{member_name} not found in {path}")


def list_package_members(path: Path) -> list[str]:
    with _open_tarball(path) as tarball:
        return sorted(member.name for member in tarball.getmembers() if member.isfile())


def read_package_json(path: Path) -> dict[str, Any]:
    raw = read_package_member(path, "package/package.json")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FhirPackageError(f"package/package.json in {path} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise FhirPackageError(f"package/package.json in {path} is not a JSON object")
    return parsed


def declared_dependencies(package_json: dict[str, Any]) -> list[tuple[str, str]]:
    """Return the ``dependencies`` a package declares, sorted by id."""
    declared = package_json.get("dependencies") or {}
    if not isinstance(declared, dict):
        raise FhirPackageError("package.json dependencies is not an object")
    return sorted((str(pkg_id), str(version)) for pkg_id, version in declared.items())


def unpinned_declared_dependencies(
    ensured: Sequence[EnsuredPackage], pins: Sequence[FhirPackagePin]
) -> list[tuple[str, str, str]]:
    """Declared dependencies (by any fetched package) that are not pinned.

    Returns ``(declaring_package_id, dependency_id, dependency_version)``
    rows. These are reported, never fetched silently: a new pin is a
    deliberate config change.
    """
    pinned = {(pin.id, pin.version) for pin in pins}
    rows: list[tuple[str, str, str]] = []
    for package in ensured:
        for dep_id, dep_version in declared_dependencies(package.package_json):
            if (dep_id, dep_version) not in pinned:
                rows.append((package.pin.id, dep_id, dep_version))
    return sorted(rows)


def make_fake_package_bytes(package_json: dict[str, Any], members: dict[str, bytes]) -> bytes:
    """Build a tiny package-shaped tarball in memory. Test support only."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tarball:
        payload = {"package/package.json": json.dumps(package_json).encode(), **members}
        for name, data in payload.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tarball.addfile(info, io.BytesIO(data))
    return buffer.getvalue()
