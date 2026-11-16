"""Setup-only download, verification and conversion of the speech models.

Speech-to-text uses OpenAI's original Whisper large-v3-turbo,
converted by Auris itself to CTranslate2 INT8 (no third-party copies), and
"who spoke when" uses SpeechBrain's ECAPA speaker embeddings. Every source
file is pinned (SHA-256 for large files, Git blob SHA-1 for small ones, as
the Hugging Face listing gives them) and every converted file is pinned by
SHA-256, so a conversion that does not reproduce the pinned bytes is refused.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

from auris.config import (
    AsrConfig,
    AsrConversion,
    PinnedFile,
    SpeakerEmbeddingConfig,
    WordListPin,
)
from auris.setup.fhir_packages import sha256_file

Fetcher = Callable[[str, Path], None]


def _fetch(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "auris-setup"})
    with urllib.request.urlopen(request, timeout=300) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def git_blob_sha1(path: Path) -> str:
    """The Git object id of a file: SHA-1 over "blob <size>\\0" and the bytes."""
    digest = hashlib.sha1(f"blob {path.stat().st_size}\0".encode())  # noqa: S324 - Git's own id
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_pinned(path: Path, pin: PinnedFile) -> None:
    if path.stat().st_size != pin.bytes:
        raise ValueError(f"{pin.name}: size mismatch; refusing the file")
    if pin.sha256 is not None and sha256_file(path) != pin.sha256:
        raise ValueError(f"{pin.name}: SHA-256 mismatch; refusing the file")
    if pin.git_blob_sha1 is not None and git_blob_sha1(path) != pin.git_blob_sha1:
        raise ValueError(f"{pin.name}: Git blob id mismatch; refusing the file")


def _ensure_file(
    repo: str, revision: str, pin: PinnedFile, folder: Path, *, fetch: bool, fetcher: Fetcher
) -> Path:
    destination = folder / pin.name
    if destination.exists():
        verify_pinned(destination, pin)
        return destination
    url = f"https://huggingface.co/{repo}/resolve/{revision}/{pin.name}"
    if not fetch:
        raise ValueError(
            f"pinned speech model file missing; run auris setup asr --fetch\nSource: {url}"
        )
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=folder, suffix=".download")
    os.close(fd)
    temporary = Path(name)
    try:
        fetcher(url, temporary)
        verify_pinned(temporary, pin)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def asr_root(config: AsrConfig, cache: Path) -> Path:
    laptop = config.laptop
    return cache / "asr" / f"{laptop.source_model.replace('/', '--')}--{laptop.revision[:12]}"


def converted_dir(config: AsrConfig, cache: Path) -> Path:
    conversion = config.laptop.conversion
    return asr_root(config, cache) / f"ct2-{conversion.tool_version}-{conversion.quantization}"


def speaker_model_path(config: AsrConfig, cache: Path) -> Path:
    pin = config.speaker_embedding
    folder = cache / "asr" / f"{pin.source_model.replace('/', '--')}--{pin.revision[:12]}"
    return folder / pin.file.name


def verify_converted(folder: Path, conversion: AsrConversion) -> None:
    for pin in conversion.files:
        path = folder / pin.name
        if not path.exists():
            raise ValueError(f"converted speech model incomplete ({pin.name}); run auris setup asr")
        if sha256_file(path) != pin.sha256:
            raise ValueError(
                f"converted speech model {pin.name} does not match its pin; "
                "delete the folder and run auris setup asr again"
            )


def convert(source: Path, destination: Path, conversion: AsrConversion) -> None:
    """Convert OpenAI's original weights with the pinned CTranslate2 version."""
    try:
        import ctranslate2
        from ctranslate2.converters import TransformersConverter
    except ImportError as error:  # pragma: no cover - depends on the optional extra
        raise ValueError("speech support is not installed; run uv sync --extra speech") from error
    if ctranslate2.__version__ != conversion.tool_version:
        raise ValueError(
            f"CTranslate2 {ctranslate2.__version__} installed, the pin says "
            f"{conversion.tool_version}; the converted bytes would not match"
        )
    staging = destination.with_name(destination.name + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    TransformersConverter(str(source), copy_files=list(conversion.copy_files)).convert(
        str(staging), quantization=conversion.quantization
    )
    verify_converted(staging, conversion)
    staging.replace(destination)


def setup_asr(
    config: AsrConfig,
    cache: Path,
    *,
    fetch: bool = False,
    fetcher: Fetcher | None = None,
    converter: Callable[[Path, Path, AsrConversion], None] = convert,
) -> tuple[Path, Path]:
    """Return (converted Whisper folder, speaker-embedding checkpoint), verified."""
    fetcher = fetcher or _fetch
    output = converted_dir(config, cache)
    if not output.exists():
        source = asr_root(config, cache) / "source"
        for pin in config.laptop.source_files:
            _ensure_file(
                config.laptop.source_model,
                config.laptop.revision,
                pin,
                source,
                fetch=fetch,
                fetcher=fetcher,
            )
        converter(source, output, config.laptop.conversion)
    verify_converted(output, config.laptop.conversion)
    speaker = ensure_speaker_model(config.speaker_embedding, cache, fetch=fetch, fetcher=fetcher)
    if config.spelling.word_list is not None:
        ensure_word_list(config.spelling.word_list, cache, fetch=fetch, fetcher=fetcher)
    return output, speaker


def word_list_path(pin: WordListPin, cache: Path) -> Path:
    return cache / "asr" / "words" / pin.sha256[:12] / "words.txt"


def ensure_word_list(
    pin: WordListPin, cache: Path, *, fetch: bool, fetcher: Fetcher | None = None
) -> Path:
    """The pinned English word list for drug-name spelling, verified."""
    destination = word_list_path(pin, cache)
    if not destination.exists():
        if not fetch:
            raise ValueError(f"English word list missing; run auris setup asr --fetch\n{pin.url}")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=destination.parent, suffix=".download")
        os.close(fd)
        temporary = Path(name)
        try:
            (fetcher or _fetch)(pin.url, temporary)
            _verify_word_list(temporary, pin)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    _verify_word_list(destination, pin)
    return destination


def _verify_word_list(path: Path, pin: WordListPin) -> None:
    if path.stat().st_size != pin.bytes or sha256_file(path) != pin.sha256:
        raise ValueError("English word list does not match its pin; refusing it")


def ensure_speaker_model(
    pin: SpeakerEmbeddingConfig, cache: Path, *, fetch: bool, fetcher: Fetcher | None = None
) -> Path:
    folder = cache / "asr" / f"{pin.source_model.replace('/', '--')}--{pin.revision[:12]}"
    return _ensure_file(
        pin.source_model, pin.revision, pin.file, folder, fetch=fetch, fetcher=fetcher or _fetch
    )


def asr_paths(config: AsrConfig, cache: Path) -> tuple[Path, Path]:
    """The verified models for a run; never downloads or converts."""
    output = converted_dir(config, cache)
    verify_converted(output, config.laptop.conversion)
    speaker = speaker_model_path(config, cache)
    if not speaker.exists():
        raise ValueError("speaker model missing; run auris setup asr --fetch")
    verify_pinned(speaker, config.speaker_embedding.file)
    return output, speaker
