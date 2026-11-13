"""Offline dataset verification. No Git subprocess, network or source downloads."""

import hashlib
import json
import os
import re
from pathlib import Path

from auris._storage import prepare_output_directory, write_private_text
from auris.config import DatasetPin
from auris.datasets.catalog import load_corpus, source_paths
from auris.eval.gold import canonical_json, relative_file
from auris.extract.leakage import check_leakage
from auris.extract.prompt import prompt_text
from auris.transcript import Transcript


def default_cache_dir() -> Path:
    from auris.cli import _repo_root

    return Path(os.environ.get("AURIS_CACHE", _repo_root() / "cache")) / "datasets"


def checkout_commit(root: Path) -> str:
    """Resolve detached HEAD, loose refs and packed refs directly, including worktrees."""
    git = root / ".git"
    if git.is_file():
        value = git.read_text("utf-8").strip()
        if not value.startswith("gitdir: "):
            raise ValueError("invalid dataset gitdir")
        git = (root / value.removeprefix("gitdir: ")).resolve()
    common = git
    if (git / "commondir").is_file():
        common = (git / (git / "commondir").read_text("utf-8").strip()).resolve()
    head = (git / "HEAD").read_text("utf-8").strip()
    visited = set()
    while head.startswith("ref: "):
        ref = head[5:]
        if not re.fullmatch(r"refs/[A-Za-z0-9_./-]+", ref) or ".." in ref or ref in visited:
            raise ValueError("invalid or cyclic dataset HEAD ref")
        visited.add(ref)
        loose = relative_file(common, ref)
        if loose.is_file():
            head = loose.read_text("utf-8").strip()
        else:
            packed = common / "packed-refs"
            matches = [
                line.split()[0]
                for line in packed.read_text("utf-8").splitlines()
                if len(line.split()) == 2 and line.split()[1] == ref
            ]
            if len(matches) != 1:
                raise ValueError("dataset HEAD ref is unresolved")
            head = matches[0]
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("invalid dataset commit")
    return head


def verify_checkout(root: Path, pin: DatasetPin, manifest: Path) -> dict[str, str]:
    if checkout_commit(root) != pin.commit:
        raise ValueError(f"{pin.id}: dataset commit mismatch")
    raw = manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != pin.manifest_sha256:
        raise ValueError(f"{pin.id}: source manifest checksum mismatch")
    document = json.loads(raw)
    if (document["dataset"], document["commit"]) != (pin.id, pin.commit):
        raise ValueError("dataset source manifest pin mismatch")
    expected = document["files"]
    if set(expected) != set(source_paths(root, pin.id)):
        raise ValueError("dataset source file inventory mismatch")
    for path, checksum in expected.items():
        if hashlib.sha256(relative_file(root, path).read_bytes()).hexdigest() != checksum:
            raise ValueError(f"{pin.id}: dataset source checksum mismatch: {path}")
    return expected


def verified_corpus(root: Path, pin: DatasetPin, manifest: Path) -> dict[str, Transcript]:
    expected = verify_checkout(root, pin, manifest)

    def read(path: str) -> str:
        raw = relative_file(root, path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected[path]:
            raise ValueError("dataset source changed after verification")
        return raw.decode("utf-8-sig")

    return load_corpus(pin, read, tuple(expected))


def setup_datasets(
    pins: tuple[DatasetPin, ...], cache: Path, manifests: Path
) -> dict[str, dict[str, Transcript]]:
    """Prepare private annotation inputs; report all audits, then refuse any leakage.

    A prompt hit does not prevent local annotation preparation, but makes setup
    fail and must also refuse inference in the evaluation harness.
    """
    from auris.datasets.splits import verify_splits

    corpora = {
        pin.id: verified_corpus(cache / pin.id, pin, manifests / f"{pin.id}-sources.json")
        for pin in pins
    }
    output = cache / "prepared"
    if not output.exists():
        prepare_output_directory(output)
    reports = []
    for pin in pins:
        corpus = corpora[pin.id]
        # Newlines retain segment boundaries, including the unused eligible pool.
        report = check_leakage(
            prompt_text(),
            ["\n".join(s.text for s in t.segments) for t in corpus.values()],
            clinical_policy="warn",
        )
        write_private_text(output / f"{pin.id}-leakage.json", canonical_json(report.metadata()))
        reports.append(report)
        verify_splits(manifests, pin.id, corpus)
    for pin in pins:
        folder = output / pin.id
        if not folder.exists():
            prepare_output_directory(folder)
        for key, transcript in corpora[pin.id].items():
            transcript.save(folder / f"{key}.json")
        write_private_text(
            folder / "source-manifest.json",
            (manifests / f"{pin.id}-sources.json").read_text("utf-8"),
        )
    for report in reports:
        report.raise_if_failed()
    return corpora
