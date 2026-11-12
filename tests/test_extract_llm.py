"""Real local-model contrastive smoke test, not an accuracy claim.

Every transcript is explicitly fictional. This is a low-confidence probe with
fewer than 25 cases, not the locked A9 evaluation.

Only loopback is allowed; no setup/download is performed. The safety stress
check supplies passing structural lanes, so missing Java or an unrelated FHIR
failure cannot hide an unreviewed critical error. Actual validator results are
also recorded separately after the extractor has been unloaded.
"""

import json
from pathlib import Path

import pytest
from extract_helpers import CASES, fixture_transcript_texts
from validator_helpers import passing_run

from auris._storage import write_private_text
from auris.config import load_frozen_config
from auris.extract.backend import (
    ExtractionError,
    FakeBackend,
    ManagedLlamaServer,
    find_llama_server,
    free_port,
)
from auris.extract.chunking import TokenCounter
from auris.extract.leakage import check_leakage
from auris.extract.pipeline import extract
from auris.extract.prompt import PROMPT_VERSION, prompt_sha256, prompt_text
from auris.fhir import Mapper
from auris.setup.validator import default_cache_dir
from auris.transcript import Transcript
from auris.validation.report import apply_validation, validate_mappings
from auris.validation.runner import ValidatorRunner

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "cache" / "llm" / "step-4.json"


def critical_errors(assertion, expected):
    """Independent, deliberately strict checks against these single-fact golds.

    This is a safety probe, not the A9 adjudicated concept-matching/F1 evaluator.
    We don't reuse the mapper's terminology lexicon to decide gold equivalence.
    """
    errors = []
    if (
        expected["kind"] == "allergy_denial"
        and assertion.kind == "allergy_statement"
        and assertion.polarity == "affirmed"
    ):
        errors.append("allergy_denial_flipped")
    if assertion.experiencer != expected["experiencer"]:
        errors.append("wrong_person")
    if assertion.kind in ("explicit_new_order", "reported_medication"):
        if assertion.concept_text != expected["concept_text"] or getattr(
            assertion, "dose_text", None
        ) != expected.get("dose_text"):
            errors.append("wrong_drug_or_dose")
    if assertion.kind == "explicit_new_order" and expected["kind"] != "explicit_new_order":
        errors.append("order_never_given")
    if (
        expected["polarity"] == "negated"
        and assertion.polarity == "affirmed"
        and assertion.kind in ("clinician_assessed_problem", "patient_reported_condition")
    ):
        errors.append("denied_condition_recorded_present")
    return errors


@pytest.mark.llm
@pytest.mark.allow_hosts(["127.0.0.1"])
def test_real_qwen_contrastive_smoke(terminology, units, intake):
    config = load_frozen_config()
    candidate = config.extractor.candidate()
    model_path = ROOT / "cache/models" / candidate.gguf_filename
    counter = TokenCounter(FakeBackend([]))
    leakage = check_leakage(prompt_text(), fixture_transcript_texts())
    report = {
        "fictional": True,
        "test_type": "contrastive_smoke_test",
        "interpretation": (
            "smoke test only; not an accuracy claim; low-confidence (fewer than 25 cases)"
        ),
        "status": "starting",
        "model_id": candidate.id,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": prompt_sha256(),
        "prompt_tokens": counter.count(prompt_text()),
        "prompt_token_count_method": counter.method,
        **leakage.metadata(),
        "cases": [],
        "safety_method": (
            "critical errors compared with fixture gold; stress gate uses "
            "passing structural lanes without review"
        ),
    }

    def save():
        write_private_text(REPORT, json.dumps(report, indent=2) + "\n")

    def skip(reason):
        report.update(status="skipped", reason=reason)
        save()
        pytest.skip(reason)

    if not leakage.passed:
        report.update(status="failed", reason="prompt/corpus leakage; smoke test refused")
        save()
        leakage.raise_if_failed()

    if not model_path.is_file():
        skip("pinned Qwen model missing; provision cache/models during setup")
    try:
        find_llama_server()
    except ExtractionError:
        skip("llama-server missing; configure AURIS_LLAMA_SERVER or PATH")
    try:
        free_port()
    except OSError:
        skip("sandbox blocks localhost bind; run llm tests outside this sandbox")
    mappings = []
    failures = []
    manager = ManagedLlamaServer(candidate, model_path, n_ctx=config.extractor.n_ctx)
    try:
        with manager as backend:
            for case in CASES:
                mapper = Mapper(
                    intake, Transcript.model_validate(case["transcript"]), terminology, units
                )
                run = extract(backend, mapper.transcript, config.extractor, candidate)
                row = {"name": case["name"], "extraction": run.document(), "safety": []}
                row["extraction"]["metadata"].update(leakage.metadata())
                report["cases"].append(row)
                report["prompt_tokens"] = run.prompt_tokens
                report["prompt_token_count_method"] = run.prompt_token_count_method
                if run.failure:
                    failures.append(f"{case['name']}: {run.failure}")
                    save()
                    continue
                # extract() already strictly parses every response through ExtractionResult.
                outcomes = mapper.map_all(run.assertions)
                mappings.extend(outcomes)
                gate_run = passing_run(intake, outcomes)
                for assertion, outcome in zip(run.assertions, outcomes, strict=True):
                    errors = critical_errors(assertion, case["assertions"][0])
                    stressed = apply_validation(outcome, gate_run)
                    row["safety"].append(
                        {
                            "id": outcome.id,
                            "critical_errors": errors,
                            "export_state_with_passing_structural_lanes": stressed.export_state,
                            "review_reasons": stressed.review_reasons,
                        }
                    )
                    if errors and stressed.export_state == "final_exportable":
                        failures.append(f"{case['name']}: unreviewed critical error")
                save()
    except (ExtractionError, OSError):
        # Present-but-broken artifacts/runtime are failures, not missing-prerequisite skips.
        report.update(status="failed", reason="local model startup or inference failed")
        save()
        raise
    # A6: the model has been unloaded before Java is started.
    checked, validation = validate_mappings(
        ValidatorRunner(config, default_cache_dir(), allow_example_urls=True), intake, mappings
    )
    report["actual_validation"] = {
        "failure": validation.failure,
        "wall_seconds": validation.wall_seconds,
        "outcomes": [outcome.model_dump(mode="json") for outcome in checked],
    }
    report["runtime"] = {
        "version": manager.runtime.version,
        "peak_rss_bytes": manager.runtime.peak_rss_bytes,
        "rss_method": manager.runtime.rss_method,
    }
    report["memory_reference"] = {
        "laptop_gb": 16,
        "whole_pipeline_budget_gb": 12,
        "measured_scope": "server process RSS only; whole-pipeline/GPU peak remains unmeasured",
    }
    report["status"] = "failed" if failures else "complete"
    report["failures"] = failures
    save()
    assert not failures, "; ".join(failures)
