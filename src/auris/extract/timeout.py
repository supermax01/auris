"""Configured throughput budgets; no network measurement or inference retries."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TimeoutBudget:
    max_tokens: int
    input_tokens: int
    generation_tokens_per_second: float = 20
    prompt_tokens_per_second: float = 100
    margin_multiplier: float = 1.5
    fixed_margin_seconds: float = 30

    @property
    def seconds(self) -> int:
        return math.ceil(
            self.margin_multiplier
            * (
                self.max_tokens / self.generation_tokens_per_second
                + self.input_tokens / self.prompt_tokens_per_second
            )
            + self.fixed_margin_seconds
        )

    @property
    def first_chunk_seconds(self) -> int:
        return math.ceil(
            self.margin_multiplier * self.input_tokens / self.prompt_tokens_per_second
            + self.fixed_margin_seconds
        )
