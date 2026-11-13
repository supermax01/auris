"""Deterministic JSON GBNF for the exported extraction-schema subset.

Inter-token whitespace is empty or at most one newline, per profile. Fixed keys
and enums use literal spellings; free string values retain JSON escapes.
Object members follow schema order; optional members may be absent, including
before/between required ones.
This constrains structure, not the parser's additional completeness/evidence
checks. Unsupported schema constraints fail rather than silently disappearing.
"""

from __future__ import annotations

import json
from typing import Any

from auris.assertions import check_schema_subset
from auris.extract.profiles import GrammarWhitespace

GRAMMAR_VERSIONS: dict[GrammarWhitespace, str] = {
    "compact": "compact-gbnf-v2",
    "newline": "newline-gbnf-v1",
}


def _literal(text: str) -> str:
    return json.dumps(text, ensure_ascii=True)


def _repeat(rule: str, minimum: int, maximum: int | None) -> str:
    if maximum == 0:
        return '""'
    if maximum is None:
        return f"{rule}{{{minimum},}}"
    return f"{rule}{{{minimum},{maximum}}}"


class _Converter:
    def __init__(self, whitespace: GrammarWhitespace) -> None:
        self.rules: dict[str, str] = {
            "root": "",
            "ws": r'"\n"?' if whitespace == "newline" else '""',
        }
        # One char means one decoded Unicode scalar, including surrogate pairs.
        # Raw controls, raw surrogates and unpaired escaped surrogates are invalid.
        self.rules["hex"] = "[0-9a-fA-F]"
        self.rules["char"] = (
            r'[^"\\\x00-\x1f\uD800-\uDFFF] | "\\" '
            r'(["\\/bfnrt] | "u" ([0-9a-cA-Ce-fE-F] hex hex hex | [dD] [0-7] hex hex) '
            r'| "u" [dD] [89aAbB] hex hex "\\u" [dD] [c-fC-F] hex hex)'
        )
        self.cache: dict[str, str] = {}

    def rule(self, expression: str) -> str:
        # Reusing identical expressions keeps the grammar small without losing
        # order (sort_keys would erase property-order identity).
        if expression not in self.cache:
            name = f"r{len(self.cache)}"
            self.cache[expression] = name
            self.rules[name] = expression
        return self.cache[expression]

    def fixed_string(self, text: str) -> str:
        """Emit exact JSON string bytes, with no escaped alternative spellings."""
        if any(char in '"\\' or ord(char) < 0x20 or 0xD800 <= ord(char) <= 0xDFFF for char in text):
            raise ValueError("fixed JSON strings must be representable without escapes")
        return self.rule(_literal('"' + text + '"'))

    def convert(self, node: dict[str, Any]) -> str:
        annotations = {"title", "description", "default"}
        supported = (
            {"anyOf"}
            if "anyOf" in node
            else {"oneOf"}
            if "oneOf" in node
            else {"type", "enum"}
            if "enum" in node
            else {
                "object": {"type", "properties", "required", "additionalProperties"},
                "array": {"type", "items", "minItems", "maxItems"},
                "string": {"type", "minLength", "maxLength"},
                "null": {"type"},
            }.get(node.get("type"), set())
        )
        if node.keys() - annotations - supported:
            raise ValueError("unsupported compact-grammar schema keyword")
        if "anyOf" in node:
            return self.rule(f'{self.convert(node["anyOf"][0])} | "null"')
        if "oneOf" in node:
            variants = node["oneOf"]
            kinds = [v.get("properties", {}).get("kind", {}).get("enum", []) for v in variants]
            if (
                not kinds
                or any(len(k) != 1 for k in kinds)
                or len({k[0] for k in kinds}) != len(kinds)
                or any("kind" not in variant.get("required", []) for variant in variants)
            ):
                raise ValueError("compact grammar requires disjoint singleton-kind variants")
            return self.rule(" | ".join(self.convert(v) for v in variants))
        if "enum" in node:
            values = node["enum"]
            if (
                node.get("type") != "string"
                or not values
                or any(type(v) is not str for v in values)
            ):
                raise ValueError("compact grammar requires nonempty string enums")
            return self.rule(" | ".join(self.fixed_string(v) for v in values))
        match node.get("type"):
            case "object":
                if node.get("additionalProperties") is not False:
                    raise ValueError("compact grammar requires closed objects")
                properties = node["properties"]
                required = set(node.get("required", []))
                if required - properties.keys():
                    raise ValueError("required compact-grammar property is undefined")
                fields = [
                    (key, f'{self.fixed_string(key)} ws ":" ws {self.convert(value)}')
                    for key, value in properties.items()
                ]
                # Two suffix states track whether a preceding member exists.
                # This permits every optional-key combination in linear space.
                tails: dict[tuple[int, bool], str] = {}

                def tail(index: int, seen: bool) -> str:
                    if index == len(fields):
                        return '""'
                    if (index, seen) not in tails:
                        key, field = fields[index]
                        prefix = '"," ws ' if seen else ""
                        # Each present member owns its trailing whitespace;
                        # omitted members add none, including in empty objects.
                        present = f"{prefix}{field} ws {tail(index + 1, True)}"
                        expression = (
                            present if key in required else f"{present} | {tail(index + 1, seen)}"
                        )
                        tails[index, seen] = self.rule(expression)
                    return tails[index, seen]

                return self.rule(f'"{{" ws {tail(0, False)} "}}"')
            case "array":
                minimum, maximum = node.get("minItems", 0), node["maxItems"]
                self.check_bounds(minimum, maximum)
                item = self.convert(node["items"])
                if maximum == 0:
                    contents = '""'
                else:
                    rest = _repeat(f'("," ws {item} ws)', max(0, minimum - 1), maximum - 1)
                    contents = f"{item} ws {rest}"
                    if minimum == 0:
                        contents = f"({contents})?"
                return self.rule(f'"[" ws {contents} "]"')
            case "string":
                minimum, maximum = node.get("minLength", 0), node.get("maxLength")
                self.check_bounds(minimum, maximum)
                return self.rule(
                    f"{_literal(chr(34))} {_repeat('char', minimum, maximum)} {_literal(chr(34))}"
                )
            case "null":
                return self.rule('"null"')
            case _:
                raise ValueError("unsupported compact-grammar schema type")

    @staticmethod
    def check_bounds(minimum: int, maximum: int | None) -> None:
        if (
            type(minimum) is not int
            or minimum < 0
            or (maximum is not None and (type(maximum) is not int or maximum < minimum))
        ):
            raise ValueError("invalid compact-grammar bounds")


def json_grammar(schema: dict[str, Any], *, whitespace: GrammarWhitespace) -> str:
    """Export versioned GBNF with a single whitespace slot at every JSON gap."""
    if whitespace not in GRAMMAR_VERSIONS:
        raise ValueError("unsupported grammar whitespace mode")
    if check_schema_subset(schema):
        raise ValueError("schema is outside the constrained-decoding subset")
    converter = _Converter(whitespace)
    converter.rules["root"] = f"ws {converter.convert(schema)} ws"
    return "".join(f"{name} ::= {expression}\n" for name, expression in converter.rules.items())


def compact_grammar(schema: dict[str, Any]) -> str:
    """Export corrected compact GBNF; no whitespace outside strings."""
    return json_grammar(schema, whitespace="compact")


def newline_grammar(schema: dict[str, Any]) -> str:
    """Export GBNF allowing zero or one newline at each inter-token gap."""
    return json_grammar(schema, whitespace="newline")
