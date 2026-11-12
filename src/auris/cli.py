"""Command-line interface for Auris."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from auris import __version__


def _repo_root() -> Path:
    """Best-effort repo root for default output paths (source-tree runs)."""
    cwd = Path.cwd()
    if (cwd / "config" / "frozen.toml").is_file():
        return cwd
    for parent in Path(__file__).resolve().parents:
        if (parent / "config" / "frozen.toml").is_file():
            return parent
    return cwd


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auris", description="Auris (research software)")
    parser.add_argument(
        "--version",
        action="version",
        version=f"auris {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("demo", help="Run the bundled demo (not implemented yet)")

    extract_parser = subparsers.add_parser(
        "extract", help="Extract facts using a local llama-server"
    )
    extract_parser.add_argument("--transcript", type=Path, required=True)
    extract_parser.add_argument("--intake", type=Path, required=True)
    extract_parser.add_argument("--out", type=Path, required=True)
    extract_parser.add_argument("--model", default=None)

    setup_parser = subparsers.add_parser("setup", help="One-time setup downloads")
    setup_subparsers = setup_parser.add_subparsers(dest="setup_command")
    fhir_parser = setup_subparsers.add_parser(
        "fhir", help="Download the pinned FHIR packages into the cache"
    )
    fhir_parser.add_argument("--config", type=Path, default=None)
    fhir_parser.add_argument("--cache", type=Path, default=None)

    terminology_parser = setup_subparsers.add_parser(
        "terminology", help="Verify terminology archives and build the local index"
    )
    terminology_parser.add_argument("--config", type=Path, default=None)
    terminology_parser.add_argument("--cache", type=Path, default=None)
    terminology_parser.add_argument("--fetch", action="store_true", help="Fetch missing archives")

    validator_parser = setup_subparsers.add_parser(
        "validator", help="Verify the validator JAR and seed its offline package cache"
    )
    validator_parser.add_argument("--config", type=Path, default=None)
    validator_parser.add_argument("--cache", type=Path, default=None)
    validator_parser.add_argument("--package-cache", type=Path, default=None)
    validator_parser.add_argument("--fetch", action="store_true", help="Fetch the missing JAR")

    checklist_parser = subparsers.add_parser(
        "checklist", help="Regenerate the US Core profile checklist from the cache"
    )
    root = _repo_root()
    checklist_parser.add_argument("--config", type=Path, default=None)
    checklist_parser.add_argument("--cache", type=Path, default=None)
    checklist_parser.add_argument(
        "--sources",
        type=Path,
        default=root / "src" / "auris" / "contract" / "element_sources.toml",
    )
    checklist_parser.add_argument(
        "--json-out",
        type=Path,
        default=root / "src" / "auris" / "contract" / "checklist.json",
    )
    checklist_parser.add_argument(
        "--md-out",
        type=Path,
        default=root / "docs" / "profile-checklist.md",
    )
    return parser


def _cmd_extract(args: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from auris.config import load_frozen_config
    from auris.extract.pipeline import extract_local, finish_pipeline
    from auris.fhir import Mapper
    from auris.fhir.contracts import load_unit_sets
    from auris.intake import load_intake
    from auris.setup import fhir_packages, terminology, validator
    from auris.terminology import TerminologyIndex
    from auris.transcript import Transcript
    from auris.validation.runner import ValidatorRunner

    config = load_frozen_config()
    candidate = config.extractor.candidate(args.model)

    try:
        transcript = Transcript.load(args.transcript)
        intake = load_intake(args.intake)
    except ValidationError:
        raise ValueError("invalid transcript or intake JSON; check the input schemas") from None
    core = config.fhir.package("hl7.fhir.r4.core")
    assert core is not None
    units = load_unit_sets(fhir_packages.default_cache_dir() / core.filename, core.sha256)
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError("output directory must be empty for a new extraction run")
    with TerminologyIndex(terminology.default_cache_dir() / "index.sqlite") as index:
        mapper = Mapper(intake, transcript, index, units)
        run = extract_local(
            transcript,
            config.extractor,
            candidate,
            _repo_root() / "cache" / "models" / candidate.gguf_filename,
        )
        # Model is unloaded before any JVM is started (A6).
        runner = ValidatorRunner(config, validator.default_cache_dir())
        outcomes, validation = finish_pipeline(run, mapper, runner, args.out)
    if run.failure:
        print(f"extraction failed: {run.failure}", file=sys.stderr)
        return 1
    print(f"extracted {len(outcomes)} assertions; wrote extraction.json and validation.json")
    if validation and validation.failure:
        print("validator NOT_CHECKED; see validation.json for the reason")
    return 0


def _cmd_setup_fhir(args: argparse.Namespace) -> int:
    from auris.config import load_frozen_config
    from auris.setup import fhir_packages

    config = load_frozen_config(args.config)
    cache_dir = args.cache if args.cache is not None else fhir_packages.default_cache_dir()
    ensured = fhir_packages.ensure_packages(config.fhir.packages, cache_dir)
    problems: list[str] = []
    for package in ensured:
        declared_license = package.package_json.get("license")
        status = "downloaded" if package.downloaded else "cached"
        print(
            f"{package.pin.id}#{package.pin.version} [{status}]"
            f" sha256 OK, license={declared_license!r} -> {package.path}"
        )
        if declared_license != package.pin.license:
            problems.append(
                f"{package.pin.id}: package.json license {declared_license!r}"
                f" != pinned {package.pin.license!r} in frozen config"
            )
    us_core = next(p for p in ensured if p.pin.id == config.fhir.us_core_package_id)
    print(f"\nDependencies declared by {us_core.pin.id}#{us_core.pin.version}:")
    for dep_id, dep_version in fhir_packages.declared_dependencies(us_core.package_json):
        pinned = config.fhir.package(dep_id, dep_version)
        marker = "pinned" if pinned and pinned.version == dep_version else "NOT PINNED"
        print(f"  {dep_id}#{dep_version} [{marker}]")
        if marker != "pinned":
            problems.append(f"declared dependency {dep_id}#{dep_version} is not pinned")
    unpinned = fhir_packages.unpinned_declared_dependencies(ensured, config.fhir.packages)
    if unpinned:
        print("\nDeclared but not required for the pinned validator closure (not fetched):")
        for declaring, dep_id, dep_version in unpinned:
            print(f"  {declaring} -> {dep_id}#{dep_version}")
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    return 0


def _cmd_setup_validator(args: argparse.Namespace) -> int:
    from auris.config import load_frozen_config
    from auris.setup import fhir_packages, validator

    config = load_frozen_config(args.config)
    path = validator.setup_validator(
        config,
        args.cache or validator.default_cache_dir(),
        args.package_cache or fhir_packages.default_cache_dir(),
        fetch=args.fetch,
    )
    print(f"validator {config.validator.version}: JAR size/SHA-256 OK")
    print(f"seeded {len(config.fhir.packages)} verified packages -> {path}")
    return 0


def _cmd_setup_terminology(args: argparse.Namespace) -> int:
    from auris.config import load_frozen_config
    from auris.setup.terminology import default_cache_dir, setup_terminology

    config = load_frozen_config(args.config)
    path = setup_terminology(
        config.terminology, args.cache or default_cache_dir(), fetch=args.fetch
    )
    print(f"terminology archives: checksums OK; index rebuilt -> {path}")
    return 0


def _cmd_checklist(args: argparse.Namespace) -> int:
    from auris.config import load_frozen_config
    from auris.contract import checklist
    from auris.setup import fhir_packages

    config = load_frozen_config(args.config)
    cache_dir = args.cache if args.cache is not None else fhir_packages.default_cache_dir()
    document = checklist.load_profile_checklists(config, cache_dir)
    sources = checklist.load_element_sources(args.sources)
    discrepancies = checklist.find_discrepancies(document)
    markdown = checklist.render_markdown(
        document,
        sources,
        discrepancies=discrepancies,
        package_pins=config.fhir.packages,
    )
    checklist.write_checklist_json(document, args.json_out)
    args.md_out.parent.mkdir(parents=True, exist_ok=True)
    args.md_out.write_text(markdown)
    print(f"wrote {args.json_out}")
    print(f"wrote {args.md_out}")
    print(f"discrepancies with PLAN/fhir-reference: {len(discrepancies)}")
    for discrepancy in discrepancies:
        print(f"  - {discrepancy}")
    problems = checklist.coverage_problems(document, sources)
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    print("element-source coverage: complete")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "extract":
        return _run_guarded(_cmd_extract, args)
    if args.command == "demo":
        print("not implemented yet (Slice 1)", file=sys.stderr)
        return 2
    if args.command == "setup" and args.setup_command == "fhir":
        return _run_guarded(_cmd_setup_fhir, args)
    if args.command == "setup" and args.setup_command == "terminology":
        return _run_guarded(_cmd_setup_terminology, args)
    if args.command == "setup" and args.setup_command == "validator":
        return _run_guarded(_cmd_setup_validator, args)
    if args.command == "checklist":
        return _run_guarded(_cmd_checklist, args)
    parser.print_help(sys.stderr)
    return 2


def _run_guarded(handler: Callable[[argparse.Namespace], int], args: argparse.Namespace) -> int:
    from auris.contract.checklist import ChecklistError
    from auris.setup.fhir_packages import FhirPackageError

    try:
        return handler(args)
    except (ChecklistError, FhirPackageError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
