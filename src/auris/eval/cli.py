"""CLI wiring; setup remains external and inference never downloads."""

import argparse
import os
import sqlite3
import sys
from pathlib import Path


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "eval", help="Run, score and compare frozen evaluation predictions"
    )
    commands = parser.add_subparsers(dest="eval_command", required=True)
    leakage = commands.add_parser("leakage-report")
    leakage.add_argument("--dataset", required=True, help="Public dataset ID or manifest path")
    leakage.add_argument(
        "--prompt-version",
        choices=("extraction_v1", "extraction_v2", "extraction_v3", "extraction_v4"),
    )
    run = commands.add_parser("run")
    run.add_argument("--dataset", type=Path, required=True)
    run.add_argument("--model", action="append", default=None)
    run.add_argument(
        "--extraction-profile",
        action="append",
        choices=("full-v0", "lean-v1"),
        help="Repeat to compare profiles of each selected model in one run",
    )
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--seed", type=int, default=1729)
    run.add_argument("--bootstrap-samples", type=int, default=2000)
    run.add_argument(
        "--locked-test-run",
        action="store_true",
        help="Authorize the first held-out extraction (Slice 4 only)",
    )
    score = commands.add_parser("score")
    score.add_argument("--run", type=Path, required=True)
    score.add_argument("--scorer-version", choices=("2", "3"), default="3")
    compare = commands.add_parser("compare")
    compare.add_argument("--run", type=Path, required=True)
    for command in (run, score):
        command.add_argument("--confirm-locked-test-rerun", action="store_true")


def command(args: argparse.Namespace) -> int:
    from pydantic import ValidationError

    try:
        return _command(args)
    except ValidationError:
        raise ValueError(
            "invalid evaluation input schema; inspect the private input files"
        ) from None


def _command(args: argparse.Namespace) -> int:
    if args.eval_command == "leakage-report":
        from auris.eval.leakage_report import leakage_report
        from auris.extract.prompt import PROMPT_VERSION

        _, sentences = leakage_report(
            args.dataset, prompt_version=args.prompt_version or PROMPT_VERSION
        )
        for sentence in sentences:
            print(sentence)
        return 0

    from auris.cli import _model_cache, _repo_root
    from auris.config import load_frozen_config
    from auris.eval.reports import compare_run
    from auris.eval.runs import run_dataset, score_run
    from auris.extract.backend import ManagedLlamaServer
    from auris.fhir import Mapper
    from auris.fhir.contracts import load_unit_sets
    from auris.setup import fhir_packages, terminology, validator
    from auris.terminology import TerminologyIndex
    from auris.validation.runner import ValidatorRunner

    ledger = (
        Path(os.environ.get("AURIS_CACHE", _repo_root() / "cache")) / "eval" / "test-ledger.jsonl"
    )
    if args.eval_command == "score":
        report = score_run(
            args.run,
            ledger=ledger,
            confirmed=args.confirm_locked_test_rerun,
            scorer_version=args.scorer_version,
        )
        print(
            "scored frozen predictions; wrote scores.json"
            + (
                "; fictional harness-check set, NOT accuracy evidence"
                if report["fictional_harness_check"]
                else ""
            )
        )
        return 0
    if args.eval_command == "compare":
        if not (args.run / "scores.json").exists():
            score_run(args.run, ledger=ledger)
        report = compare_run(args.run)
        print(report["selection"]["verdict"])
        print("wrote report.md and report.json")
        return 0
    config = load_frozen_config()
    core = config.fhir.package("hl7.fhir.r4.core")
    assert core is not None
    try:
        units = load_unit_sets(fhir_packages.default_cache_dir() / core.filename, core.sha256)
    except FileNotFoundError:
        raise ValueError("FHIR unit sets missing; run auris setup fhir") from None
    try:
        index_context = TerminologyIndex(terminology.default_cache_dir() / "index.sqlite")
    except sqlite3.Error:
        raise ValueError("terminology missing; run auris setup terminology --fetch") from None
    with index_context as index:
        result = run_dataset(
            args.dataset,
            args.out,
            config,
            args.model or [config.extractor.candidate().id],
            server_factory=lambda candidate: ManagedLlamaServer(
                candidate, _model_cache() / candidate.gguf_filename, n_ctx=config.extractor.n_ctx
            ),
            mapper_factory=lambda intake, transcript: Mapper(intake, transcript, index, units),
            validator_factory=lambda fictional: ValidatorRunner(
                config, validator.default_cache_dir(), allow_example_urls=fictional
            ),
            ledger=ledger,
            confirmed=args.confirm_locked_test_rerun,
            locked_run=args.locked_test_run,
            seed=args.seed,
            samples=args.bootstrap_samples,
            profiles=args.extraction_profile,
        )
    print(result["notice"])
    print(
        "froze evaluation predictions and metadata; run auris eval score, then auris eval compare"
    )
    for model, metadata in result["model_metadata"].items():
        if metadata["failed_encounters"]:
            print(
                f"{model}: {len(metadata['failed_encounters'])} failed encounters", file=sys.stderr
            )
            for reason, count in sorted(metadata["failure_reasons"].items()):
                print(f"  {count}: {reason}", file=sys.stderr)
    return int(any(m["failed_encounters"] for m in result["model_metadata"].values()))
