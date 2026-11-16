"""Pinned llama-server SSE reassembly with absolute and between-event deadlines."""

import json
import time
from typing import Any, BinaryIO

from auris.extract.errors import ExtractionError, ExtractionTimeoutError, LoopDetectedError
from auris.extract.objects import AssertionScanner

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
IDLE_SECONDS = 60.0


class StreamAssembler:
    def __init__(self, identical_assertion_limit: int = 4) -> None:
        self.scanner = AssertionScanner()
        self.limit = identical_assertion_limit
        self.content_chunks = 0
        self.reasoning = ""
        self.finish_reason: str | None = None
        self.usage: dict[str, Any] | None = None
        self.timings: dict[str, Any] | None = None
        self.done = False

    def feed(self, data: str) -> None:
        if data == "[DONE]":
            self.done = True
            return
        try:
            event = json.loads(data)
            choices = event["choices"]
            if not isinstance(choices, list) or len(choices) > 1:
                raise ValueError
            if choices:
                choice = choices[0]
                if choice.get("index", 0) != 0:
                    raise ValueError
                delta = choice.get("delta", {})
                content = delta.get("content")
                if content is not None:
                    if not isinstance(content, str):
                        raise ValueError
                    # Count received content events, including empty strings.
                    # This excludes server-only tokens: the captured sample has
                    # 117 content events versus usage.completion_tokens=120.
                    self.content_chunks += 1
                    self.scanner.feed(content, repeat_limit=self.limit)
                reasoning = delta.get("reasoning_content")
                if reasoning:
                    if not isinstance(reasoning, str):
                        raise ValueError
                    self.reasoning += reasoning
                finish = choice.get("finish_reason")
                if finish is not None:
                    self.finish_reason = finish
            if event.get("usage") is not None:
                self.usage = event["usage"]
            if event.get("timings") is not None:
                self.timings = event["timings"]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ExtractionError("invalid local extractor SSE event") from None
        # Reasoning is a hard refusal, never a split retry, even during a loop.
        if self.reasoning:
            raise ExtractionError("extractor returned reasoning despite thinking-off configuration")
        if self.scanner.metrics["max_identical_assertion_repeats"] >= self.limit:
            raise LoopDetectedError(self.response(stopped=True))

    def response(self, *, stopped: bool = False) -> dict[str, Any]:
        message = {"role": "assistant", "content": self.scanner.text}
        if self.reasoning:
            message["reasoning_content"] = self.reasoning
        result: dict[str, Any] = {
            "choices": [{"message": message, "finish_reason": self.finish_reason}],
        }
        if self.usage is not None and not stopped:
            result["usage"] = self.usage
        if self.timings is not None:
            result["timings"] = self.timings
        if stopped:
            result["usage"] = {"completion_tokens": self.content_chunks}
            result["output_token_count_method"] = "received_content_chunks"
        return result


def read_stream(
    response: BinaryIO,
    *,
    started: float,
    timeout: float,
    first_chunk_timeout: float,
    identical_assertion_limit: int,
    idle_seconds: float = IDLE_SECONDS,
) -> dict[str, Any]:
    assembler = StreamAssembler(identical_assertion_limit)
    total_deadline = started + timeout
    chunk_deadline = started + first_chunk_timeout
    phase = "first_chunk"
    buffer = b""
    data_lines: list[str] = []
    received = 0

    def allowance() -> float:
        now = time.monotonic()
        if now >= total_deadline:
            raise ExtractionTimeoutError("local extractor total stream timed out", phase="total")
        if now >= chunk_deadline:
            raise ExtractionTimeoutError(f"local extractor {phase} stream timed out", phase=phase)
        return min(total_deadline, chunk_deadline) - now

    try:
        while not assembler.done:
            remaining = allowance()
            # HTTPResponse.read1 performs at most one underlying buffered read,
            # so a peer trickling an unterminated event cannot reset a deadline.
            # urllib exposes the connected socket through this pinned path.
            sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
            if sock is not None:
                sock.settimeout(remaining)
            try:
                chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - received))
            except TimeoutError:
                allowance()
                timed_phase = "total" if total_deadline <= chunk_deadline else phase
                raise ExtractionTimeoutError(
                    f"local extractor {timed_phase} stream timed out", phase=timed_phase
                ) from None
            allowance()
            if not chunk:
                raise ExtractionError("local extractor SSE ended before DONE; no partial results")
            received += len(chunk)
            if received > MAX_RESPONSE_BYTES:
                raise ExtractionError("extractor response exceeds size limit")
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.rstrip(b"\r")
                if not line:
                    if data_lines:
                        assembler.feed("\n".join(data_lines))
                        data_lines.clear()
                        phase = "idle"
                        chunk_deadline = time.monotonic() + idle_seconds
                elif line.startswith(b"data:"):
                    data_lines.append(line[5:].removeprefix(b" ").decode("utf-8"))
        allowance()
        if assembler.finish_reason is None:
            raise ExtractionError("local extractor SSE has no finish reason; no partial results")
        return assembler.response()
    except ExtractionError as exc:
        if not isinstance(exc, LoopDetectedError):
            exc.partial_response = assembler.response(stopped=True)
        raise
    except UnicodeError:
        raise ExtractionError("invalid local extractor SSE encoding") from None
