"""Offline tests for FHIR package setup with a fictional fake tarball."""

import pytest

from auris.cli import main
from auris.config import FhirPackagePin
from auris.setup import fhir_packages

PACKAGE_JSON = {
    "name": "example.fictional",
    "version": "0.0.1",
    "license": "CC0-1.0",
    "dependencies": {"hl7.fhir.r4.core": "4.0.1"},
}
MEMBERS = {"package/StructureDefinition-fictional.json": b'{"id": "fictional"}'}


@pytest.fixture()
def package_bytes():
    return fhir_packages.make_fake_package_bytes(PACKAGE_JSON, MEMBERS)


@pytest.fixture()
def pin(package_bytes):
    return FhirPackagePin(
        id="example.fictional",
        version="0.0.1",
        sha256=fhir_packages.sha256_bytes(package_bytes),
        license="CC0-1.0",
    )


def test_ensure_package_downloads_verifies_and_caches(tmp_path, pin, package_bytes):
    calls = []

    def fetch(url):
        calls.append(url)
        return package_bytes

    ensured = fhir_packages.ensure_package(pin, tmp_path, fetcher=fetch)
    assert ensured.downloaded is True
    assert ensured.path.is_file()
    assert ensured.package_json["license"] == "CC0-1.0"
    assert calls == ["https://packages.fhir.org/example.fictional/0.0.1"]

    def forbidden(url):  # pragma: no cover - must not be called
        raise AssertionError("cached package must not be downloaded again")

    cached = fhir_packages.ensure_package(pin, tmp_path, fetcher=forbidden)
    assert cached.downloaded is False


def test_registry_fallback_on_transport_error(tmp_path, pin, package_bytes):
    def fetch(url):
        if "packages2" not in url:
            raise OSError("primary registry down (fictional)")
        return package_bytes

    ensured = fhir_packages.ensure_package(pin, tmp_path, fetcher=fetch)
    assert ensured.downloaded is True


def test_checksum_mismatch_download_is_refused(tmp_path, package_bytes):
    bad_pin = FhirPackagePin(
        id="example.fictional",
        version="0.0.1",
        sha256="0" * 64,
        license="CC0-1.0",
    )
    with pytest.raises(fhir_packages.ChecksumMismatchError):
        fhir_packages.ensure_package(bad_pin, tmp_path, fetcher=lambda url: package_bytes)
    assert not fhir_packages.package_path(tmp_path, bad_pin).exists()


def test_checksum_mismatch_cached_file_is_refused(tmp_path, pin):
    destination = fhir_packages.package_path(tmp_path, pin)
    destination.write_bytes(b"tampered")
    with pytest.raises(fhir_packages.ChecksumMismatchError):
        fhir_packages.ensure_package(pin, tmp_path, fetcher=lambda url: b"")


def test_unpinned_package_is_refused(tmp_path):
    unpinned = FhirPackagePin(
        id="example.fictional",
        version="0.0.1",
        sha256="TODO-pin-later",
        license="CC0-1.0",
        allow_unpinned=True,
    )
    with pytest.raises(fhir_packages.FhirPackageError, match="unpinned"):
        fhir_packages.ensure_package(unpinned, tmp_path, fetcher=lambda url: b"")


def test_package_members_dependencies_and_unpinned_report(tmp_path, pin, package_bytes):
    ensured = fhir_packages.ensure_package(pin, tmp_path, fetcher=lambda url: package_bytes)
    assert (
        fhir_packages.read_package_member(
            ensured.path, "package/StructureDefinition-fictional.json"
        )
        == b'{"id": "fictional"}'
    )
    assert fhir_packages.declared_dependencies(ensured.package_json) == [
        ("hl7.fhir.r4.core", "4.0.1")
    ]
    assert fhir_packages.unpinned_declared_dependencies([ensured], [pin]) == [
        ("example.fictional", "hl7.fhir.r4.core", "4.0.1")
    ]
    with pytest.raises(fhir_packages.FhirPackageError, match="not found"):
        fhir_packages.read_package_member(ensured.path, "package/missing.json")


def test_cli_checklist_fails_cleanly_without_cache(tmp_path, capsys):
    result = main(["checklist", "--cache", str(tmp_path)])
    assert result == 1
    assert "auris setup fhir" in capsys.readouterr().err
