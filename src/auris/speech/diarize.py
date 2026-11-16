"""Who spoke when, for a single room microphone.

Silero VAD (bundled with faster-whisper) finds speech; fixed-length windows
inside the speech are turned into SpeechBrain ECAPA voice fingerprints; the
fingerprints are clustered into speakers (cosine k-means, fixed seed, so a
rerun gives the same answer); each transcribed word takes the speaker of
the windows around it. Speakers are only "speaker 1", "speaker 2"; who is
who is decided separately (:mod:`auris.speech.roles`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from auris.speech.asr import Word
from auris.speech.audio import SAMPLE_RATE

if TYPE_CHECKING:
    import numpy as np

WINDOW_SECONDS = 1.5
HOP_SECONDS = 0.75
MIN_REGION_SECONDS = 0.4
NEAREST_WINDOW_SECONDS = 2.0


@dataclass(frozen=True)
class Window:
    start: float
    end: float
    region: int = 0

    @property
    def middle(self) -> float:
        return (self.start + self.end) / 2


def speech_regions(audio: np.ndarray) -> list[tuple[float, float]]:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    stamps = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=300))
    return [(s["start"] / SAMPLE_RATE, s["end"] / SAMPLE_RATE) for s in stamps]


def windows(regions: list[tuple[float, float]], duration: float) -> list[Window]:
    """Windows inside each speech region, never reaching into a neighbouring one.

    A short region (a lone "okay" between the other speaker's sentences) is
    one window of exactly its own audio, so its fingerprint is its own voice.
    """
    result: list[Window] = []
    for number, (start, end) in enumerate(regions):
        end = min(end, duration)
        length = end - start
        if length < MIN_REGION_SECONDS:
            continue
        if length <= WINDOW_SECONDS:
            result.append(Window(start, end, number))
            continue
        position = start
        while position + WINDOW_SECONDS <= end:
            result.append(Window(position, position + WINDOW_SECONDS, number))
            position += HOP_SECONDS
        if result[-1].end < end - HOP_SECONDS / 2:
            result.append(Window(end - WINDOW_SECONDS, end, number))
    return result


class SpeakerEmbedder:
    """SpeechBrain ECAPA-TDNN (spkrec-ecapa-voxceleb), weights only."""

    def __init__(self, checkpoint: Path) -> None:
        import torch
        from speechbrain.lobes.features import Fbank
        from speechbrain.lobes.models.ECAPA_TDNN import ECAPA_TDNN

        self.torch = torch
        self.features = Fbank(n_mels=80)
        # Architecture from the pinned model's hyperparams.yaml.
        self.model = ECAPA_TDNN(
            input_size=80,
            channels=[1024, 1024, 1024, 1024, 3072],
            kernel_sizes=[5, 3, 3, 3, 1],
            dilations=[1, 2, 3, 4, 1],
            attention_channels=128,
            lin_neurons=192,
        )
        state = torch.load(checkpoint, map_location="cpu", weights_only=True)
        self.model.load_state_dict(state)
        self.model.eval()

    def embed(self, audio: np.ndarray, spans: list[Window], batch: int = 64) -> np.ndarray:
        import numpy as np

        torch = self.torch
        width = int(WINDOW_SECONDS * SAMPLE_RATE)
        vectors = []
        with torch.inference_mode():
            for first in range(0, len(spans), batch):
                chunk = np.zeros((len(spans[first : first + batch]), width), dtype=np.float32)
                for row, span in enumerate(spans[first : first + batch]):
                    piece = audio[int(span.start * SAMPLE_RATE) : int(span.end * SAMPLE_RATE)]
                    # A short region is repeated to fill the window (no other voice, no silence).
                    chunk[row] = np.resize(piece, width) if piece.size else 0.0
                feats = self.features(torch.from_numpy(chunk))
                feats = feats - feats.mean(dim=1, keepdim=True)  # sentence mean normalisation
                vectors.append(self.model(feats).squeeze(1).numpy())
        matrix = np.concatenate(vectors) if vectors else np.zeros((0, 192), dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.maximum(norms, 1e-9)


def cluster(
    embeddings: np.ndarray, speakers: int, *, seed: int = 0, restarts: int = 10
) -> np.ndarray:
    """Cosine k-means with k-means++ seeding; the best of several fixed-seed restarts."""
    import numpy as np

    count = len(embeddings)
    if count == 0:
        return np.zeros(0, dtype=int)
    if count <= speakers:
        return np.arange(count)
    rng = np.random.default_rng(seed)
    best_labels, best_score = np.zeros(count, dtype=int), -np.inf
    for _ in range(restarts):
        centres = [embeddings[rng.integers(count)]]
        while len(centres) < speakers:
            similarity = np.max(embeddings @ np.stack(centres).T, axis=1)
            weights = np.clip(1 - similarity, 0, None) ** 2
            total = weights.sum()
            pick = rng.choice(count, p=weights / total) if total > 0 else rng.integers(count)
            centres.append(embeddings[pick])
        centre = np.stack(centres)
        labels = np.zeros(count, dtype=int)
        for _ in range(100):
            labels_next = np.argmax(embeddings @ centre.T, axis=1)
            if np.array_equal(labels_next, labels) and _ > 0:
                break
            labels = labels_next
            for k in range(speakers):
                members = embeddings[labels == k]
                if len(members):
                    mean = members.mean(axis=0)
                    centre[k] = mean / max(np.linalg.norm(mean), 1e-9)
        score = float(np.sum(np.max(embeddings @ centre.T, axis=1)))
        if score > best_score:
            best_labels, best_score = labels.copy(), score
    # Number speakers by first appearance, so labels are stable and readable.
    order: dict[int, int] = {}
    for label in best_labels:
        order.setdefault(int(label), len(order))
    return np.array([order[int(label)] for label in best_labels])


def smooth(labels: list[int], regions: list[int] | None = None, width: int = 1) -> list[int]:
    """Majority vote over neighbouring windows of the same speech region.

    Removes one-window flickers inside continuous speech, but never across a
    pause: a separate short reply keeps its own speaker.
    """
    regions = regions if regions is not None else [0] * len(labels)
    result = []
    for index in range(len(labels)):
        around = [
            labels[i]
            for i in range(max(0, index - width), min(len(labels), index + width + 1))
            if regions[i] == regions[index]
        ]
        result.append(
            max(set(around), key=lambda value: (around.count(value), value == labels[index]))
        )
    return result


def assign_words(
    words: list[Word],
    spans: list[Window],
    labels: list[int],
    unwindowed: list[tuple[float, float]] = (),
) -> list[int | None]:
    """Each word takes the majority speaker of the windows covering its middle.

    A word inside a speech stretch too short to fingerprint (``unwindowed``)
    stays unattributed rather than borrowing a neighbour's speaker, which is
    usually the other person.
    """
    import bisect

    starts = [span.start for span in spans]
    result: list[int | None] = []
    for word in words:
        middle = (word.start + word.end) / 2
        votes: dict[int, float] = {}
        index = bisect.bisect_right(starts, middle)
        for position in range(max(0, index - 4), index):
            span = spans[position]
            if span.start <= middle <= span.end:
                weight = 1.0 - abs(middle - span.middle) / WINDOW_SECONDS
                votes[labels[position]] = votes.get(labels[position], 0.0) + weight
        if votes:
            result.append(max(votes, key=lambda label: votes[label]))
            continue
        if any(start <= middle <= end for start, end in unwindowed):
            result.append(None)
            continue
        nearest = min(range(len(spans)), key=lambda i: abs(spans[i].middle - middle), default=None)
        if nearest is not None and abs(spans[nearest].middle - middle) <= NEAREST_WINDOW_SECONDS:
            result.append(labels[nearest])
        else:
            result.append(None)
    return result


def diarize(
    audio: np.ndarray, words: list[Word], embedder: SpeakerEmbedder, speakers: int = 2
) -> list[int | None]:
    """Speaker number (0-based) for each word, or None where no voice evidence is near."""
    regions = speech_regions(audio)
    spans = windows(regions, audio.size / SAMPLE_RATE)
    if not spans:
        return [None] * len(words)
    covered = {span.region for span in spans}
    unwindowed = [region for number, region in enumerate(regions) if number not in covered]
    raw = [int(label) for label in cluster(embedder.embed(audio, spans), speakers)]
    labels = smooth(raw, [span.region for span in spans])
    return assign_words(words, spans, labels, unwindowed)
