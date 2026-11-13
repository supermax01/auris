"""Deterministic compact GBNF for the exported extraction-schema subset.

Only JSON string values may contain whitespace. Object members follow schema
order; optional members may be absent, including before/between required ones.
This constrains structure, not the parser's additional completeness/evidence
checks. Unsupported schema constraints fail rather than silently disappearing.
"""

from __future__ import annotations

import json
import re
from typing import Any

from auris.assertions import check_schema_subset

GRAMMAR_VERSION = "compact-gbnf-v1"


def _literal(text: str) -> str:
    return json.dumps(text, ensure_ascii=True)


def _repeat(rule: str, minimum: int, maximum: int | None) -> str:
    if maximum == 0:
        return '""'
    if maximum is None:
        return f"{rule}{{{minimum},}}"
    return f"{rule}{{{minimum},{maximum}}}"


class _Converter:
    def __init__(self) -> None:
        self.rules: dict[str, str] = {"root": "", "ws": '""'}
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
        """Accept equivalent JSON escapes in fixed keys and enum values too."""
        characters: list[str] = []
        for char in text:
            encoded = json.dumps(char, ensure_ascii=True)[1:-1]
            if not encoded.startswith("\\u"):
                encoded = f"\\u{ord(char):04x}"
            # JSON's 'u' is lowercase, but hex digits are case insensitive.
            escaped = " ".join(
                _literal("\\u")
                if part == "\\u"
                else f"[{part}{part.upper()}]"
                if part in "abcdef"
                else _literal(part)
                for part in re.findall(r"\\u|.", encoded)
            )
            choices = [_literal(json.dumps(char, ensure_ascii=False)[1:-1]), escaped]
            if char == "/":
                choices.append(_literal("\\/"))
            characters.append(self.rule(" | ".join(choices)))
        return self.rule(" ".join([_literal('"'), *characters, _literal('"')]))

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
                        present = f"{prefix}{field} {tail(index + 1, True)}"
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
                    rest = _repeat(f'(ws "," ws {item})', max(0, minimum - 1), maximum - 1)
                    contents = f"{item} {rest}"
                    if minimum == 0:
                        contents = f"({contents})?"
                return self.rule(f'"[" ws {contents} ws "]"')
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


def compact_grammar(schema: dict[str, Any]) -> str:
    """Export GBNF v1; no whitespace outside strings, including at EOF."""
    if check_schema_subset(schema):
        raise ValueError("schema is outside the constrained-decoding subset")
    converter = _Converter()
    converter.rules["root"] = converter.convert(schema)
    return "".join(f"{name} ::= {expression}\n" for name, expression in converter.rules.items())
