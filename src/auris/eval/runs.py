"""Sequential managed inference, frozen artifacts, and network-free replay."""

from __future__ import annotations

import json
import platform
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pydantic.version import VERSION as PYDANTIC_VERSION

from auris._storage import prepare_output_directory, write_private_text
from auris.config import ExtractorCandidate, FrozenConfig
from auris.eval import FICTIONAL_BANNER, REPLAYABLE_SCORERS, SCORER_VERSION
from auris.eval.diagnostics import encounter_diagnostics
from auris.eval.gold import Dataset, canonical_json, digest, load_dataset, relative_file
from auris.eval.ledger import register_test_use
from auris.eval.metrics import score_model
from auris.eval.predictions import Prediction, freeze_prediction
from auris.extract.backend import Backend, ExtractionError, RuntimeMetadata
from auris.extract.identity import profile_identity
from auris.extract.leakage import check_leakage
from auris.extract.pipeline import ExtractionRun, extract, finish_pipeline
from auris.extract.profiles import ExtractionProfile, profile_contract
from auris.extract.prompt import prompt_text
from auris.fhir import Mapper
from auris.intake import Intake, load_intake
from auris.transcript import Transcript
from auris.validation.runner import ValidatorRunner

ServerFactory = Callable[[ExtractorCandidate], AbstractContextManager[Backend]]
MapperFactory = Callable[[Intake, Transcript], Mapper]
ValidatorFactory = Callable[[bool], ValidatorRunner]


def write_json(path: Path, value: object) -> None:
    write_private_text(path, canonical_json(value))


def scorer_hash() -> str:
    directory = Path(__file__).parent
    sources = {p.name: digest(p.read_bytes()) for p in sorted(directory.glob("*.py"))}
    return digest(canonical_json(sources).encode())


def run_dataset(
    manifest: Path,
    output: Path,
    config: FrozenConfig,
    models: list[str],
    *,
    server_factory: ServerFactory,
    mapper_factory: MapperFactory,
    validator_factory: ValidatorFactory,
    ledger: Path,
    confirmed: bool = False,
    locked_run: bool = False,
    seed: int = 1729,
    samples: int = 2000,
    profiles: list[ExtractionProfile] | None = None,
) -> dict:
    from auris.datasets.splits import PublicSplit

    raw = json.loads(manifest.read_text("utf-8"))
    run_id = uuid.uuid4().hex
    if raw.get("schema_version") == "public-split-1":
        public = PublicSplit.model_validate(raw)
        if public.split == "test" and not (locked_run or confirmed):
            register_test_use(ledger, public, run_id, "run")  # Logs the refusal.
        raise ValueError(
            "public split has no clinician gold or supplied intake; annotation required"
        )
    dataset, transcripts = load_dataset(manifest)
    if dataset.split == "test" and not (locked_run or confirmed):
        register_test_use(ledger, dataset, run_id, "run")  # Before even a prompt audit.
    candidates = [config.extractor.candidate(model) for model in models]
    if not candidates or len({c.id for c in candidates}) != len(candidates):
        raise ValueError("select at least one unique model")
    selected_profiles = profiles if profiles is not None else [config.extractor.extraction_profile]
    if not selected_profiles or len(set(selected_profiles)) != len(selected_profiles):
        raise ValueError("select at least one unique extraction profile")
    for profile in selected_profiles:
        profile_contract(profile)
    variants = [(c, p, f"{c.id}@{p}") for c in candidates for p in selected_profiles]
    if samples < 2:
        raise ValueError("bootstrap needs at least two replicates")
    prepare_output_directory(output)
    corpus = ["\n".join(s.text for s in transcripts[e.id].segments) for e in dataset.encounters]
    public = any(
        t.source and t.source.dataset in {"primock57", "aci-bench"} for t in transcripts.values()
    )
    if public:
        # Audit the entire eligible corpus, not just the selected dev/test split.
        from auris.cli import _repo_root
        from auris.setup.datasets import default_cache_dir, verified_corpus

        if {pin.id for pin in config.datasets} != {"primock57", "aci-bench"}:
            raise ValueError("public evaluation requires both pinned dataset corpora")
        for pin in config.datasets:
            full = verified_corpus(
                default_cache_dir() / pin.id,
                pin,
                _repo_root() / "eval/datasets" / f"{pin.id}-sources.json",
            )
            corpus.extend("\n".join(s.text for s in t.segments) for t in full.values())
    audits = {}
    prompts = {}
    for profile in selected_profiles:
        prompts[profile] = prompt_text() if profile == "full-v0" else prompt_text(profile)
        audits[profile] = check_leakage(
            prompts[profile],
            corpus,
            clinical_terms=dataset.clinical_watchlist,
            clinical_policy="warn" if public else "refuse",
        )
    leakage_metadata = {
        **audits[selected_profiles[0]].metadata(),
        "leakage_check": "pass" if all(a.passed for a in audits.values()) else "fail",
        "profiles": {p: a.metadata() for p, a in audits.items()},
    }
    write_json(output / "leakage.json", leakage_metadata)
    for audit in audits.values():
        audit.raise_if_failed()  # Before reservation, mapper, or server startup.
    lock = register_test_use(
        ledger, dataset, run_id, "run", confirmed=confirmed, locked_run=locked_run
    )
    frozen_encounters = []
    for encounter in dataset.encounters:
        transcript_path = f"dataset/{encounter.id}/transcript.json"
        intake_path = f"dataset/{encounter.id}/intake.json"
        prepare_output_directory(output / "dataset" / encounter.id)
        transcripts[encounter.id].save(output / transcript_path)
        # Keep the original intake bytes so the declared content hash stays valid.
        write_private_text(
            output / intake_path,
            relative_file(manifest.parent, encounter.intake_path).read_bytes().decode("utf-8"),
        )
        frozen_encounters.append(
            encounter.model_copy(
                update={"transcript_path": transcript_path, "intake_path": intake_path}
            )
        )
    dataset = dataset.model_copy(update={"encounters": tuple(frozen_encounters)})
    write_json(output / "dataset.json", dataset.model_dump(mode="json"))
    # Re-verify copied inputs and parse all supplied anchors before any inference.
    load_dataset(output / "dataset.json")
    intakes = {e.id: load_intake(output / e.intake_path) for e in dataset.encounters}
    write_json(output / "config.json", config.model_dump(mode="json"))
    write_private_text(output / "prompt.md", prompts[selected_profiles[0]])
    for profile, text in prompts.items():
        write_private_text(output / f"prompt-{profile}.md", text)
    document: dict[str, Any] = {
        "schema_version": "1",
        "scorer_version": SCORER_VERSION,
        "scorer_sha256": scorer_hash(),
        "run_id": run_id,
        "dataset_split": dataset.split,
        "notice": FICTIONAL_BANNER
        if dataset.split == "harness-check"
        else "Mock-consultation evaluation; public data may have been in model pretraining.",
        "status": "running",
        "seed": seed,
        "bootstrap_samples": samples,
        "models": [identity for _, _, identity in variants],
        "extraction_profiles": selected_profiles,
        "default_model": f"{config.extractor.candidate().id}@{selected_profiles[0]}",
        "leakage": leakage_metadata,
        "test_use": lock,
        "implementation_sha256": digest(
            canonical_json(
                {
                    str(p.relative_to(Path(__file__).parents[1])): digest(p.read_bytes())
                    for p in sorted(Path(__file__).parents[1].rglob("*"))
                    if p.is_file() and p.suffix in (".py", ".md", ".toml", ".json")
                }
            ).encode()
        ),
        "environment": {
            "python": platform.python_version(),
            "pydantic": PYDANTIC_VERSION,
            "system": platform.system(),
            "machine": platform.machine(),
            "os_release": platform.release(),
        },
        "model_metadata": {},
    }
    write_json(output / "run.json", document)
    for candidate, profile, identity in variants:
        extractor_config = config.extractor.model_copy(update={"extraction_profile": profile})
        started = time.monotonic()
        extractions: dict[str, ExtractionRun] = {}
        manager = server_factory(candidate)
        try:
            with manager as backend:
                for encounter in dataset.encounters:
                    extractions[encounter.id] = extract(
                        backend, transcripts[encounter.id], extractor_config, candidate
                    )
        except (ExtractionError, OSError) as exc:
            # Failures are explicit zero-prediction encounters, never dropped.
            for encounter in dataset.encounters:
                if encounter.id not in extractions:
                    extractions[encounter.id] = ExtractionRun(
                        model=candidate,
                        extraction_profile=profile,
                        decoding=extractor_config.model_dump(mode="json"),
                        transcript_sha256=encounter.transcript_sha256,
                        runtime=getattr(manager, "runtime", RuntimeMetadata()),
                        failure=str(exc)
                        if isinstance(exc, ExtractionError)
                        else "managed extractor startup or I/O failed; check local prerequisites",
                    )
        inference_seconds = time.monotonic() - started
        # The context is closed before the first Java validator call (A6).
        model_predictions = {}
        for encounter in dataset.encounters:
            run = extractions[encounter.id]
            intake = intakes[encounter.id]
            mapper = mapper_factory(intake, transcripts[encounter.id])
            encounter_dir = output / identity / encounter.id
            outcomes, _ = finish_pipeline(
                run, mapper, validator_factory(encounter.fictional), encounter_dir
            )
            model_predictions[encounter.id] = [
                freeze_prediction(a, o).model_dump(mode="json")
                for a, o in zip(run.assertions, outcomes, strict=True)
            ]
        runtime = next(iter(extractions.values())).runtime
        document["model_metadata"][identity] = {
            "model": candidate.model_dump(mode="json"),
            **profile_identity(profile),
            "measurements_by_encounter": {key: r.measurements for key, r in extractions.items()},
            "runtime": asdict(runtime),
            "managed_inference_wall_seconds": inference_seconds,
            "extraction_wall_seconds": sum(r.wall_seconds for r in extractions.values()),
            "total_wall_seconds": time.monotonic() - started,
            "failed_encounters": [key for key, r in extractions.items() if r.failure],
            "failure_reasons": dict(
                sorted(Counter(r.failure for r in extractions.values() if r.failure).items())
            ),
            "reduced_schema_by_encounter": {
                key: r.reduced_schema_state.value for key, r in extractions.items()
            },
            "extraction_success_rate": sum(r.failure is None for r in extractions.values())
            / len(extractions),
            "memory_scope": (
                "sampled server-process RSS across all encounters; not whole-pipeline memory"
            ),
            "budget_status": "unverified: whole-pipeline memory and audio runtime not measured",
        }
        write_json(output / identity / "predictions.json", model_predictions)
        write_json(output / "run.json", document)
    document["status"] = "complete"
    # Hash every frozen input/output. Reports and audit entries are derived files.
    document["artifacts"] = {
        str(p.relative_to(output)): digest(p.read_bytes())
        for p in sorted(output.rglob("*"))
        if p.is_file() and p.name != "run.json"
    }
    write_json(output / "run.json", document)
    return document


def load_run(output: Path) -> tuple[dict, Dataset, dict[str, dict[str, tuple[Prediction, ...]]]]:
    document = json.loads((output / "run.json").read_text("utf-8"))
    if document["status"] != "complete" or document["schema_version"] != "1":
        raise ValueError("evaluation run incomplete or unsupported")
    if document["scorer_version"] not in ("1", *REPLAYABLE_SCORERS):
        raise ValueError("unsupported frozen scorer version")
    for path, expected in document["artifacts"].items():
        if digest(relative_file(output, path).read_bytes()) != expected:
            raise ValueError("frozen artifact hash mismatch")
    dataset, transcripts = load_dataset(output / "dataset.json")
    predictions = {}
    for model in document["models"]:
        raw = json.loads(relative_file(output, f"{model}/predictions.json").read_text("utf-8"))
        predictions[model] = {}
        failures: Counter[str] = Counter()
        for key, rows in raw.items():
            # Version 1 projections lack resource/review/quote details. Recover
            # only from hashed frozen artifacts, never by rerunning production.
            extraction_path = f"{model}/{key}/extraction.json"
            if extraction_path not in document["artifacts"]:
                raise ValueError("frozen extraction artifact missing from manifest")
            extraction = json.loads(relative_file(output, extraction_path).read_text("utf-8"))
            if extraction["failure"]:
                failures[extraction["failure"]] += 1
            mappings = {m["id"]: m for m in extraction.get("mappings", [])}
            if set(mappings) != {p["id"] for p in rows}:
                raise ValueError("frozen predictions and mappings disagree")
            enriched = []
            for row in rows:
                mapping = mappings[row["id"]]
                assertion = extraction["assertions"][mapping["assertion_position"]]
                enriched.append(
                    Prediction.model_validate(
                        {
                            **row,
                            "resource": mapping["resource"],
                            "review_reasons": mapping["review_reasons"],
                            "quoted_evidence": assertion["evidence"],
                        }
                    )
                )
            predictions[model][key] = tuple(enriched)
        document["model_metadata"][model]["failure_reasons"] = dict(sorted(failures.items()))
        if any(len({p.id for p in rows}) != len(rows) for rows in predictions[model].values()):
            raise ValueError("duplicate frozen prediction id")
        for encounter_id, rows in predictions[model].items():
            if encounter_id not in transcripts:
                raise ValueError("prediction references unknown encounter")
            for prediction in rows:
                for span in prediction.evidence:
                    span.verify(transcripts[encounter_id])
    return document, dataset, predictions


def score_run(
    output: Path, *, ledger: Path, confirmed: bool = False, scorer_version: str = SCORER_VERSION
) -> dict:
    if scorer_version not in REPLAYABLE_SCORERS:
        raise ValueError("unsupported scorer version")
    document, dataset, predictions = load_run(output)
    register_test_use(ledger, dataset, document["run_id"], "score", confirmed=confirmed)
    result = {
        "schema_version": "1",
        "scorer_version": scorer_version,
        "scorer_sha256": scorer_hash(),
        "inference_scorer": {
            "version": document["scorer_version"],
            "sha256": document["scorer_sha256"],
        },
        "run_id": document["run_id"],
        "dataset_id": dataset.id,
        "split": dataset.split,
        "fictional_harness_check": dataset.split == "harness-check",
        "notice": document["notice"],
        "seed": document["seed"],
        "bootstrap_samples": document["bootstrap_samples"],
        "annotation": {e.id: e.annotation.model_dump(mode="json") for e in dataset.encounters},
        "provenance": {
            e.id: {"source": e.source, "license": e.license, "fictional": e.fictional}
            for e in dataset.encounters
        },
        "models": {
            model: score_model(
                dataset,
                predictions[model],
                seed=document["seed"],
                samples=document["bootstrap_samples"],
                metadata=document["model_metadata"][model],
                scorer_version=scorer_version,
            )
            for model in document["models"]
        },
    }
    _, transcripts = load_dataset(output / "dataset.json")
    for model in document["models"]:
        diagnostics: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for encounter in dataset.encounters:
            for error_type, records in encounter_diagnostics(
                encounter,
                predictions[model][encounter.id],
                transcripts[encounter.id],
                scorer_version=scorer_version,
            ).items():
                diagnostics[error_type].extend(records)
        result["models"][model]["error_analysis"] = dict(sorted(diagnostics.items()))
    write_json(output / "scores.json", result)
    write_json(
        output / "score-receipt.json",
        {
            "scores_sha256": digest((output / "scores.json").read_bytes()),
            "run_sha256": digest((output / "run.json").read_bytes()),
        },
    )
    return result
