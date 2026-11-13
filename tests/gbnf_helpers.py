"""Independent interpreter for emitted GBNF, using Python's regex automaton.

Reads only grammar text, never the originating schema or converter helpers.
Supports literals, classes, references, grouping, alternation and repetition.
"""

import json
import re
from functools import cache

TOKEN = re.compile(
    r'"(?:\\.|[^"\\])*"|\[(?:\\.|[^\]\\])*\]|[a-zA-Z][a-zA-Z0-9-]*|\{\d+,\d*\}|[()|?*+]'
)


def grammar_pattern(grammar: str) -> re.Pattern[str]:
    rules = dict(line.split(" ::= ", 1) for line in grammar.splitlines())

    @cache
    def expand(name: str) -> str:
        expression = rules[name]
        tokens = []
        position = 0
        for match in TOKEN.finditer(expression):
            assert not expression[position : match.start()].strip(), "unknown GBNF syntax"
            tokens.append(match.group())
            position = match.end()
        assert not expression[position:].strip(), "unparsed GBNF suffix"
        parts = []
        for token in tokens:
            if token.startswith('"'):
                parts.append(re.escape(json.loads(token)))
            elif token.startswith("[") or token[0] in "()|?*+{":
                parts.append(token)
            else:
                parts.append(f"(?:{expand(token)})")
        return "".join(parts)

    return re.compile(expand("root"))
