"""Record the selected wire contract, including order-sensitive schema identity."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from auris.config import ExtractorCandidate

from auris.assertions import extraction_json_schema
from auris.extract.grammar import GRAMMAR_VERSIONS, json_grammar
from auris.extract.profiles import ExtractionProfile, profile_contract, sampling_parameters
from auris.extract.prompt import WINDOW_INSTRUCTION, WINDOW_PROMPT_VERSION, prompt_sha256


def profile_identity(
    profile: ExtractionProfile,
    candidate: ExtractorCandidate | None = None,
    *,
    seed: int | None = None,
) -> dict[str, Any]:
    contract = profile_contract(profile)
    schema = extraction_json_schema(contract.schema_version, property_order=contract.property_order)
    identity = {
        "extraction_profile": profile,
        "schema_version": contract.schema_version,
        "prompt_version": contract.prompt_version,
        "prompt_sha256": prompt_sha256(profile),
        "window_prompt_version": WINDOW_PROMPT_VERSION,
        "window_instruction_sha256": hashlib.sha256(WINDOW_INSTRUCTION.encode()).hexdigest(),
        "schema_property_order": contract.property_order,
        "assertion_property_order": {
            variant["properties"]["kind"]["enum"][0]: list(variant["properties"])
            for variant in schema["properties"]["assertions"]["items"]["oneOf"]
        },
    }
    if sampling := sampling_parameters(profile, candidate, seed=seed):
        identity["sampling_parameters"] = sampling
    if profile == "full-v0-card":
        if candidate is None:
            from auris.config import load_frozen_config

            candidate = load_frozen_config().extractor.candidate()
        assert candidate.card_sampling is not None
        identity["card_sampling"] = candidate.card_sampling.model_dump(mode="json")
    if contract.grammar_whitespace is not None:
        identity.update(
            grammar_version=GRAMMAR_VERSIONS[contract.grammar_whitespace],
            grammar_sha256=hashlib.sha256(
                json_grammar(schema, whitespace=contract.grammar_whitespace).encode("utf-8")
            ).hexdigest(),
        )
    return identity
