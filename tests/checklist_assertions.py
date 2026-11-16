"""Independent task-zero checklist assertions for every mapped test resource."""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
DOCUMENT = json.loads((ROOT / "src/auris/contract/checklist.json").read_text())
SOURCES = tomllib.loads((ROOT / "src/auris/contract/element_sources.toml").read_text())["profiles"]
PROFILES = {profile["url"]: profile for profile in DOCUMENT["profiles"]}


def matches(actual, pattern):
    if isinstance(pattern, dict):
        return isinstance(actual, dict) and all(
            k in actual and matches(actual[k], v) for k, v in pattern.items()
        )
    if isinstance(pattern, list):
        return isinstance(actual, list) and all(any(matches(a, p) for a in actual) for p in pattern)
    return actual == pattern


def values(parent, name):
    if not isinstance(parent, dict):
        return []
    if "[x]" in name:
        prefix = name.replace("[x]", "")
        found = [
            value
            for key, value in parent.items()
            if key.startswith(prefix) and key[len(prefix) : len(prefix) + 1].isupper()
        ]
    else:
        found = [parent[name]] if name in parent else []
    return [item for value in found for item in (value if isinstance(value, list) else [value])]


def selected(parent, step, path, elements):
    name, _, slice_name = step.partition(":")
    if "[x]" in name and slice_name:
        return values(parent, slice_name)
    found = values(parent, name)
    if not slice_name:
        return found
    # Derive slice selection from its direct or descendant pattern/fixed values.
    constraints = [
        e
        for e in elements
        if (e["id"] == path or e["id"].startswith(path + ".")) and ("fixed" in e or "pattern" in e)
    ]

    def in_slice(item):
        for constraint in constraints:
            candidates = [item]
            for part in constraint["id"][len(path) :].strip(".").split("."):
                if part:
                    candidates = [
                        child for c in candidates for child in values(c, part.split(":")[0])
                    ]
            expected = constraint.get("fixed", constraint.get("pattern"))["value"]
            if not any(matches(c, expected) for c in candidates):
                return False
        return True

    return [item for item in found if in_slice(item)]


def check_resource(resource, profile=None):
    profile = profile or PROFILES[resource["meta"]["profile"][0]]
    sources = SOURCES[profile["id"]]["elements"]
    for element in profile["elements"]:
        if sources.get(element["id"], {}).get("source") == "not-emitted":
            continue
        parents = [resource]
        parts = element["id"].split(".")
        for index, step in enumerate(parts[1:-1], start=1):
            parents = [
                child
                for parent in parents
                for child in selected(
                    parent, step, ".".join(parts[: index + 1]), profile["elements"]
                )
            ]
        if len(parts) == 1:
            continue
        # A required descendant applies only when its optional parent exists.
        # Required parents themselves are checked on their own checklist rows.
        for parent in parents:
            found = selected(parent, parts[-1], element["id"], profile["elements"])
            assert len(found) >= element["min"], element["id"]
            if element["max"] != "*":
                assert len(found) <= int(element["max"]), element["id"]
            for value in found:
                if "fixed" in element:
                    assert value == element["fixed"]["value"], element["id"]
                if "pattern" in element:
                    assert matches(value, element["pattern"]["value"]), element["id"]
