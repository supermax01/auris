"""Versioned extraction contracts; the full profile remains the default."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from auris.config import ExtractorCandidate, ExtractorConfig

ExtractionProfile = Literal[
    "full-v0",
    "full-v0-dry",
    "full-v0-card",
    "lean-v1",
    "abl-optional",
    "abl-quotes",
    "abl-compact",
    "abl-order",
    "grammar-compact",
    "grammar-compact-quotes",
    "grammar-newline",
    "grammar-newline-quotes",
]
SchemaVersion = Literal["1", "2"]
PropertyOrder = Literal["canonical", "evidence-last"]
GrammarWhitespace = Literal["compact", "newline"]
PROFILES: tuple[ExtractionProfile, ...] = (
    "full-v0",
    "full-v0-dry",
    "full-v0-card",
    "lean-v1",
    "abl-optional",
    "abl-quotes",
    "abl-compact",
    "abl-order",
    "grammar-compact",
    "grammar-compact-quotes",
    "grammar-newline",
    "grammar-newline-quotes",
)


@dataclass(frozen=True)
class Profile:
    schema_version: SchemaVersion
    prompt_version: str
    property_order: PropertyOrder = "canonical"
    compact_json: bool = False
    grammar_whitespace: GrammarWhitespace | None = None


_CONTRACTS: dict[ExtractionProfile, Profile] = {
    "full-v0": Profile("1", "extraction_v3"),
    "full-v0-dry": Profile("1", "extraction_v3"),
    "full-v0-card": Profile("1", "extraction_v3"),
    "lean-v1": Profile("2", "extraction_v4", compact_json=True),
    "abl-optional": Profile("2", "extraction_abl_optional_v1"),
    "abl-quotes": Profile("1", "extraction_abl_quotes_v1"),
    "abl-compact": Profile("1", "extraction_abl_compact_v1", compact_json=True),
    "abl-order": Profile("2", "extraction_v4", "evidence-last", compact_json=True),
    "grammar-compact": Profile("1", "extraction_v3", grammar_whitespace="compact"),
    "grammar-compact-quotes": Profile(
        "1", "extraction_abl_quotes_v1", grammar_whitespace="compact"
    ),
    "grammar-newline": Profile("1", "extraction_v3", grammar_whitespace="newline"),
    "grammar-newline-quotes": Profile(
        "1", "extraction_abl_quotes_v1", grammar_whitespace="newline"
    ),
}
PROMPT_VERSIONS = ("extraction_v1", "extraction_v2") + tuple(
    dict.fromkeys(contract.prompt_version for contract in _CONTRACTS.values())
)


def profile_contract(profile: ExtractionProfile = "full-v0") -> Profile:
    try:
        return _CONTRACTS[profile]
    except KeyError:
        raise ValueError("unsupported extraction profile") from None


def sampling_parameters(
    profile: ExtractionProfile,
    candidate: ExtractorCandidate | None = None,
    *,
    seed: int | None = None,
) -> dict[str, object]:
    """A-priori card settings, the recorded DRY profile, or legacy defaults.

    See docs/engineering/step-10.md; never choose parameters from visit results.
    """
    if profile == "full-v0-card":
        if candidate is None:
            from auris.config import load_frozen_config

            candidate = load_frozen_config().extractor.candidate()
        card = candidate.card_sampling
        if card is None:
            raise ValueError("full-v0-card requires pinned card sampling")
        # The card selects these samplers a priori. Explicit neutral values
        # prevent unnamed server defaults from introducing another factor.
        return {
            # CardSampling defaults min_p/presence/repeat to neutral when unnamed.
            **card.model_dump(exclude={"source_repo", "revision", "readme_sha256", "mode"}),
            "frequency_penalty": 0.0,
            "repeat_last_n": 64,
            "dry_multiplier": 0.0,
            "xtc_probability": 0.0,
            "xtc_threshold": 1.0,
            "typical_p": 1.0,
            "top_n_sigma": -1.0,
            "dynatemp_range": 0.0,
            "mirostat": 0,
            "adaptive_target": -1.0,
            "samplers": ["penalties", "top_k", "top_p", "temperature"],
            "seed": 0 if seed is None else seed,
        }
    if profile != "full-v0-dry":
        return {} if seed is None else {"seed": seed}
    return {
        "dry_multiplier": 1.0,
        "dry_base": 1.75,
        "dry_allowed_length": 2,
        "dry_penalty_last_n": 64,
        "dry_sequence_breakers": ["\n", ":", '"', "*"],
        "samplers": [
            "penalties",
            "dry",
            "top_n_sigma",
            "top_k",
            "typ_p",
            "top_p",
            "min_p",
            "xtc",
            "temperature",
        ],
        "seed": 0 if seed is None else seed,
    }


def decoding_parameters(
    config: ExtractorConfig, candidate: ExtractorCandidate
) -> tuple[float, dict[str, object]]:
    sampling = sampling_parameters(config.extraction_profile, candidate, seed=config.seed)
    override = config.temperature_override
    temperature = config.temperature if override is None else override
    if config.extraction_profile == "full-v0-card":
        card_temperature = sampling.pop("temperature")
        if (override is not None and override != card_temperature) or (
            config.temperature != 0 and config.temperature != card_temperature
        ):
            raise ValueError("full-v0-card refuses a conflicting temperature override")
        temperature = float(card_temperature)
    if config.extraction_profile == "full-v0-dry" and temperature != 0:
        raise ValueError("full-v0-dry requires temperature 0")
    return temperature, sampling
