"""Frozen reference configuration.

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

from auris.extract.profiles import ExtractionProfile

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
    """One pinned FHIR package tarball."""

    id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    version: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    sha256: str = Field(min_length=1)
    license: str = Field(min_length=1)
    allow_unpinned: bool = False
    bytes: int | None = Field(default=None, gt=0)
    required_by: str | None = None

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
        us_core = self.package(self.us_core_package_id, self.us_core_version)
        if us_core is None:
            raise ValueError(f"US Core package {self.us_core_package_id} is not pinned")
        if us_core.version != self.us_core_version:
            raise ValueError("us_core_version does not match the pinned US Core package version")
        core = self.package("hl7.fhir.r4.core")
        if core is None or core.version != self.version:
            raise ValueError("hl7.fhir.r4.core must be pinned at the FHIR version")
        return self

    def package(self, package_id: str, version: str | None = None) -> FhirPackagePin | None:
        for package in self.packages:
            if package.id == package_id and (version is None or package.version == version):
                return package
        return None


class PinnedFile(_FrozenModel):
    """One upstream file, pinned by SHA-256 (large files) or Git blob SHA-1.

    Hugging Face lists large (LFS) files by SHA-256 and small files by
    their Git blob id, so either pin is checked against the source listing.
    """

    name: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    bytes: int = Field(gt=0)
    sha256: str | None = None
    git_blob_sha1: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")

    @model_validator(mode="after")
    def _one_pin(self) -> PinnedFile:
        if (self.sha256 is None) == (self.git_blob_sha1 is None):
            raise ValueError(f"{self.name}: pin exactly one of sha256 or git_blob_sha1")
        if self.sha256 is not None:
            _check_sha256(self.sha256, allow_unpinned=False, field=f"{self.name} sha256")
        return self


class ConvertedFile(_FrozenModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    sha256: str

    @field_validator("sha256")
    @classmethod
    def _check(cls, value: str) -> str:
        return _check_sha256(value, allow_unpinned=False, field="converted file sha256")


class AsrConversion(_FrozenModel):
    """Auris converts OpenAI's original weights itself."""

    tool: Literal["ctranslate2"]
    tool_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    quantization: Literal["int8"]
    copy_files: tuple[str, ...]
    files: tuple[ConvertedFile, ...] = Field(min_length=1)


class LaptopAsrConfig(_FrozenModel):
    source_model: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    license: str = Field(min_length=1)
    source_files: tuple[PinnedFile, ...] = Field(min_length=1)
    conversion: AsrConversion

    @field_validator("revision")
    @classmethod
    def _check_revision(cls, value: str) -> str:
        return _check_commit_sha(value, field="revision")


class AsrDecoding(_FrozenModel):
    """Fixed decoding settings: deterministic, so a rerun gives the same words."""

    language: Literal["en"] = "en"
    beam_size: int = Field(default=5, ge=1, le=10)
    temperature: float = Field(default=0.0, ge=0, le=1)
    condition_on_previous_text: bool = False
    vad_filter: bool = True
    word_timestamps: Literal[True] = True
    # Style only (punctuation, capitals). It must say nothing clinical: Whisper can
    # repeat its prompt into quiet stretches, and a leaked symptom would be a fact.
    initial_prompt: str | None = None
    # Also give the style sentence to every 30-second window (faster-whisper's
    # per-window prompt). Without it the style reaches only the first window,
    # because no earlier text is carried over (condition_on_previous_text).
    style_every_window: bool = False

    @field_validator("initial_prompt")
    @classmethod
    def _non_clinical(cls, value: str | None) -> str | None:
        if value is not None and re.search(
            r"\d|pain|cough|fever|allerg|mg\b|tablet|blood|pressure|diabet|asthma", value, re.I
        ):
            raise ValueError("initial_prompt must contain no clinical words or numbers")
        return value


_HINT_WORD = re.compile(r"[a-z]+(?:[ -][a-z]+)*")


class AsrHints(_FrozenModel):
    """Drug names Whisper is nudged toward (faster-whisper ``hotwords``).

    The words go in front of every 30-second window as if they had just been
    said, so Whisper favours their spelling. That is also the risk the style
    prompt is guarded against: Whisper can repeat its prompt into quiet
    stretches, and a drug name that was never said would become a fact. So
    the list is fixed, comes from a source independent of every evaluation
    set, and is used only when its measured effect says so (``enabled``).
    """

    enabled: bool = False
    source: str = ""
    words: tuple[str, ...] = ()

    @field_validator("words")
    @classmethod
    def _check_words(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for word in value:
            if not _HINT_WORD.fullmatch(word):
                raise ValueError(f"hint {word!r} must be lower-case letters, spaces or hyphens")
        if len(set(value)) != len(value):
            raise ValueError("hint words must be unique")
        return value

    @model_validator(mode="after")
    def _check_source(self) -> AsrHints:
        if self.words and not self.source.strip():
            raise ValueError("a hint list must say where it comes from (source)")
        if self.enabled and not self.words:
            raise ValueError("hints are enabled but the list is empty")
        return self

    @property
    def prompt(self) -> str | None:
        """The text given to Whisper, or None when hints are off."""
        return ", ".join(self.words) if self.enabled else None

    def report(self) -> dict | None:
        """What a transcript records about its hints (None when off), including the
        exact text Whisper was given, so a change in how it is built is never reused."""
        if not self.enabled:
            return None
        return {"source": self.source, "words": list(self.words), "prompt": self.prompt}


class AsrSegmentation(_FrozenModel):
    """Join a speaker's consecutive pieces into turns (``auris.speech.turns``)."""

    join_turns: bool = False
    gap_seconds: float = Field(default=1.5, ge=0, le=5)
    max_seconds: float = Field(default=60, gt=5, le=300)

    def report(self) -> dict | None:
        """What a transcript records about segmentation (None when off)."""
        if not self.join_turns:
            return None
        from auris.speech.turns import JOIN_VERSION

        return {"version": JOIN_VERSION, **self.model_dump(mode="json")}


class WordListPin(_FrozenModel):
    """A pinned English word list (setup-only download)."""

    url: str = Field(pattern=r"^https://")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(gt=0)
    license: str = Field(min_length=1)


class AsrSpelling(_FrozenModel):
    """Misheard drug names respelled as the RxNorm ingredient they sound like
    (``auris.speech.spelling``). Used only when its measured effect says so."""

    enabled: bool = False
    word_list: WordListPin | None = None

    @model_validator(mode="after")
    def _check_word_list(self) -> AsrSpelling:
        if self.enabled and self.word_list is None:
            raise ValueError("drug-name spelling is enabled but has no word list")
        return self


class SpeakerEmbeddingConfig(_FrozenModel):
    """Voice fingerprints for "who spoke when" (Silero VAD + SpeechBrain)."""

    source_model: str = Field(min_length=1)
    revision: str
    license: Literal["Apache-2.0"]
    file: PinnedFile

    @field_validator("revision")
    @classmethod
    def _check_revision(cls, value: str) -> str:
        return _check_commit_sha(value, field="revision")


class AsrConfig(_FrozenModel):
    laptop: LaptopAsrConfig
    decoding: AsrDecoding = AsrDecoding()
    hints: AsrHints = AsrHints()
    spelling: AsrSpelling = AsrSpelling()
    segmentation: AsrSegmentation = AsrSegmentation()
    speaker_embedding: SpeakerEmbeddingConfig


class CardSampling(_FrozenModel):
    """A-priori upstream non-thinking settings, never selected on Auris data."""

    source_repo: str = Field(min_length=1)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    readme_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: Literal["non-thinking"]
    temperature: float = Field(gt=0, allow_inf_nan=False)
    top_p: float = Field(gt=0, le=1)
    top_k: int = Field(ge=0)
    min_p: float = Field(default=0.0, ge=0, le=1)
    presence_penalty: float = Field(default=0.0, allow_inf_nan=False)
    repeat_penalty: float = Field(default=1.0, gt=0, allow_inf_nan=False)


class LoopGuardConfig(_FrozenModel):
    version: Literal["loop-guard-v1"] = "loop-guard-v1"
    identical_assertion_limit: Literal[4] = 4


class ExtractorCandidate(_FrozenModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    repo: str = Field(min_length=1)
    revision: str
    gguf_filename: str = Field(pattern=r"^[A-Za-z0-9_.-]+\.gguf$")
    sha256: str
    license: Literal["Apache-2.0", "MIT", "BSD-3-Clause", "BSD-2-Clause"]
    quantization: str = Field(min_length=1)
    runtime_flags: tuple[str, ...]
    role: Literal["default", "comparator"]
    bytes: int | None = Field(default=None, gt=0)
    generation_tokens_per_second: float = Field(default=20, gt=0, allow_inf_nan=False)
    prompt_tokens_per_second: float = Field(default=100, gt=0, allow_inf_nan=False)
    card_sampling: CardSampling | None = None

    @model_validator(mode="after")
    def _pins(self) -> ExtractorCandidate:
        _check_commit_sha(self.revision, field="revision")
        _check_sha256(self.sha256, allow_unpinned=False, field="sha256")
        # Flags cannot override the loopback binding or load remote artifacts.
        if self.runtime_flags not in (
            (),
            ("--reasoning", "off"),
            ("--reasoning", "off", "--reasoning-budget", "0"),
        ):
            raise ValueError("only the tested reasoning-off runtime flags are supported")
        return self


class ExtractorConfig(_FrozenModel):
    extraction_profile: ExtractionProfile = "full-v0"
    seed: int | None = Field(default=None, ge=0, le=4294967295)
    temperature_override: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    loop_guard: LoopGuardConfig = LoopGuardConfig()
    model: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    gguf_filename: str = Field(min_length=1)
    gguf_sha256: str = Field(min_length=1)
    license: str = Field(min_length=1)
    n_ctx: int = Field(gt=0)
    thinking: Literal[False]
    max_output_tokens: int = Field(gt=0)
    min_output_tokens: int = Field(gt=0)
    candidates: tuple[ExtractorCandidate, ...] = Field(min_length=1)
    temperature: float
    temperature_status: str = Field(min_length=1)

    @model_validator(mode="after")
    def _candidates(self) -> ExtractorConfig:
        defaults = [candidate for candidate in self.candidates if candidate.role == "default"]
        if len(defaults) != 1 or len({c.id for c in self.candidates}) != len(self.candidates):
            raise ValueError("extractor requires unique candidate ids and exactly one default")
        default = defaults[0]
        if (
            default.repo,
            default.revision,
            default.gguf_filename,
            default.sha256,
            default.license,
        ) != (self.model, self.revision, self.gguf_filename, self.gguf_sha256, self.license):
            raise ValueError("default candidate must match the existing extractor pins")
        if self.min_output_tokens > self.max_output_tokens:
            raise ValueError("min_output_tokens must not exceed max_output_tokens")
        if self.min_output_tokens >= self.n_ctx:
            raise ValueError("min_output_tokens must leave room for the prompt")
        return self

    def candidate(self, model_id: str | None = None) -> ExtractorCandidate:
        for candidate in self.candidates:
            if candidate.id == model_id or (model_id is None and candidate.role == "default"):
                return candidate
        raise ValueError("unknown extractor model id")

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
    heap_gb: int = Field(gt=0, le=2)


class ValidatorConfig(_FrozenModel):
    artifact: str = Field(pattern=r"^[A-Za-z0-9_.-]+\.jar$")
    version: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    sha256: str = Field(min_length=1)
    allow_unpinned: bool = False
    url: str = Field(pattern=r"^https://")
    bytes: int = Field(gt=0)
    license: str = Field(min_length=1)

    @property
    def filename(self) -> str:
        return f"{Path(self.artifact).stem}-{self.version}.jar"

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


class DatasetPin(_FrozenModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    repository: str = Field(pattern=r"^https://github.com/")
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    license: Literal["CC-BY-4.0"]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ArchiveDatasetPin(_FrozenModel):
    """A public dataset published as one archive (not a Git repository)."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    url: str = Field(pattern=r"^https://")
    doi: str
    version: int = Field(ge=1)
    license: Literal["CC0-1.0"]
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    archive_bytes: int = Field(gt=0)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FhirServerConfig(_FrozenModel):
    """Local FHIR test server for the integration proof.

    The server file is taken from HAPI's official image by layer digest
    (content-addressed) and pinned by SHA-256; no Docker daemon is needed.
    """

    software: str = Field(min_length=1)
    image: str = Field(min_length=1)
    tag: str = Field(min_length=1)
    index_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    layer_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    member: str = Field(min_length=1)
    war_bytes: int = Field(gt=0)
    war_sha256: str
    license: Literal["Apache-2.0"]
    heap_mb: int = Field(default=1024, ge=256)

    @field_validator("war_sha256")
    @classmethod
    def _check(cls, value: str) -> str:
        return _check_sha256(value, allow_unpinned=False, field="war_sha256")


class FrozenConfig(_FrozenModel):
    """Root of ``config/frozen.toml``."""

    fhir: FhirConfig
    asr: AsrConfig
    extractor: ExtractorConfig
    jvm: JvmConfig
    validator: ValidatorConfig
    fhir_server: FhirServerConfig
    reference_machines: ReferenceMachinesConfig
    terminology: TerminologyConfig
    datasets: tuple[DatasetPin, ...] = ()
    # Archive-published corpora, kept apart from the Git pins.
    archive_datasets: tuple[ArchiveDatasetPin, ...] = ()

    @field_validator("datasets")
    @classmethod
    def _unique_datasets(cls, pins: tuple[DatasetPin, ...]) -> tuple[DatasetPin, ...]:
        if len({pin.id for pin in pins}) != len(pins):
            raise ValueError("duplicate dataset pin")
        return pins


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
    """Load and validate the frozen configuration from TOML.

    An optional ``research.toml`` beside it adds extractor candidates and
    dataset pins (never changing the app's own values)."""
    config_path = Path(path) if path is not None else default_config_path()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    extra_path = config_path.with_name("research.toml")
    if extra_path.is_file():
        with extra_path.open("rb") as handle:
            extra = tomllib.load(handle)
        unknown = set(extra) - {"extractor", "datasets", "archive_datasets"} or set(
            extra.get("extractor", {})
        ) - {"candidates"}
        if unknown:
            raise ValueError(f"research.toml may only add candidates and datasets: {unknown}")
        raw["extractor"]["candidates"] = [
            *raw["extractor"].get("candidates", []),
            *extra.get("extractor", {}).get("candidates", []),
        ]
        for key in ("datasets", "archive_datasets"):
            if key in extra:
                raw[key] = [*raw.get(key, []), *extra[key]]
    return FrozenConfig.model_validate(raw)


def with_hints(config: FrozenConfig, enabled: bool | None) -> FrozenConfig:
    """The config with speech hints switched on or off (None: as frozen).

    For measuring the hints' effect; the choice is recorded in every
    transcript's provenance, so runs with and without them are never mixed.
    """
    if enabled is None or enabled == config.asr.hints.enabled:
        return config
    hints = AsrHints.model_validate({**config.asr.hints.model_dump(), "enabled": enabled})
    return config.model_copy(update={"asr": config.asr.model_copy(update={"hints": hints})})


def with_spelling(config: FrozenConfig, enabled: bool | None) -> FrozenConfig:
    """The config with drug-name spelling switched on or off (None: as frozen)."""
    if enabled is None or enabled == config.asr.spelling.enabled:
        return config
    spelling = AsrSpelling.model_validate({**config.asr.spelling.model_dump(), "enabled": enabled})
    return config.model_copy(update={"asr": config.asr.model_copy(update={"spelling": spelling})})


def with_speech_options(
    config: FrozenConfig,
    *,
    style_every_window: bool | None = None,
    join_turns: bool | None = None,
) -> FrozenConfig:
    """The config with the per-window style prompt and/or turn joining switched
    on or off (None: as frozen); both are recorded in transcript provenance."""
    asr = config.asr
    if style_every_window is not None:
        decoding = AsrDecoding.model_validate(
            {**asr.decoding.model_dump(), "style_every_window": style_every_window}
        )
        asr = asr.model_copy(update={"decoding": decoding})
    if join_turns is not None:
        segmentation = AsrSegmentation.model_validate(
            {**asr.segmentation.model_dump(), "join_turns": join_turns}
        )
        asr = asr.model_copy(update={"segmentation": segmentation})
    return config if asr is config.asr else config.model_copy(update={"asr": asr})
