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
from auris.extract.chunking import TokenCounter, merge_assertions, plan_chunks
from auris.extract.prompt import PROMPT_VERSION, messages, prompt_sha256, prompt_text
from auris.fhir import Mapper
from auris.fhir.models import LaneState, MappingOutcome
from auris.transcript import Transcript
from auris.validation.models import ValidationRun
from auris.validation.report import validate_mappings, write_validation
from auris.validation.runner import ValidatorRunner


class SchemaResponseError(ExtractionError):
    """The reduced-schema checker ran and rejected the response."""


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
            raise ExtractionError("extraction truncated (finish_reason=length); no partial results")
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
            "max_tokens": config.max_tokens,
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
        chunks = plan_chunks(
            transcript, schema, backend, n_ctx=config.n_ctx, max_tokens=config.max_tokens
        )
        groups = []
        for chunk in chunks:
            call: dict[str, Any] = {
                "segment_ids": [s.id for s in chunk.segments],
                "reserved_input_tokens": chunk.input_tokens,
                "token_count_method": chunk.token_count_method,
            }
            run.calls.append(call)
            call_started = time.monotonic()
            try:
                response = backend.complete(
                    messages(chunk.segments),
                    schema,
                    temperature=config.temperature,
                    max_tokens=config.max_tokens,
                )
                call["raw_response"] = response
                call["usage"] = response.get("usage")
                result = parse_completion(response)
                allowed = {s.id for s in chunk.segments}
                if any(
                    item.segment_id not in allowed for a in result.assertions for item in a.evidence
                ):
                    raise ExtractionError("extraction cites a segment outside its input window")
                call["parsed_assertions"] = result.model_dump(mode="json")["assertions"]
                groups.append(result.assertions)
            finally:
                call["wall_seconds"] = time.monotonic() - call_started
        merged = merge_assertions(groups, transcript)
        run.assertions = merged.assertions
        run.conflict_positions = merged.conflict_positions
        run.reduced_schema_state = LaneState.PASS
    except (ExtractionError, OSError) as exc:
        if isinstance(exc, SchemaResponseError):
            run.reduced_schema_state = LaneState.FAIL
        run.failure = str(exc) if isinstance(exc, ExtractionError) else "local extractor I/O failed"
        # Per-call raw/parsed output is retained for diagnosis, never mapped.
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
                "max_tokens": config.max_tokens,
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
