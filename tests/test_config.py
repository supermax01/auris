"""Frozen configuration tests (PLAN A6)."""

import copy
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from auris.config import FrozenConfig, load_frozen_config

REPO_ROOT = Path(__file__).resolve().parents[1]
FROZEN_TOML = REPO_ROOT / "config" / "frozen.toml"


@pytest.fixture(scope="module")
def raw_config():
    with FROZEN_TOML.open("rb") as handle:
        return tomllib.load(handle)


def test_committed_frozen_config_loads():
    config = load_frozen_config(FROZEN_TOML)
    assert config.fhir.version == "4.0.1"
    assert config.fhir.us_core_package_id == "hl7.fhir.us.core"
    assert config.fhir.us_core_version == "6.1.0"
    us_core = config.fhir.package("hl7.fhir.us.core")
    assert us_core is not None and us_core.license == "CC0-1.0"
    assert all(package.pinned for package in config.fhir.packages)
    assert config.asr.laptop.source_model == "openai/whisper-large-v3-turbo"
    assert config.extractor.model == "Qwen/Qwen3-8B-GGUF"
    assert config.extractor.gguf_filename == "Qwen3-8B-Q4_K_M.gguf"
    assert config.extractor.n_ctx == 8192
    assert config.extractor.thinking is False
    assert config.extractor.temperature == 0.0
    assert config.extractor.temperature_status == "candidate, confirm in Slice 1"
    assert config.jvm.distribution == "temurin-21"
    assert config.jvm.heap_gb == 2
    assert config.reference_machines.laptop.memory_gb == 16
    assert config.reference_machines.server.gpu_memory_gb == 24


def test_us_core_declared_dependencies_are_pinned():
    # US Core 6.1.0 package.json `dependencies`, recorded in Step 1.
    config = load_frozen_config(FROZEN_TOML)
    declared = {
        "hl7.fhir.r4.core": "4.0.1",
        "hl7.terminology.r4": "5.0.0",
        "hl7.fhir.uv.extensions.r4": "1.0.0",
        "hl7.fhir.uv.bulkdata": "2.0.0",
        "hl7.fhir.uv.smart-app-launch": "2.1.0",
        "us.nlm.vsac": "0.11.0",
        "hl7.fhir.uv.sdc": "3.0.0",
        "us.cdc.phinvads": "0.12.0",
        "ihe.formatcode.fhir": "1.1.0",
    }
    for package_id, version in declared.items():
        pin = config.fhir.package(package_id)
        assert pin is not None, package_id
        assert pin.version == version


def test_validator_placeholder_requires_allow_unpinned(raw_config):
    config = FrozenConfig.model_validate(copy.deepcopy(raw_config))
    assert config.validator.pinned is False
    assert config.validator.allow_unpinned is True

    mutated = copy.deepcopy(raw_config)
    mutated["validator"]["allow_unpinned"] = False
    with pytest.raises(ValidationError, match="allow_unpinned"):
        FrozenConfig.model_validate(mutated)

    pinned = copy.deepcopy(raw_config)
    pinned["validator"] = {
        "artifact": "validator_cli.jar",
        "version": "1.0.0",
        "sha256": "a" * 64,
        "allow_unpinned": False,
    }
    assert FrozenConfig.model_validate(pinned).validator.pinned is True


def test_wrong_length_and_missing_sha_are_rejected(raw_config):
    mutated = copy.deepcopy(raw_config)
    mutated["fhir"]["packages"][0]["sha256"] = "abc123"
    with pytest.raises(ValidationError, match="SHA-256"):
        FrozenConfig.model_validate(mutated)

    mutated = copy.deepcopy(raw_config)
    del mutated["fhir"]["packages"][0]["sha256"]
    with pytest.raises(ValidationError):
        FrozenConfig.model_validate(mutated)

    mutated = copy.deepcopy(raw_config)
    mutated["extractor"]["gguf_sha256"] = "TODO-pin-later"
    with pytest.raises(ValidationError):
        FrozenConfig.model_validate(mutated)


def test_bad_revision_and_duplicate_package_are_rejected(raw_config):
    mutated = copy.deepcopy(raw_config)
    mutated["asr"]["laptop"]["revision"] = "main"
    with pytest.raises(ValidationError, match="commit SHA"):
        FrozenConfig.model_validate(mutated)

    mutated = copy.deepcopy(raw_config)
    mutated["fhir"]["packages"].append(dict(mutated["fhir"]["packages"][0]))
    with pytest.raises(ValidationError, match="duplicate"):
        FrozenConfig.model_validate(mutated)


def test_load_from_file_rejects_corrupted_sha(tmp_path):
    text = FROZEN_TOML.read_text()
    corrupted = text.replace(
        "ebd7731df7d36b5b7d39d5fb6c9d77b44bb7fe5742f1a2e87f164738c3289d44",
        "not-a-sha",
        1,
    )
    assert corrupted != text
    target = tmp_path / "frozen.toml"
    target.write_text(corrupted)
    with pytest.raises(ValidationError):
        load_frozen_config(target)
