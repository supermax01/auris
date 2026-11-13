"""Rendered input measurement and explicit offline estimates for context budgets."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from auris.extract.backend import Backend, ExtractionError
from auris.extract.profiles import ExtractionProfile

HEADROOM_TOKENS = 256
MEASURED_INPUT = "server_apply_template_tokenize"
ESTIMATED_INPUT = "conservative_utf8_chars"

# Provisional, deterministic sizing from the five PriMock development runs.
# Successful broad windows used up to ~51 output tokens/segment and ~1.1
# output tokens/transcript token. Reserve 56/segment or 1.25/transcript token,
# whichever is larger, plus JSON overhead. Short dense/repetitive completions
# can exceed this estimate: truncation splitting remains mandatory.
OUTPUT_TOKENS_PER_SEGMENT = 56
OUTPUT_RATIO_NUMERATOR = 5
OUTPUT_RATIO_DENOMINATOR = 4
OUTPUT_FIXED_TOKENS = 128
OUTPUT_ESTIMATE = {
    "version": "primock_dev_v1",
    "tokens_per_segment": OUTPUT_TOKENS_PER_SEGMENT,
    "tokens_per_transcript_token": OUTPUT_RATIO_NUMERATOR / OUTPUT_RATIO_DENOMINATOR,
    "fixed_tokens": OUTPUT_FIXED_TOKENS,
}


def estimate_output_tokens(transcript_tokens: int, segment_count: int) -> int:
    """Size output from transcript content, excluding fixed prompt/schema cost."""
    by_tokens = (
        OUTPUT_RATIO_NUMERATOR * transcript_tokens + OUTPUT_RATIO_DENOMINATOR - 1
    ) // OUTPUT_RATIO_DENOMINATOR
    return OUTPUT_FIXED_TOKENS + max(OUTPUT_TOKENS_PER_SEGMENT * segment_count, by_tokens)


def estimate_tokens(text: str) -> int:
    # Three ASCII chars/token; non-ASCII bytes each reserve a token. This is
    # explicitly an estimate, never a guarantee or permission for partial output.
    ascii_chars = sum(ord(char) < 128 for char in text)
    return math.ceil(ascii_chars / 3) + len(text.encode()) - ascii_chars


class TokenCounter:
    """Raw-text counts for prompt diagnostics and the A5 transcript window target."""

    def __init__(self, backend: Backend) -> None:
        self.backend = backend
        self.used_estimate = False
        self._cache: dict[str, int] = {}

    @property
    def method(self) -> str:
        return ESTIMATED_INPUT if self.used_estimate else "server_tokenize"

    def count(self, text: str) -> int:
        if text not in self._cache:
            measured = self.backend.tokenize(text)
            if measured is None:
                self.used_estimate = True
                measured = estimate_tokens(text)
            self._cache[text] = measured
        return self._cache[text]


@dataclass(frozen=True)
class InputTokens:
    tokens: int
    method: str


class InputTokenCounter:
    def __init__(
        self,
        backend: Backend,
        schema: dict[str, Any],
        *,
        profile: ExtractionProfile = "full-v0",
        grammar: str | None = None,
    ) -> None:
        self.backend = backend
        self.schema = schema
        self.profile = profile
        self.grammar = grammar
        self._cache: dict[str, InputTokens] = {}

    def count(self, messages: list[dict[str, str]]) -> InputTokens:
        key = json.dumps(messages, ensure_ascii=False)
        if key not in self._cache:
            measured = self.backend.count_input_tokens(
                messages,
                self.schema,
                **({"grammar": self.grammar} if self.grammar is not None else {}),
            )
            if measured is None:
                schema_text = json.dumps(self.schema, separators=(",", ":"), ensure_ascii=False)
                estimated = estimate_tokens(schema_text) + sum(
                    estimate_tokens(message["content"]) for message in messages
                )
                self._cache[key] = InputTokens(estimated, ESTIMATED_INPUT)
            else:
                if type(measured) is not int or measured <= 0:
                    raise ExtractionError("invalid rendered input token count")
                self._cache[key] = InputTokens(measured, MEASURED_INPUT)
        return self._cache[key]


def output_budget(input_tokens: int, *, n_ctx: int, max_output_tokens: int) -> int:
    return min(max_output_tokens, n_ctx - input_tokens - HEADROOM_TOKENS)


def compare_input_usage(count: InputTokens, usage: Any) -> dict[str, Any]:
    actual = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    if type(actual) is not int or actual <= 0:
        actual = None
    measured = count.tokens if count.method == MEASURED_INPUT else None
    difference = actual - measured if actual is not None and measured is not None else None
    if measured is None:
        status = "estimate_only"
    elif actual is None:
        status = "usage_unavailable"
    else:
        status = "match" if difference == 0 else "mismatch"
    return {
        "status": status,
        "tokenizer_prompt_tokens": measured,
        "usage_prompt_tokens": actual,
        "difference": difference,
    }
