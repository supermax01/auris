"""Versioned extraction contracts; the full profile remains the default."""

from dataclasses import dataclass
from typing import Literal

ExtractionProfile = Literal[
    "full-v0",
    "full-v0-dry",
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


def sampling_parameters(profile: ExtractionProfile) -> dict[str, object]:
    """Pre-registered unit-strength DRY, otherwise the pinned server defaults.

    See docs/engineering/step-9.md; never choose parameters from visit results.
    """
    if profile != "full-v0-dry":
        return {}
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
        "seed": 0,
    }
