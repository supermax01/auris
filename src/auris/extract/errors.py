"""Safe-to-log transport failures; private bodies are separate attributes."""

from typing import Any


class ExtractionError(ValueError):
    """Never includes clinical response text in its message."""

    partial_response: dict[str, Any] | None = None


class ExtractionTimeoutError(ExtractionError):
    def __init__(self, message: str, *, phase: str = "request") -> None:
        super().__init__(message)
        self.phase = phase
        self.partial_response: dict[str, Any] | None = None


class LoopDetectedError(ExtractionError):
    def __init__(self, partial_response: dict[str, Any]) -> None:
        super().__init__("extractor repetition loop; no partial results")
        self.partial_response = partial_response
