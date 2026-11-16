"""`auris app serve`: the engine the desktop window talks to."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path


def default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Auris"
    return Path.home() / ".local" / "share" / "auris"


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("app", help="The desktop app's local engine")
    commands = parser.add_subparsers(dest="app_command", required=True)
    server = commands.add_parser("serve", help="Serve the app on 127.0.0.1 for its window")
    server.add_argument("--data", type=Path, default=None, help="private data folder")
    server.add_argument("--ui", type=Path, default=None, help="built interface (ui/dist)")
    server.add_argument("--demo", action="store_true", help="fill today with a fictional day")
    server.add_argument("--json", action="store_true", help="print one JSON ready event")
    server.add_argument(
        "--exit-with-parent", action="store_true", help="stop when standard input closes"
    )


def command(args: argparse.Namespace) -> int:
    from auris.app.api import AppSession
    from auris.app.demo import demo_transcript, seed_demo_day
    from auris.app.jobs import NoteJobs, subprocess_runner
    from auris.app.server import serve
    from auris.app.store import VisitStore

    data_dir = args.data or default_data_dir()
    store = VisitStore(data_dir)
    if args.demo:
        seed_demo_day(store, date.today())
    root = Path.cwd()
    jobs = NoteJobs(data_dir, store, subprocess_runner(root))
    jobs.start()
    ui_dir = args.ui if args.ui is not None else root / "ui" / "dist"
    serve(
        AppSession(
            store,
            jobs=jobs,
            demo=args.demo,
            demo_transcript=demo_transcript(root) if args.demo else None,
        ),
        ui_dir if ui_dir.is_dir() else None,
        json_events=args.json,
        exit_with_parent=args.exit_with_parent,
    )
    return 0
