"""Record the selected wire contract, including order-sensitive schema identity."""

from typing import Any

from auris.assertions import extraction_json_schema
from auris.extract.profiles import ExtractionProfile, profile_contract
from auris.extract.prompt import prompt_sha256


def profile_identity(profile: ExtractionProfile) -> dict[str, Any]:
    contract = profile_contract(profile)
    schema = extraction_json_schema(contract.schema_version, property_order=contract.property_order)
    return {
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
