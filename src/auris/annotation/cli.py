"""Annotation CLI; deliberately does not import extraction or evaluation runners."""

import argparse
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
    for command in (pilot, server):
        command.add_argument(
            "--cache",
            type=Path,
            default=Path(os.environ.get("AURIS_CACHE", _repo_root() / "cache")),
        )
        command.add_argument(
            "--guidelines", type=Path, default=_repo_root() / "docs/annotation/guidelines.md"
        )


def command(args: argparse.Namespace) -> int:
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

    assignment = Assignment.model_validate_json(args.assignment.read_text("utf-8"))
    serve(
        AnnotationSession(
            assignment,
            cache=args.cache,
            manifests=args.manifests,
            workdir=args.workdir,
            guidelines=args.guidelines.read_bytes(),
            locked_run=args.locked_test_run,
            confirmed=args.confirm_locked_test_rerun,
        )
    )
    return 0
