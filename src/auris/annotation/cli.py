"""Annotation CLI; deliberately does not import extraction or evaluation runners."""

import argparse
import json
import os
from pathlib import Path

from auris.annotation.models import Assignment
from auris.annotation.pilot import create_pilot
from auris.datasets.splits import PublicSplit


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    from auris.cli import _repo_root

    parser = subparsers.add_parser("annotate", help="Human-only local annotation and pilot kit")
    commands = parser.add_subparsers(dest="annotation_command", required=True)
    pilot = commands.add_parser("pilot", help="Prepare a timed, independent dev-only pilot")
    pilot.add_argument("--dataset", type=Path, required=True)
    pilot.add_argument("--n", type=int, default=8)
    pilot.add_argument("--seed", type=int, required=True)
    pilot.add_argument("--out", type=Path, required=True)
    server = commands.add_parser("serve", help="Serve a private blind annotation page on loopback")
    server.add_argument("--assignment", type=Path, required=True)
    server.add_argument("--workdir", type=Path, required=True)
    server.add_argument("--locked-test-run", action="store_true")
    server.add_argument("--confirm-locked-test-rerun", action="store_true")
    server.add_argument("--manifests", type=Path, default=_repo_root() / "eval/datasets")
    agreement = commands.add_parser("agreement", help="Compare completed independent human work")
    agreement.add_argument("--a", type=Path, required=True)
    agreement.add_argument("--b", type=Path, required=True)
    agreement.add_argument("--assignment", type=Path)
    agreement.add_argument("--out", type=Path, required=True)
    agreement.add_argument("--round", type=int, default=1)
    agreement.add_argument("--overhead-hours", type=float, default=65)
    agreement.add_argument("--seed", type=int, default=1729)
    adjudicate = commands.add_parser(
        "adjudicate", help="Build and serve a third-clinician assignment"
    )
    adjudicate.add_argument("--a", type=Path, required=True)
    adjudicate.add_argument("--b", type=Path, required=True)
    adjudicate.add_argument("--assignment", type=Path, required=True)
    adjudicate.add_argument("--adjudicator-id", required=True)
    adjudicate.add_argument("--clinically-trained", action="store_true", required=True)
    adjudicate.add_argument("--out", type=Path, required=True)
    adjudicate.add_argument("--prepare-only", action="store_true")
    export = commands.add_parser(
        "export-gold", help="Package completed adjudication for the harness"
    )
    export.add_argument("--assignment", type=Path, required=True)
    export.add_argument("--adjudicated", type=Path, required=True)
    export.add_argument("--intakes", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True)
    for command in (adjudicate, export):
        command.add_argument("--locked-test-run", action="store_true")
        command.add_argument("--confirm-locked-test-rerun", action="store_true")
        command.add_argument("--manifests", type=Path, default=_repo_root() / "eval/datasets")
    for command in (pilot, server, adjudicate, export):
        command.add_argument(
            "--cache",
            type=Path,
            default=Path(os.environ.get("AURIS_CACHE", _repo_root() / "cache")),
        )
        command.add_argument(
            "--guidelines", type=Path, default=_repo_root() / "docs/annotation/guidelines.md"
        )


def command(args: argparse.Namespace) -> int:
    from auris._storage import prepare_output_directory, write_private_text
    from auris.annotation.adjudication import AdjudicationAssignment, AdjudicationSession
    from auris.annotation.comparison import load_pair
    from auris.eval.gold import canonical_json

    if args.annotation_command == "agreement":
        from auris.annotation.agreement import agreement, markdown

        assignment = (
            Assignment.model_validate_json(args.assignment.read_bytes())
            if args.assignment
            else None
        )
        a, b = load_pair(args.a, args.b, assignment)
        report = agreement(
            a,
            b,
            assignment,
            round_number=args.round,
            overhead_hours=args.overhead_hours,
            seed=args.seed,
        )
        readable = markdown(report)
        prepare_output_directory(args.out)
        write_private_text(args.out / "agreement.json", canonical_json(report))
        write_private_text(args.out / "agreement.md", readable)
        print(readable)
        return 0
    if args.annotation_command == "export-gold":
        from auris.annotation.export import export_gold

        manifest = export_gold(
            AdjudicationAssignment.model_validate_json(args.assignment.read_bytes()),
            args.adjudicated,
            args.intakes,
            args.out,
            cache=args.cache,
            manifests=args.manifests,
            locked_run=args.locked_test_run,
            confirmed=args.confirm_locked_test_rerun,
        )
        print(f"Wrote adjudicated gold: {manifest}. Test scoring remains ledger-locked.")
        return 0
    if args.annotation_command == "pilot":
        dataset = PublicSplit.model_validate_json(args.dataset.read_text("utf-8"))
        create_pilot(
            dataset,
            args.out,
            args.guidelines.read_bytes(),
            n=args.n,
            seed=args.seed,
            cache=args.cache,
        )
        print(
            "Prepared independent assignments A/B, printable pilot sheet, "
            "and FICTIONAL FY2027 intake anchors."
        )
        return 0
    from auris.annotation.server import serve
    from auris.annotation.service import AnnotationSession

    if args.annotation_command == "adjudicate":
        source = Assignment.model_validate_json(args.assignment.read_bytes())
        a, b = load_pair(args.a, args.b, source)
        assignment = AdjudicationAssignment(
            assignment=source,
            a=a,
            b=b,
            adjudicator_id=args.adjudicator_id,
            clinically_trained=args.clinically_trained,
        )
        prepare_output_directory(args.out)
        path = args.out / "adjudication-assignment.json"
        write_private_text(path, canonical_json(assignment.model_dump(mode="json")))
        print(f"Prepared third-clinician assignment: {path}")
        if args.prepare_only:
            return 0
        workdir = args.out / "work"
    else:
        raw = json.loads(args.assignment.read_bytes())
        assignment = (
            AdjudicationAssignment.model_validate(raw)
            if raw.get("schema_version") == "adjudication-assignment-1"
            else Assignment.model_validate(raw)
        )
        workdir = args.workdir
    session_class = (
        AdjudicationSession if isinstance(assignment, AdjudicationAssignment) else AnnotationSession
    )
    serve(
        session_class(
            assignment,
            cache=args.cache,
            manifests=args.manifests,
            workdir=workdir,
            guidelines=args.guidelines.read_bytes(),
            locked_run=args.locked_test_run,
            confirmed=args.confirm_locked_test_rerun,
        )
    )
    return 0
