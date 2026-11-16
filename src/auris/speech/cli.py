"""`auris transcribe` and `auris setup asr`."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from auris.transcript import SpeakerRole


def _track(value: str):
    from auris.speech.transcribe import Track

    role, sep, path = value.partition("=")
    if not sep:
        return Track(Path(value))
    try:
        return Track(Path(path), SpeakerRole(role))
    except ValueError as error:
        choices = ", ".join(role.value for role in SpeakerRole)
        raise argparse.ArgumentTypeError(f"role must be one of {choices}") from error


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "transcribe",
        help="Audio to a timed, speaker-labelled transcript (needs the speech extra)",
        description=(
            "One --audio file is a single room microphone: speakers are found by voice and "
            "roles are scored. Several --audio files are one track per speaker; give each "
            "its role as ROLE=PATH (clinician, patient, companion, other) or let Auris score it."
        ),
    )
    parser.add_argument(
        "--audio", action="append", type=_track, required=True, metavar="[ROLE=]PATH"
    )
    parser.add_argument("--speakers", type=int, default=2, help="speakers in a room recording")
    parser.add_argument("--out", type=Path, required=True, help="transcript.json to write")
    parser.add_argument("--report", type=Path, help="default: <out>.asr.json")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--threads", type=int, default=0)


def add_setup_parser(setup_subparsers: argparse._SubParsersAction) -> None:
    parser = setup_subparsers.add_parser(
        "asr", help="Fetch, verify and convert the pinned speech models (needs the speech extra)"
    )
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--fetch", action="store_true", help="download missing pinned files")


def report_fields(config) -> dict:
    from auris.speech.spelling import spelling_report

    laptop = config.asr.laptop
    return {
        "asr_model": f"{laptop.source_model}@{laptop.revision}",
        "conversion": f"{laptop.conversion.tool} {laptop.conversion.tool_version} "
        f"{laptop.conversion.quantization}",
        "decoding": config.asr.decoding.model_dump(),
        "hints": config.asr.hints.report(),
        "spelling": spelling_report(config),
        "segmentation": config.asr.segmentation.report(),
        "speaker_model": f"{config.asr.speaker_embedding.source_model}@"
        f"{config.asr.speaker_embedding.revision}",
    }


def command(args: argparse.Namespace, cache: Path) -> int:
    from auris._storage import ensure_private_parent, write_private_text
    from auris.config import load_frozen_config
    from auris.setup.asr import asr_paths
    from auris.speech import audio as audio_io
    from auris.speech.asr import Transcriber
    from auris.speech.transcribe import transcribe_room, transcribe_tracks

    if len({track.role is None for track in args.audio}) > 1:
        raise ValueError("give a role for every track (ROLE=PATH) or for none")
    report_path = args.report or args.out.with_name(args.out.stem + ".asr.json")
    ensure_private_parent(args.out)
    ensure_private_parent(report_path)
    config = load_frozen_config(args.config)
    model_dir, speaker_checkpoint = asr_paths(config.asr, cache)
    transcriber = Transcriber(
        model_dir, config.asr.decoding, threads=args.threads, hotwords=config.asr.hints.prompt
    )
    speller = None
    if config.asr.spelling.enabled:
        from auris.speech.spelling import load_speller

        speller = load_speller(config, cache)
    if len(args.audio) == 1 and args.audio[0].role is None:
        from auris.speech.diarize import SpeakerEmbedder

        samples = audio_io.load(args.audio[0].path)
        transcript, report = transcribe_room(
            samples,
            transcriber,
            SpeakerEmbedder(speaker_checkpoint),
            speakers=args.speakers,
            speller=speller,
            segmentation=config.asr.segmentation,
        )
    else:
        transcript, report = transcribe_tracks(
            args.audio, transcriber, speller, config.asr.segmentation
        )
    transcript.save(args.out)
    from auris.speech.transcribe import pipeline_fingerprint

    report = {
        **report_fields(config),
        "fingerprint": pipeline_fingerprint(),
        **report,
        "segments": len(transcript.segments),
    }
    write_private_text(report_path, json.dumps(report, indent=2) + "\n")
    roles = report["roles"]
    print(
        f"{len(transcript.segments)} segments, {report['audio_seconds']:.0f} s of audio in "
        f"{report['processing_seconds']:.0f} s; roles: "
        + ", ".join(f"{s['speaker']}={s['role']}" for s in roles["speakers"])
    )
    if roles["method"] != "given" and not roles.get("decided"):
        print(
            "Who is who is not clear; every speaker stays unknown until a person sets it in review."
        )
    return 0


def setup_command(args: argparse.Namespace, cache: Path) -> int:
    from auris.config import load_frozen_config
    from auris.setup.asr import setup_asr

    config = load_frozen_config(args.config)
    model_dir, speaker = setup_asr(config.asr, cache, fetch=args.fetch)
    print(f"Speech model ready: {model_dir}")
    print(f"Speaker model ready: {speaker}")
    return 0
