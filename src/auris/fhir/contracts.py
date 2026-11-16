"""Read fixed values from task zero's checklist and unit sets from pinned R4."""

from __future__ import annotations

import copy
import json
import tarfile
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from auris.setup.fhir_packages import sha256_file

UnitSets = dict[str, frozenset[str]]
UNIT_SET_IDS = ("ucum-bodytemp", "ucum-bodyweight", "ucum-bodylength")


@lru_cache(maxsize=1)
def _profiles() -> dict[str, dict[str, Any]]:
    document = json.loads(files("auris.contract").joinpath("checklist.json").read_text())
    return {profile["id"]: profile for profile in document["profiles"]}


def profile_url(profile: str) -> str:
    return _profiles()[profile]["url"]


def element(profile: str, element_id: str) -> dict[str, Any]:
    return next(e for e in _profiles()[profile]["elements"] if e["id"] == element_id)


def fixed(profile: str, element_id: str) -> Any:
    definition = element(profile, element_id)
    return copy.deepcopy(definition.get("fixed", definition.get("pattern"))["value"])


def unit_sets_from_valuesets(valuesets: list[dict[str, Any]]) -> UnitSets:
    result = {}
    for value_set in valuesets:
        concepts = value_set["compose"]["include"]
        if len(concepts) != 1 or concepts[0]["system"] != "http://unitsofmeasure.org":
            raise ValueError("unexpected UCUM value set composition")
        result[value_set["id"]] = frozenset(item["code"] for item in concepts[0]["concept"])
    if set(result) != set(UNIT_SET_IDS):
        raise ValueError("all three R4 UCUM unit sets are required")
    return result


def load_unit_sets(core_package: Path, expected_sha256: str) -> UnitSets:
    """Read the actual closed unit sets; never download during mapping."""
    if sha256_file(core_package) != expected_sha256:
        raise ValueError("R4 package SHA-256 mismatch")
    with tarfile.open(core_package, "r:gz") as archive:
        valuesets = []
        for name in UNIT_SET_IDS:
            stream = archive.extractfile(f"package/ValueSet-{name}.json")
            if stream is None:
                raise ValueError("R4 UCUM value set missing")
            valuesets.append(json.load(stream))
    return unit_sets_from_valuesets(valuesets)
