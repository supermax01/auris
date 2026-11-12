"""Frozen reference configuration (PLAN Appendix A6).

The configuration lives in ``config/frozen.toml`` and is loaded through the
pydantic models in this module so that a missing or malformed pin fails
loudly instead of silently drifting from the audited values.
"""

from __future__ import annotations

import os
import re
import tomllib
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TODO_PLACEHOLDER_PREFIX = "TODO-"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def is_placeholder(value: str) -> bool:
    """Return True for deliberate ``TODO-...`` placeholder pins."""
    return value.startswith(TODO_PLACEHOLDER_PREFIX)


def _check_sha256(value: str, *, allow_unpinned: bool, field: str) -> str:
    if is_placeholder(value):
        if not allow_unpinned:
            raise ValueError(f"{field} is an unpinned placeholder but allow_unpinned is false")
        return value
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{field} must be a 64-character lowercase hex SHA-256")
    return value


def _check_commit_sha(value: str, *, field: str) -> str:
    if not _COMMIT_RE.fullmatch(value):
        raise ValueError(f"{field} must be a 40-character lowercase hex commit SHA")
    return value


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FhirPackagePin(_FrozenModel):
    """One pinned FHIR package tarball (PLAN A6/A8: fetched, never bundled)."""

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    sha256: str = Field(min_length=1)
    license: str = Field(min_length=1)
    allow_unpinned: bool = False

    @model_validator(mode="after")
    def _check_pin(self) -> FhirPackagePin:
        _check_sha256(self.sha256, allow_unpinned=self.allow_unpinned, field="sha256")
        return self

    @property
    def pinned(self) -> bool:
        return not is_placeholder(self.sha256)

    @property
    def filename(self) -> str:
        return f"{self.id}-{self.version}.tgz"


class FhirConfig(_FrozenModel):
    version: Literal["4.0.1"]
    us_core_package_id: str = Field(min_length=1)
    us_core_version: str = Field(min_length=1)
    packages: list[FhirPackagePin] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_packages(self) -> FhirConfig:
        seen: set[tuple[str, str]] = set()
        for package in self.packages:
            key = (package.id, package.version)
            if key in seen:
                raise ValueError(f"duplicate FHIR package pin: {package.id}#{package.version}")
            seen.add(key)
        us_core = self.package(self.us_core_package_id)
        if us_core is None:
            raise ValueError(f"US Core package {self.us_core_package_id} is not pinned")
        if us_core.version != self.us_core_version:
            raise ValueError("us_core_version does not match the pinned US Core package version")
        core = self.package("hl7.fhir.r4.core")
        if core is None or core.version != self.version:
            raise ValueError("hl7.fhir.r4.core must be pinned at the FHIR version")
        return self

    def package(self, package_id: str) -> FhirPackagePin | None:
        for package in self.packages:
            if package.id == package_id:
                return package
        return None


class LaptopAsrConfig(_FrozenModel):
    source_model: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    license: str = Field(min_length=1)

    @field_validator("revision")
    @classmethod
    def _check_revision(cls, value: str) -> str:
        return _check_commit_sha(value, field="revision")


class AsrConfig(_FrozenModel):
    laptop: LaptopAsrConfig


class ExtractorConfig(_FrozenModel):
    model: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    gguf_filename: str = Field(min_length=1)
    gguf_sha256: str = Field(min_length=1)
    license: str = Field(min_length=1)
    n_ctx: int = Field(gt=0)
    thinking: bool
    temperature: float
    temperature_status: str = Field(min_length=1)

    @field_validator("revision")
    @classmethod
    def _check_revision(cls, value: str) -> str:
        return _check_commit_sha(value, field="revision")

    @field_validator("gguf_sha256")
    @classmethod
    def _check_gguf_sha256(cls, value: str) -> str:
        return _check_sha256(value, allow_unpinned=False, field="gguf_sha256")

    @field_validator("gguf_filename")
    @classmethod
    def _check_gguf_filename(cls, value: str) -> str:
        if not value.endswith(".gguf"):
            raise ValueError("gguf_filename must name a .gguf file")
        return value


class JvmConfig(_FrozenModel):
    distribution: str = Field(min_length=1)
    heap_gb: int = Field(gt=0)


class ValidatorConfig(_FrozenModel):
    artifact: str = Field(min_length=1)
    version: str = Field(min_length=1)
    sha256: str = Field(min_length=1)
    allow_unpinned: bool = False

    @model_validator(mode="after")
    def _check_pin(self) -> ValidatorConfig:
        for field, value in (("version", self.version), ("sha256", self.sha256)):
            if is_placeholder(value) and not self.allow_unpinned:
                raise ValueError(
                    f"validator {field} is an unpinned placeholder but allow_unpinned is false"
                )
        if not is_placeholder(self.sha256):
            _check_sha256(self.sha256, allow_unpinned=False, field="sha256")
        return self

    @property
    def pinned(self) -> bool:
        return not (is_placeholder(self.version) or is_placeholder(self.sha256))


class LaptopReferenceMachine(_FrozenModel):
    name: str = Field(min_length=1)
    chip: str = Field(min_length=1)
    memory_gb: int = Field(gt=0)
    pipeline_memory_budget_gb: int = Field(gt=0)
    runtime_target: str = Field(min_length=1)


class ServerReferenceMachine(_FrozenModel):
    name: str = Field(min_length=1)
    gpu_memory_gb: int = Field(gt=0)
    memory_gb: int = Field(gt=0)


class ReferenceMachinesConfig(_FrozenModel):
    laptop: LaptopReferenceMachine
    server: ServerReferenceMachine


class TerminologyPin(_FrozenModel):
    """An independently verified, setup-only terminology archive."""

    release: str = Field(min_length=1)
    url: str = Field(pattern=r"^https://")
    filename: str = Field(pattern=r"^[A-Za-z0-9_.-]+\.zip$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    md5: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    license: str = Field(min_length=1)
    valid_from: date | None = None
    valid_to: date | None = None

    @model_validator(mode="after")
    def _check_dates(self) -> TerminologyPin:
        if (self.valid_from is None) != (self.valid_to is None):
            raise ValueError("terminology validity needs both dates")
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("terminology validity dates are reversed")
        return self


class TerminologyConfig(_FrozenModel):
    rxnorm: TerminologyPin
    icd10cm: TerminologyPin


class FrozenConfig(_FrozenModel):
    """Root of ``config/frozen.toml``."""

    fhir: FhirConfig
    asr: AsrConfig
    extractor: ExtractorConfig
    jvm: JvmConfig
    validator: ValidatorConfig
    reference_machines: ReferenceMachinesConfig
    terminology: TerminologyConfig


def default_config_path() -> Path:
    """Locate ``config/frozen.toml`` (env override, cwd, then source tree)."""
    override = os.environ.get("AURIS_FROZEN_CONFIG")
    if override:
        return Path(override)
    candidate = Path.cwd() / "config" / "frozen.toml"
    if candidate.is_file():
        return candidate
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "config" / "frozen.toml"
        if candidate.is_file():
            return candidate
    return Path.cwd() / "config" / "frozen.toml"


def load_frozen_config(path: str | Path | None = None) -> FrozenConfig:
    """Load and validate the frozen configuration from TOML."""
    config_path = Path(path) if path is not None else default_config_path()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    return FrozenConfig.model_validate(raw)
