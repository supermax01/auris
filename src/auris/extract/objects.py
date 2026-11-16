"""Incremental, string-aware assertion boundaries; never a schema validator."""

import json
from collections import Counter, defaultdict
from typing import Any


class AssertionScanner:
    """Recognize complete direct objects in the root assertions array.

    Incomplete tails are ignored. Strings, escaped delimiters and nested evidence
    objects cannot terminate an assertion. This scanner grants no acceptance.
    """

    def __init__(self) -> None:
        self.text = ""
        self.stack: list[str] = []
        self.quoted = False
        self.escaped = False
        self.string_start = 0
        self.root_key: str | None = None
        self.in_assertions = False
        self.object_start: int | None = None
        self.counts: Counter[tuple] = Counter()
        self.polarities: dict[tuple, set[str | None]] = defaultdict(set)
        self.complete_assertions_seen = 0

    def feed(
        self, content: str, *, repeat_limit: int | None = None
    ) -> list[tuple[dict[str, Any], int]]:
        offset = len(self.text)
        self.text += content
        completed = []
        for index, char in enumerate(content, offset):
            if self.quoted:
                if self.escaped:
                    self.escaped = False
                elif char == "\\":
                    self.escaped = True
                elif char == '"':
                    self.quoted = False
                    if self.stack == ["{"]:
                        try:
                            self.root_key = json.loads(self.text[self.string_start : index + 1])
                        except ValueError:
                            self.root_key = None
                continue
            if char == '"':
                self.quoted = True
                self.string_start = index
            elif char in "[{":
                if self.stack == ["{"] and char == "[":
                    self.in_assertions = self.root_key == "assertions"
                if self.in_assertions and self.stack == ["{", "["] and char == "{":
                    self.object_start = index
                self.stack.append(char)
            elif char in "]}":
                if not self.stack or self.stack[-1] != ("[" if char == "]" else "{"):
                    continue  # Invalid JSON is rejected by the document parser.
                self.stack.pop()
                if self.object_start is not None and self.stack == ["{", "["]:
                    raw = self.text[self.object_start : index + 1]
                    self.object_start = None
                    try:
                        item = json.loads(raw)
                    except ValueError:
                        continue
                    self.complete_assertions_seen += 1
                    key = assertion_key(item)
                    if key is not None:
                        self.counts[key] += 1
                        self.polarities[key[:2] + key[3:]].add(key[2])
                    completed.append((item, index + 1))
                    if (
                        key is not None
                        and repeat_limit is not None
                        and self.counts[key] >= repeat_limit
                    ):
                        break
        return completed

    @property
    def metrics(self) -> dict[str, int]:
        return {
            "complete_assertions_seen": self.complete_assertions_seen,
            "max_identical_assertion_repeats": max(self.counts.values(), default=0),
            "duplicate_assertions": sum(n - 1 for n in self.counts.values()),
            "polarity_flip_pairs": sum(
                len(p) * (len(p) - 1) // 2 for p in self.polarities.values()
            ),
        }


def assertion_key(item: object) -> tuple | None:
    if not isinstance(item, dict):
        return None
    kind, concept, polarity = (item.get(k) for k in ("kind", "concept_text", "polarity"))
    evidence = item.get("evidence")
    if (
        not isinstance(kind, str)
        or not (concept is None or isinstance(concept, str))
        or "concept_text" not in item
        or polarity not in ("affirmed", "negated", None)
        or "polarity" not in item
        or not isinstance(evidence, list)
        or not evidence
    ):
        return None
    pairs = []
    for quote in evidence:
        if not isinstance(quote, dict) or any(
            not isinstance(quote.get(k), str) for k in ("segment_id", "quote")
        ):
            return None
        pairs.append((quote["segment_id"], quote["quote"]))
    return (
        kind,
        " ".join(concept.casefold().split()) if concept is not None else None,
        polarity,
        tuple(sorted(pairs)),
    )
