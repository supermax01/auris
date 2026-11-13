"""Versioned extraction contracts; the full profile remains the default."""

from dataclasses import dataclass
from typing import Literal

ExtractionProfile = Literal[
    "full-v0", "lean-v1", "abl-optional", "abl-quotes", "abl-compact", "abl-order"
]
SchemaVersion = Literal["1", "2"]
PropertyOrder = Literal["canonical", "evidence-last"]
PROFILES: tuple[ExtractionProfile, ...] = (
    "full-v0",
    "lean-v1",
    "abl-optional",
    "abl-quotes",
    "abl-compact",
    "abl-order",
)


@dataclass(frozen=True)
class Profile:
    schema_version: SchemaVersion
    prompt_version: str
    property_order: PropertyOrder = "canonical"
    compact_json: bool = False


_CONTRACTS: dict[ExtractionProfile, Profile] = {
    "full-v0": Profile("1", "extraction_v3"),
    "lean-v1": Profile("2", "extraction_v4", compact_json=True),
    "abl-optional": Profile("2", "extraction_abl_optional_v1"),
    "abl-quotes": Profile("1", "extraction_abl_quotes_v1"),
    "abl-compact": Profile("1", "extraction_abl_compact_v1", compact_json=True),
    "abl-order": Profile("2", "extraction_v4", "evidence-last", compact_json=True),
}
PROMPT_VERSIONS = ("extraction_v1", "extraction_v2") + tuple(
    dict.fromkeys(contract.prompt_version for contract in _CONTRACTS.values())
)


def profile_contract(profile: ExtractionProfile = "full-v0") -> Profile:
    try:
        return _CONTRACTS[profile]
    except KeyError:
        raise ValueError("unsupported extraction profile") from None
