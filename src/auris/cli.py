"""Command-line interface for Auris."""

from __future__ import annotations

import argparse
import sys

from auris import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auris", description="Auris (research software)")
    parser.add_argument(
        "--version",
        action="version",
        version=f"auris {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("demo", help="Run the bundled demo (not implemented yet)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "demo":
        print("not implemented yet (Slice 1)", file=sys.stderr)
        return 2
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
