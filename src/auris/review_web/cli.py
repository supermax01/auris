"""The step-12a review command: view, approve, dismiss and undo."""

import argparse
from pathlib import Path


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("review", help="Local clinician review of a completed run")
    commands = parser.add_subparsers(dest="review_command", required=True)
    server = commands.add_parser("serve", help="Open the loopback-only clinician review page")
    server.add_argument("--run", type=Path, required=True)


def command(args: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from auris.review_web.server import serve
    from auris.review_web.service import ReviewSession

    try:
        session = ReviewSession(args.run)
    except ValidationError:
        raise ValueError("invalid review artifacts; verify the run's retained inputs") from None
    serve(session)
    return 0
