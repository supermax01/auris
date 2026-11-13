"""Extraction, fail-closed parsing, mapping, validation and private artifacts."""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from auris._storage import prepare_output_directory, write_private_text
from auris.assertions import (
    MAX_ASSERTIONS,
    AssertionBase,
    ExtractionResult,
    extraction_json_schema,
    parse_extraction_json,
)
from auris.config import ExtractorCandidate, ExtractorConfig
from auris.extract.backend import (
    Backend,
    ExtractionError,
    ExtractionTimeoutError,
    ManagedLlamaServer,
    RuntimeMetadata,
)
from auris.extract.chunking import (
    Chunk,
    measure_chunk,
    merge_assertions,
    plan_chunks,
    split_segments,
)
from auris.extract.citations import resolve_citations
from auris.extract.errors import LoopDetectedError
from auris.extract.grammar import json_grammar
from auris.extract.identity import profile_identity
from auris.extract.measurements import (
    extraction_measurements,
    json_whitespace_stats,
    mapped_evidence_measurements,
    repetition_metrics,
)
from auris.extract.profiles import (
    ExtractionProfile,
    SchemaVersion,
    decoding_parameters,
    profile_contract,
)
from auris.extract.prompt import messages, prompt_text
from auris.extract.quarantine import ItemError, QuarantinedItem, QuarantineReview
from auris.extract.timeout import TimeoutBudget
from auris.extract.tokens import (
    HEADROOM_TOKENS,
    OUTPUT_ESTIMATE,
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


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        _reject_constant(value)
    return number


def _item_errors(error: ValidationError, position: int) -> tuple[ItemError, ...]:
    errors = []
    for entry in error.errors():
        location = tuple(entry["loc"])
        if location[:2] == ("assertions", 0):
            location = ("assertions", position, *location[2:])
        errors.append(ItemError(type=entry["type"], location=location))
    return tuple(errors)


def parse_completion(
    response: dict[str, Any],
    *,
    schema_version: SchemaVersion = "1",
    quarantined: list[QuarantinedItem] | None = None,
    call_index: int = 0,
    window_id: str = "0",
    transcript_sha256: str | None = None,
) -> ExtractionResult:
    try:
        choices = response["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ExtractionError("extractor must return exactly one completion")
        choice = choices[0]
        if choice["message"].get("reasoning_content"):
            raise ExtractionError("extractor returned reasoning despite thinking-off configuration")
        if choice["finish_reason"] == "length":
            raise TruncatedCompletionError(
                "extraction truncated (finish_reason=length); no partial results"
            )
        if choice["finish_reason"] != "stop":
            raise ExtractionError("extractor did not finish normally; no partial results")
        content = choice["message"]["content"]
        if not isinstance(content, str):
            raise ExtractionError("extractor message content is not JSON text")
        # Check duplicates before strict pydantic JSON parsing; Python's default
        # JSON parser would silently retain the last clinical field value.
        document = json.loads(
            content,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
        if (
            not isinstance(document, dict)
            or set(document) != {"assertions"}
            or not isinstance(document["assertions"], list)
        ):
            raise SchemaResponseError("schema-invalid extraction response; wrong top-level shape")
        items = document["assertions"]
        if len(items) >= MAX_ASSERTIONS:
            raise ExtractionError(
                "extraction reached assertion bound; completeness cannot be assured"
            )
        valid = []
        for position, item in enumerate(items):
            try:
                # Keep strict JSON-mode validation (including strict number types
                # and v2 optional-null normalization), not coercive Python mode.
                sibling = parse_extraction_json(
                    json.dumps({"assertions": [item]}, allow_nan=False),
                    schema_version=schema_version,
                )
                valid.extend(sibling.assertions)
            except ValidationError as exc:
                if quarantined is None:
                    raise  # Legacy direct callers may request all-or-nothing parsing.
                quarantined.append(
                    QuarantinedItem(
                        call_index=call_index,
                        window_id=window_id,
                        position=position,
                        raw_item=item,
                        transcript_sha256=transcript_sha256,
                        errors=_item_errors(exc, position),
                    )
                )
        result = ExtractionResult(assertions=tuple(valid))
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
    extraction_profile: ExtractionProfile = "full-v0"
    measurements: dict[str, Any] = field(default_factory=dict)
    loop_guard: dict[str, Any] = field(default_factory=dict)
    quarantined_items: list[QuarantinedItem] = field(default_factory=list)
    quarantine_reviews: list[QuarantineReview] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    prompt_tokens: int | None = None
    prompt_token_count_method: str | None = None
    assertions: tuple[AssertionBase, ...] = ()
    conflict_positions: frozenset[int] = frozenset()
    citation_failures: dict[int, frozenset[str]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    discarded_responses: dict[int, dict[str, Any]] = field(default_factory=dict, repr=False)
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
            "citation_failures": [
                {
                    "assertion_position": position,
                    "reason": "citation_outside_window",
                    "segment_ids": sorted(ids),
                }
                for position, ids in sorted(self.citation_failures.items())
            ],
            "calls": self.calls,
            "quarantined_items": [item.model_dump(mode="json") for item in self.quarantined_items],
            "visit_review_items": [item.review_item() for item in self.quarantined_items],
            "quarantine_reviews": [
                review.model_dump(mode="json") for review in self.quarantine_reviews
            ],
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
                **profile_identity(
                    self.extraction_profile, self.model, seed=self.decoding.get("seed")
                ),
                "loop_guard": self.loop_guard,
                "measurements": self.measurements,
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
    profile = config.extraction_profile
    contract = profile_contract(profile)
    try:
        temperature, sampling = decoding_parameters(config, model)
    except ValueError as exc:
        raise ExtractionError(str(exc)) from None
    run = ExtractionRun(
        model=model,
        decoding={
            "temperature": temperature,
            "max_output_tokens": config.max_output_tokens,
            "min_output_tokens": config.min_output_tokens,
            "headroom_tokens": HEADROOM_TOKENS,
            "n_ctx": config.n_ctx,
            "thinking": config.thinking,
            "output_estimate": dict(OUTPUT_ESTIMATE),
            "sampling_parameters": sampling,
            "seed": sampling.get("seed"),
            "reproducibility": (
                "sampled decoding requires the same build, hardware, seed and single-slot server"
            ),
            "json_whitespace": (
                f"grammar_{contract.grammar_whitespace}_json"
                if contract.grammar_whitespace is not None
                else "prompt_compact_json"
                if contract.compact_json
                else "server_default"
            ),
        },
        transcript_sha256=transcript.content_hash,
        runtime=backend.runtime,
        extraction_profile=profile,
        loop_guard=config.loop_guard.model_dump(mode="json"),
    )
    try:
        counter = TokenCounter(backend)
        run.prompt_tokens = counter.count(prompt_text(profile))
        run.prompt_token_count_method = counter.method
        schema = extraction_json_schema(
            contract.schema_version, property_order=contract.property_order
        )
        grammar = (
            json_grammar(schema, whitespace=contract.grammar_whitespace)
            if contract.grammar_whitespace is not None
            else None
        )
        input_counter = InputTokenCounter(backend, schema, profile=profile, grammar=grammar)
        chunks = plan_chunks(
            transcript,
            schema,
            backend,
            n_ctx=config.n_ctx,
            min_output_tokens=config.min_output_tokens,
            max_output_tokens=config.max_output_tokens,
            input_counter=input_counter,
        )
        groups: list[tuple[AssertionBase, ...]] = []
        group_windows: list[frozenset[str]] = []
        resolved: dict[tuple[str, ...], str] = {}

        def split_window(chunk: Chunk, window_id: str, reason: str) -> None:
            if len(chunk.segments) == 1:
                if reason in ("finish_reason=length", "extractor repetition loop"):
                    raise ExtractionError(
                        f"single transcript segment still truncated ({reason}); no partial results"
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
                "transcript_tokens": chunk.transcript_tokens,
                "transcript_token_count_method": chunk.transcript_token_count_method,
                "planned_output_tokens": chunk.planned_output_tokens,
                "output_estimate_exceeds_budget": chunk.planned_output_tokens > max_tokens,
                "repetition": None,
                "transport": "sse",
            }
            budget = TimeoutBudget(
                max_tokens,
                chunk.input_tokens,
                generation_tokens_per_second=model.generation_tokens_per_second,
                prompt_tokens_per_second=model.prompt_tokens_per_second,
            )
            call["timeout"] = {
                **asdict(budget),
                "seconds": budget.seconds,
                "first_chunk_seconds": budget.first_chunk_seconds,
                "idle_seconds": 60,
                "source": "configured",
            }
            call_index = len(run.calls)
            run.calls.append(call)
            call_started = time.monotonic()
            split_reason: str | None = None
            comparison = {"status": "usage_unavailable"}
            try:
                response = backend.complete(
                    messages(chunk.segments, profile),
                    schema,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=budget.seconds,
                    first_chunk_timeout=budget.first_chunk_seconds,
                    identical_assertion_limit=config.loop_guard.identical_assertion_limit,
                    **({"sampling": sampling} if sampling else {}),
                    **({"grammar": grammar} if grammar is not None else {}),
                )
                call["raw_response"] = response
                # Diagnose even invalid/truncated output, without parsing it.
                choices = response.get("choices")
                if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict):
                    message = choices[0].get("message")
                    if isinstance(message, dict) and isinstance(message.get("content"), str):
                        call["repetition"] = repetition_metrics(message["content"])
                call["usage"] = response.get("usage")
                call["output_token_count_method"] = "server_usage"
                usage = call["usage"]
                actual = usage.get("completion_tokens") if isinstance(usage, dict) else None
                call["actual_output_tokens"] = (
                    actual if type(actual) is int and actual >= 0 else None
                )
                comparison = compare_input_usage(
                    InputTokens(chunk.input_tokens, chunk.token_count_method), call["usage"]
                )
                call["input_token_comparison"] = comparison
                quarantined: list[QuarantinedItem] = []
                result = parse_completion(
                    response,
                    schema_version=contract.schema_version,
                    quarantined=quarantined,
                    call_index=call_index,
                    window_id=window_id,
                    transcript_sha256=transcript.content_hash,
                )
                run.quarantined_items.extend(quarantined)
                call["quarantined_item_count"] = len(quarantined)
                call["json_whitespace"] = json_whitespace_stats(
                    response["choices"][0]["message"]["content"]
                )
                allowed = frozenset(s.id for s in chunk.segments)
                assertions, resolutions = resolve_citations(
                    result.assertions,
                    chunk.segments,
                    known_segment_ids=frozenset(s.id for s in transcript.segments),
                )
                call["citation_resolutions"] = resolutions
                call["citation_failure_positions"] = [
                    i
                    for i, assertion in enumerate(assertions)
                    if any(item.segment_id not in allowed for item in assertion.evidence)
                ]
                call["parsed_assertions"] = result.model_dump(mode="json")["assertions"]
                groups.append(assertions)
                group_windows.append(allowed)
            except ExtractionError as exc:
                call["failure_type"] = (
                    "timeout" if isinstance(exc, ExtractionTimeoutError) else type(exc).__name__
                )
                if isinstance(exc, ExtractionTimeoutError):
                    call["timeout_phase"] = exc.phase
                partial = getattr(exc, "partial_response", None)
                if partial is not None:
                    call["raw_response"] = partial
                    text = partial["choices"][0]["message"]["content"]
                    call["repetition"] = repetition_metrics(text)
                    call["usage"] = partial.get("usage")
                    call["actual_output_tokens"] = partial["usage"]["completion_tokens"]
                    call["output_token_count_method"] = partial["output_token_count_method"]
                    call["output_token_count_limitation"] = (
                        "received content chunks only; captured sample has "
                        "117 versus 120 usage tokens"
                    )
                    call["input_token_comparison"] = {
                        "status": "usage_unavailable_stream_stopped",
                        "reason": "final prompt usage was not received",
                    }
                if "raw_response" in call:
                    # Kept only in a separate, clearly discarded private artifact.
                    # Never included in the extraction document or assertions.
                    run.discarded_responses[call_index] = {
                        "status": "discarded",
                        "used_for_assertions": False,
                        "reason": str(exc),
                        "response": call.pop("raw_response"),
                    }
                    call["discarded_response_file"] = f"discarded-call-{call_index:04d}.json"
                    call["output_discarded"] = True
                if isinstance(exc, TruncatedCompletionError):
                    call["finish_reason"] = "length"
                    split_reason = "finish_reason=length"
                elif isinstance(exc, LoopDetectedError):
                    call["finish_reason"] = "loop_guard"
                    split_reason = "extractor repetition loop"
                else:
                    raise
            finally:
                call["wall_seconds"] = time.monotonic() - call_started
            if split_reason != "extractor repetition loop" and comparison["status"] in (
                "mismatch",
                "usage_unavailable",
            ):
                raise ExtractionError(
                    "rendered input token count could not be verified against usage.prompt_tokens"
                )
            if split_reason:
                split_window(chunk, window_id, split_reason)
            resolved[key] = window_id

        for index, chunk in enumerate(chunks):
            run_window(chunk, str(index))
        merged = merge_assertions(groups, transcript, allowed_segment_ids=group_windows)
        run.assertions = merged.assertions
        run.conflict_positions = merged.conflict_positions
        run.citation_failures = merged.citation_failures
        run.reduced_schema_state = LaneState.PASS
    except (ExtractionError, OSError) as exc:
        if isinstance(exc, SchemaResponseError):
            run.reduced_schema_state = LaneState.FAIL
        run.failure = str(exc) if isinstance(exc, ExtractionError) else "local extractor I/O failed"
        # Successful/invalid per-call output is diagnostic only on failure.
        # Discarded bodies are private diagnostics, never parsed or mapped.
    finally:
        run.wall_seconds = time.monotonic() - started
        run.measurements = extraction_measurements(
            run.assertions, run.calls, transcript, run.citation_failures
        )
        run.measurements["quarantined_item_count"] = len(run.quarantined_items)
    return run


def extract_local(
    transcript: Transcript, config: ExtractorConfig, model: ExtractorCandidate, model_path: Path
) -> ExtractionRun:
    """Own the model lifetime, including startup-failure metadata and unload."""
    try:
        temperature, sampling = decoding_parameters(config, model)
    except ValueError as exc:
        raise ExtractionError(str(exc)) from None
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
            extraction_profile=config.extraction_profile,
            loop_guard=config.loop_guard.model_dump(mode="json"),
            transcript_sha256=transcript.content_hash,
            decoding={
                "temperature": temperature,
                "sampling_parameters": sampling,
                "seed": sampling.get("seed"),
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
    if not run.measurements:
        run.measurements = extraction_measurements(
            run.assertions, run.calls, mapper.transcript, run.citation_failures
        )
    for index, response in run.discarded_responses.items():
        write_private_text(
            output_dir / f"discarded-call-{index:04d}.json", json.dumps(response, indent=2) + "\n"
        )
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
        invalid_ids = run.citation_failures.get(position)
        allowed = (
            frozenset(s.id for s in mapper.transcript.segments) - invalid_ids
            if invalid_ids
            else None
        )
        outcome = mapper.map_assertion(assertion, position, allowed_segment_ids=allowed)
        if position in run.conflict_positions:
            outcome = outcome.model_copy(
                update={
                    "is_material": True,
                    "review_reasons": (*outcome.review_reasons, "merge_conflict"),
                }
            )
        outcomes.append(outcome)
    checked, validation = validate_mappings(validator, mapper.intake, outcomes)
    run.measurements.update(mapped_evidence_measurements(checked))
    write_validation(output_dir / "validation.json", mapper.intake, checked, validation)
    if run.quarantined_items:
        # Visit-level failures, separate from accepted siblings' schema lanes.
        document = json.loads((output_dir / "validation.json").read_text("utf-8"))
        document["visit_review_items"] = [item.review_item() for item in run.quarantined_items]
        write_private_text(output_dir / "validation.json", json.dumps(document, indent=2) + "\n")
    # Preserve mapper resources/ledger and exact offsets for downstream review.
    document = run.document()
    document["mappings"] = [outcome.model_dump(mode="json") for outcome in checked]
    write_private_text(output_dir / "extraction.json", json.dumps(document, indent=2) + "\n")
    return checked, validation
