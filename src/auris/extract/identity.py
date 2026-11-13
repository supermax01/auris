"""Record the selected wire contract, including order-sensitive schema identity."""

import hashlib
from typing import Any

from auris.assertions import extraction_json_schema
from auris.extract.grammar import GRAMMAR_VERSIONS, json_grammar
from auris.extract.profiles import ExtractionProfile, profile_contract
from auris.extract.prompt import prompt_sha256


def profile_identity(profile: ExtractionProfile) -> dict[str, Any]:
    contract = profile_contract(profile)
    schema = extraction_json_schema(contract.schema_version, property_order=contract.property_order)
    identity = {
        "extraction_profile": profile,
        "schema_version": contract.schema_version,
        "prompt_version": contract.prompt_version,
        "prompt_sha256": prompt_sha256(profile),
        "schema_property_order": contract.property_order,
        "assertion_property_order": {
            variant["properties"]["kind"]["enum"][0]: list(variant["properties"])
            for variant in schema["properties"]["assertions"]["items"]["oneOf"]
        },
    }
    if contract.grammar_whitespace is not None:
        identity.update(
            grammar_version=GRAMMAR_VERSIONS[contract.grammar_whitespace],
            grammar_sha256=hashlib.sha256(
                json_grammar(schema, whitespace=contract.grammar_whitespace).encode("utf-8")
            ).hexdigest(),
        )
    return identity
