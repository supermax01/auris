"""Versioned extraction contracts; the full profile remains the default."""

from dataclasses import dataclass
from typing import Literal

ExtractionProfile = Literal["full-v0", "lean-v1"]
SchemaVersion = Literal["1", "2"]
PROFILES: tuple[ExtractionProfile, ...] = ("full-v0", "lean-v1")


@dataclass(frozen=True)
class Profile:
    schema_version: SchemaVersion
    prompt_version: str


def profile_contract(profile: ExtractionProfile = "full-v0") -> Profile:
    if profile == "full-v0":
        return Profile("1", "extraction_v3")
    if profile == "lean-v1":
        return Profile("2", "extraction_v4")
    raise ValueError("unsupported extraction profile")
