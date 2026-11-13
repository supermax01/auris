"""Offline setup contracts with fictional archives and fake downloads only."""

import copy
import io
import json
import tarfile
from pathlib import Path

import pytest
from helpers import make_fake_package_bytes

from auris.cli import main
from auris.config import FhirPackagePin, load_frozen_config
from auris.setup.fhir_packages import FhirPackageError, sha256_bytes
from auris.setup.validator import ensure_jar, seed_packages, setup_validator, verify_seed


@pytest.fixture
def fake_config(tmp_path):
    config = load_frozen_config()
    jar = b"fictional test JAR, never executed"
    pin = config.validator.model_copy(update={"bytes": len(jar), "sha256": sha256_bytes(jar)})
    pins = []
    archives = tmp_path / "archives"
    archives.mkdir()
    for version in ("1.0.0", "2.0.0"):
        metadata = {"name": "example.fictional", "version": version, "license": "CC0-1.0"}
        data = make_fake_package_bytes(metadata, {"package/test.json": b"{}"})
        package = FhirPackagePin(
            **{
                "id": metadata["name"],
                "version": version,
                "license": "CC0-1.0",
                "sha256": sha256_bytes(data),
                "bytes": len(data),
            }
        )
        (archives / package.filename).write_bytes(data)
        pins.append(package)
    return (
        config.model_copy(
            update={"validator": pin, "fhir": config.fhir.model_copy(update={"packages": pins})}
        ),
        jar,
        archives,
    )


def test_setup_fake_fetch_and_standard_cache_layout(tmp_path, fake_config):
    config, jar, archives = fake_config
    cache = tmp_path / "validator"
    calls = []

    def fetch(url):
        calls.append(url)
        return jar

    packages = setup_validator(config, cache, archives, fetch=True, fetcher=fetch)
    assert calls == [config.validator.url]
    assert (packages / "packages.ini").read_bytes() == b"[cache]\r\nversion = 4\r\n\r\n"
    for pin in config.fhir.packages:
        assert (packages / f"{pin.id}#{pin.version}" / "package" / "test.json").read_text() == "{}"
    verify_seed(config, cache / "home")
    setup_validator(
        config, cache, archives, fetch=True, fetcher=lambda _: pytest.fail("cache must not fetch")
    )


@pytest.mark.parametrize("cached", [True, False])
def test_jar_mismatch_refused(tmp_path, fake_config, cached):
    config, _, _ = fake_config
    if cached:
        (tmp_path / config.validator.filename).write_bytes(b"corrupt")
    with pytest.raises(FhirPackageError, match="mismatch"):
        ensure_jar(config.validator, tmp_path, fetch=True, fetcher=lambda _: b"corrupt")


def test_missing_jar_never_fetches_without_opt_in(tmp_path, fake_config):
    config, _, _ = fake_config
    with pytest.raises(FhirPackageError, match="missing"):
        ensure_jar(config.validator, tmp_path, fetcher=lambda _: pytest.fail("no fetch"))


def test_bad_archive_does_not_replace_previous_seed(tmp_path, fake_config):
    config, _, archives = fake_config
    home = tmp_path / "home"
    target = seed_packages(config, archives, home)
    marker = target / "keep-on-failure"
    marker.write_text("old seed")
    (archives / config.fhir.packages[0].filename).write_bytes(b"corrupt")
    with pytest.raises(FhirPackageError, match="SHA-256"):
        seed_packages(config, archives, home)
    assert marker.read_text() == "old seed"


@pytest.mark.parametrize("kind", ["traversal", "absolute", "symlink", "hardlink"])
def test_unsafe_tar_members_refused(tmp_path, fake_config, kind):
    config, _, archives = fake_config
    pin = config.fhir.packages[0]
    original = archives / pin.filename
    metadata = {"name": pin.id, "version": pin.version, "license": pin.license}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        data = json.dumps(metadata).encode()
        member = tarfile.TarInfo("package/package.json")
        member.size = len(data)
        tar.addfile(member, io.BytesIO(data))
        member = tarfile.TarInfo(
            {
                "traversal": "package/../../escape",
                "absolute": "/escape",
                "symlink": "package/link",
                "hardlink": "package/link",
            }[kind]
        )
        if kind in ("symlink", "hardlink"):
            member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
            member.linkname = "../../escape"
        tar.addfile(member)
    data = buffer.getvalue()
    original.write_bytes(data)
    pin = pin.model_copy(update={"bytes": len(data), "sha256": sha256_bytes(data)})
    config = config.model_copy(update={"fhir": config.fhir.model_copy(update={"packages": [pin]})})
    with pytest.raises(FhirPackageError, match="unsafe"):
        seed_packages(config, archives, tmp_path / "home")
    assert not (tmp_path / "escape").exists()


def test_missing_seed_and_config_change_refused(tmp_path, fake_config):
    config, _, archives = fake_config
    home = tmp_path / "home"
    with pytest.raises(FhirPackageError, match="missing/stale"):
        verify_seed(config, home)
    seed_packages(config, archives, home)
    changed = copy.deepcopy(config)
    changed = changed.model_copy(
        update={"fhir": changed.fhir.model_copy(update={"packages": changed.fhir.packages[:1]})}
    )
    with pytest.raises(FhirPackageError, match="missing/stale"):
        verify_seed(changed, home)


def test_cli_reports_declared_but_not_required_dependency(tmp_path, monkeypatch, capsys):
    from auris.setup import fhir_packages

    config = load_frozen_config()
    packages = [
        fhir_packages.EnsuredPackage(
            pin=p,
            path=Path(p.filename),
            downloaded=False,
            package_json={
                "license": p.license,
                "dependencies": (
                    {"hl7.fhir.r5.core": "5.0.0"} if p.id == "hl7.terminology.r5" else {}
                ),
            },
        )
        for p in config.fhir.packages
    ]
    monkeypatch.setattr(fhir_packages, "ensure_packages", lambda *args: packages)
    assert main(["setup", "fhir", "--cache", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "declared but not required" in output.lower()
    assert "hl7.fhir.r5.core#5.0.0" in output


def test_cli_validator_missing_jar_is_actionable(tmp_path, capsys):
    assert main(["setup", "validator", "--cache", str(tmp_path)]) == 1
    assert "auris setup validator --fetch" in capsys.readouterr().err
