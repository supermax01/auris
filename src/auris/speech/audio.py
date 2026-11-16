"""Load audio as 16 kHz mono float32, and mix separate tracks into one."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

SAMPLE_RATE = 16_000


def load(path: Path) -> np.ndarray:
    """Decode any common audio file to 16 kHz mono float32 (PyAV, via faster-whisper)."""
    from faster_whisper.audio import decode_audio

    if not path.is_file():
        raise ValueError(f"audio file not found: {path.name}")
    audio = decode_audio(str(path), sampling_rate=SAMPLE_RATE)
    if audio.size == 0:
        raise ValueError(f"audio file has no samples: {path.name}")
    return audio


def mix(tracks: list[np.ndarray]) -> np.ndarray:
    """Sum separate tracks into one, as a single room microphone would hear them."""
    import numpy as np

    if not tracks:
        raise ValueError("nothing to mix")
    length = max(track.size for track in tracks)
    mixed = np.zeros(length, dtype=np.float32)
    for track in tracks:
        mixed[: track.size] += track
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 1.0:
        mixed /= peak
    return mixed


def seconds(audio: np.ndarray) -> float:
    return audio.size / SAMPLE_RATE
