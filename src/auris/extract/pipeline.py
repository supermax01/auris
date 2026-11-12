"""Extraction, fail-closed parsing, mapping, validation and private artifacts."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auris._storage import prepare_output_directory, write_private_text
from auris.assertions import MAX_ASSERTIONS, AssertionBase, ExtractionResult, extraction_json_schema
from auris.config import ExtractorCandidate, ExtractorConfig
from auris.extract.backend import Backend, ExtractionError, ManagedLlamaServer, RuntimeMetadata
from auris.extract.chunking import (
    Chunk,
    measure_chunk,
    merge_assertions,
    plan_chunks,
    split_segments,
)
from auris.extract.prompt import PROMPT_VERSION, messages, prompt_sha256, prompt_text
from auris.extract.tokens import (
    HEADROOM_TOKENS,
    InputTokenCounter,
    InputTokens,
    TokenCounter,
    compare_input_usage,
    output_budget,
)
from auris.fhir import Mapper
from auris.fhir.models import LaneState, MappingOutcome
from auris.transcript import Transcript
from auris.validation.models import ValidationRun
from auris.validation.report import validate_mappings, write_validation
from auris.validation.runner import ValidatorRunner


class SchemaResponseError(ExtractionError):
    """The reduced-schema checker ran and rejected the response."""


class TruncatedCompletionError(ExtractionError):
    """A length-limited attempt contributes no assertions, even if valid JSON."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SchemaResponseError("schema-invalid extraction response (duplicate JSON key)")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise SchemaResponseError("schema-invalid extraction response (non-finite JSON number)")


def parse_completion(response: dict[str, Any]) -> ExtractionResult:
    try:
        choices = response["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ExtractionError("extractor must return exactly one completion")
        choice = choices[0]
        if choice["finish_reason"] == "length":
            raise TruncatedCompletionError(
                "extraction truncated (finish_reason=length); no partial results"
            )
        if choice["finish_reason"] != "stop":
            raise ExtractionError("extractor did not finish normally; no partial results")
        if choice["message"].get("reasoning_content"):
            raise ExtractionError("extractor returned reasoning despite thinking-off configuration")
        content = choice["message"]["content"]
        if not isinstance(content, str):
            raise ExtractionError("extractor message content is not JSON text")
        # Check duplicates before strict pydantic JSON parsing; Python's default
        # JSON parser would silently retain the last clinical field value.
        json.loads(
            content,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        result = ExtractionResult.model_validate_json(content, strict=True)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ExtractionError):
            raise
        raise SchemaResponseError(
            "schema-invalid extraction response; no partial results"
        ) from None
    if len(result.assertions) == MAX_ASSERTIONS:
        raise ExtractionError("extraction reached assertion bound; completeness cannot be assured")
    return result


@dataclass
class ExtractionRun:
    model: ExtractorCandidate
    decoding: dict[str, Any]
    transcript_sha256: str
    runtime: RuntimeMetadata
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    prompt_tokens: int | None = None
    prompt_token_count_method: str | None = None
    assertions: tuple[AssertionBase, ...] = ()
    conflict_positions: frozenset[int] = frozenset()
    calls: list[dict[str, Any]] = field(default_factory=list)
    splits: list[dict[str, Any]] = field(default_factory=list)
    window_reuses: list[dict[str, str]] = field(default_factory=list)
    wall_seconds: float = 0
    failure: str | None = None
    reduced_schema_state: LaneState = LaneState.NOT_CHECKED

    def document(self) -> dict[str, Any]:
        return {
            "status": "failed" if self.failure else "complete",
            "failure": self.failure,
            "reduced_schema_state": self.reduced_schema_state.value,
            "assertions": [a.model_dump(mode="json") for a in self.assertions],
            "merge_conflict_positions": sorted(self.conflict_positions),
            "calls": self.calls,
            "metadata": {
                "model_id": self.model.id,
                "run_timestamp": self.started_at,
                "model_repo": self.model.repo,
                "model_license": self.model.license,
                "model_sha256": self.model.sha256,
                "model_revision": self.model.revision,
                "quantization": self.model.quantization,
                "runtime_flags": self.model.runtime_flags,
                "runtime": asdict(self.runtime),
                "prompt_version": PROMPT_VERSION,
                "prompt_sha256": prompt_sha256(),
                "prompt_tokens": self.prompt_tokens,
                "prompt_token_count_method": self.prompt_token_count_method,
                "decoding": self.decoding,
                "transcript_sha256": self.transcript_sha256,
                "wall_seconds": self.wall_seconds,
                "token_usage": [call.get("usage") for call in self.calls],
                "input_token_comparisons": [
                    call.get("input_token_comparison") for call in self.calls
                ],
                "splits": self.splits,
                "window_reuses": self.window_reuses,
            },
        }


def extract(
    backend: Backend,
    transcript: Transcript,
    config: ExtractorConfig,
    model: ExtractorCandidate,
) -> ExtractionRun:
    started = time.monotonic()
    run = ExtractionRun(
        model=model,
        decoding={
            "temperature": config.temperature,
            "max_output_tokens": config.max_output_tokens,
            "min_output_tokens": config.min_output_tokens,
            "headroom_tokens": HEADROOM_TOKENS,
            "n_ctx": config.n_ctx,
            "thinking": config.thinking,
        },
        transcript_sha256=transcript.content_hash,
        runtime=backend.runtime,
    )
    try:
        counter = TokenCounter(backend)
        run.prompt_tokens = counter.count(prompt_text())
        run.prompt_token_count_method = counter.method
        schema = extraction_json_schema()
        input_counter = InputTokenCounter(backend, schema)
        chunks = plan_chunks(
            transcript,
            schema,
            backend,
            n_ctx=config.n_ctx,
            min_output_tokens=config.min_output_tokens,
            input_counter=input_counter,
        )
        groups: list[tuple[AssertionBase, ...]] = []
        resolved: dict[tuple[str, ...], str] = {}

        def split_window(chunk: Chunk, window_id: str, reason: str) -> None:
            if len(chunk.segments) == 1:
                if reason == "finish_reason=length":
                    raise ExtractionError(
                        "single transcript segment still truncated (finish_reason=length); "
                        "no partial results"
                    )
                raise ExtractionError("one transcript segment exceeds the available context budget")
            halves = split_segments(chunk.segments)
            children = [f"{window_id}.{i}" for i in range(2)]
            left_ids = {segment.id for segment in halves[0]}
            run.splits.append(
                {
                    "window_id": window_id,
                    "reason": reason,
                    "segment_ids": [s.id for s in chunk.segments],
                    "child_window_ids": children,
                    "child_segment_ids": [[s.id for s in half] for half in halves],
                    "overlap_segment_ids": [s.id for s in halves[1] if s.id in left_ids],
                }
            )
            for child_id, half in zip(children, halves, strict=True):
                run_window(measure_chunk(half, input_counter), child_id)

        def run_window(chunk: Chunk, window_id: str) -> None:
            key = tuple(segment.id for segment in chunk.segments)
            if key in resolved:
                # Small overlapping split trees can revisit the same window.
                # Its successful work is already in groups; never infer twice.
                run.window_reuses.append(
                    {"window_id": window_id, "source_window_id": resolved[key]}
                )
                return
            max_tokens = output_budget(
                chunk.input_tokens, n_ctx=config.n_ctx, max_output_tokens=config.max_output_tokens
            )
            if max_tokens < config.min_output_tokens:
                split_window(chunk, window_id, "output_below_floor")
                resolved[key] = window_id
                return
            call: dict[str, Any] = {
                "window_id": window_id,
                "segment_ids": [s.id for s in chunk.segments],
                "reserved_input_tokens": chunk.input_tokens,
                "token_count_method": chunk.token_count_method,
                "max_tokens": max_tokens,
            }
            run.calls.append(call)
            call_started = time.monotonic()
            truncated = False
            try:
                response = backend.complete(
                    messages(chunk.segments),
                    schema,
                    temperature=config.temperature,
                    max_tokens=max_tokens,
                )
                call["raw_response"] = response
                call["usage"] = response.get("usage")
                comparison = compare_input_usage(
                    InputTokens(chunk.input_tokens, chunk.token_count_method), call["usage"]
                )
                call["input_token_comparison"] = comparison
                result = parse_completion(response)
                allowed = {s.id for s in chunk.segments}
                if any(
                    item.segment_id not in allowed for a in result.assertions for item in a.evidence
                ):
                    raise ExtractionError("extraction cites a segment outside its input window")
                call["parsed_assertions"] = result.model_dump(mode="json")["assertions"]
                groups.append(result.assertions)
            except TruncatedCompletionError:
                # Discard the entire generated body, including syntactically
                # complete JSON. Only usage, timing and split metadata survive.
                del call["raw_response"]
                del response
                call["finish_reason"] = "length"
                call["output_discarded"] = True
                truncated = True
            finally:
                call["wall_seconds"] = time.monotonic() - call_started
            if comparison["status"] in ("mismatch", "usage_unavailable"):
                raise ExtractionError(
                    "rendered input token count could not be verified against usage.prompt_tokens"
                )
            if truncated:
                split_window(chunk, window_id, "finish_reason=length")
            resolved[key] = window_id

        for index, chunk in enumerate(chunks):
            run_window(chunk, str(index))
        merged = merge_assertions(groups, transcript)
        run.assertions = merged.assertions
        run.conflict_positions = merged.conflict_positions
        run.reduced_schema_state = LaneState.PASS
    except (ExtractionError, OSError) as exc:
        if isinstance(exc, SchemaResponseError):
            run.reduced_schema_state = LaneState.FAIL
        run.failure = str(exc) if isinstance(exc, ExtractionError) else "local extractor I/O failed"
        # Successful/invalid per-call output is diagnostic only on failure.
        # Truncated bodies are discarded entirely, never parsed or mapped.
    finally:
        run.wall_seconds = time.monotonic() - started
    return run


def extract_local(
    transcript: Transcript, config: ExtractorConfig, model: ExtractorCandidate, model_path: Path
) -> ExtractionRun:
    """Own the model lifetime, including startup-failure metadata and unload."""
    manager = ManagedLlamaServer(model, model_path, n_ctx=config.n_ctx)
    timestamp = datetime.now(UTC).isoformat()
    started = time.monotonic()
    try:
        with manager as backend:
            run = extract(backend, transcript, config, model)
    except (ExtractionError, OSError) as exc:
        run = ExtractionRun(
            model=model,
            runtime=manager.runtime,
            transcript_sha256=transcript.content_hash,
            decoding={
                "temperature": config.temperature,
                "max_output_tokens": config.max_output_tokens,
                "min_output_tokens": config.min_output_tokens,
                "headroom_tokens": HEADROOM_TOKENS,
                "n_ctx": config.n_ctx,
                "thinking": config.thinking,
            },
            failure=str(exc)
            if isinstance(exc, ExtractionError)
            else "local extractor startup I/O failed",
        )
    run.started_at = timestamp
    run.wall_seconds = time.monotonic() - started
    return run


def finish_pipeline(
    run: ExtractionRun, mapper: Mapper, validator: ValidatorRunner, output_dir: Path
) -> tuple[tuple[MappingOutcome, ...], ValidationRun | None]:
    """Call after unloading the extractor (A6); never export a final bundle here."""
    # Do not overwrite a previous run or leave stale success artifacts after a failure.
    try:
        prepare_output_directory(output_dir)
    except ValueError as exc:
        raise ExtractionError(str(exc)) from None
    write_private_text(output_dir / "extraction.json", json.dumps(run.document(), indent=2) + "\n")
    if run.failure:
        write_private_text(
            output_dir / "validation.json",
            json.dumps(
                {
                    "status": "failed",
                    "reason": run.failure,
                    "resources": [],
                    "lanes": {
                        name: {
                            "state": run.reduced_schema_state.value
                            if name == "reduced_schema"
                            else "NOT_CHECKED",
                            "reason": run.failure,
                        }
                        for name in (
                            "reduced_schema",
                            "evidence",
                            "base_r4",
                            "us_core_profile",
                            "terminology",
                        )
                    },
                },
                indent=2,
            )
            + "\n",
        )
        return (), None
    outcomes = []
    for position, assertion in enumerate(run.assertions):
        outcome = mapper.map_assertion(assertion, position)
        if position in run.conflict_positions:
            outcome = outcome.model_copy(
                update={
                    "is_material": True,
                    "review_reasons": (*outcome.review_reasons, "merge_conflict"),
                }
            )
        outcomes.append(outcome)
    checked, validation = validate_mappings(validator, mapper.intake, outcomes)
    write_validation(output_dir / "validation.json", mapper.intake, checked, validation)
    # Preserve mapper resources/ledger and exact offsets for downstream review.
    document = run.document()
    document["mappings"] = [outcome.model_dump(mode="json") for outcome in checked]
    write_private_text(output_dir / "extraction.json", json.dumps(document, indent=2) + "\n")
    return checked, validation
