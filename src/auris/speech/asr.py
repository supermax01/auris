"""Speech-to-text with faster-whisper and the converted, pinned Whisper model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from auris.config import AsrDecoding

if TYPE_CHECKING:
    import numpy as np


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str
    probability: float


@dataclass(frozen=True)
class AsrSegment:
    start: float
    end: float
    text: str
    words: tuple[Word, ...]


# faster-whisper keeps at most this many hint tokens and drops the rest without
# a word; a list that does not fit is refused instead.
MAX_HINT_TOKENS = 448 // 2 - 1


class Transcriber:
    """One loaded Whisper model; deterministic decoding from the frozen settings.

    ``hotwords`` is the hint text (``AsrHints.prompt``): None when hints are off.
    """

    def __init__(
        self,
        model_dir: Path,
        decoding: AsrDecoding,
        *,
        threads: int = 0,
        hotwords: str | None = None,
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        """``device``/``compute_type``: the laptop default is CPU int8. Evaluation on
        a rented GPU uses ``cuda`` with ``int8_float16`` (the same int8 weights,
        float16 arithmetic); results then differ slightly and record both."""
        try:
            from faster_whisper import WhisperModel
        except ImportError as error:  # pragma: no cover - depends on the optional extra
            raise ValueError(
                "speech support is not installed; run uv sync --extra speech"
            ) from error
        self.decoding = decoding
        self.model = WhisperModel(
            str(model_dir), device=device, compute_type=compute_type, cpu_threads=threads
        )
        self.device, self.compute_type = device, compute_type
        # The per-window prompt: the style sentence (when asked for) and any hints.
        # With the style in every window it is not also the first window's start
        # prompt, so every window, the first included, sees it exactly once.
        style = decoding.initial_prompt if decoding.style_every_window else None
        self.initial_prompt = None if decoding.style_every_window else decoding.initial_prompt
        hotwords = " ".join(part for part in (style, hotwords) if part) or None
        if hotwords is not None:
            tokens = len(
                self.model.hf_tokenizer.encode(" " + hotwords.strip(), add_special_tokens=False).ids
            )
            if tokens > MAX_HINT_TOKENS:
                what = "the style sentence plus the speech hints" if style else "speech hints"
                raise ValueError(
                    f"{what} are {tokens} tokens; Whisper keeps only {MAX_HINT_TOKENS}"
                )
        self.hotwords = hotwords

    def transcribe(self, audio: np.ndarray) -> list[AsrSegment]:
        decoding = self.decoding
        segments, _ = self.model.transcribe(
            audio,
            language=decoding.language,
            beam_size=decoding.beam_size,
            temperature=decoding.temperature,
            condition_on_previous_text=decoding.condition_on_previous_text,
            vad_filter=decoding.vad_filter,
            word_timestamps=True,
            initial_prompt=self.initial_prompt,
            hotwords=self.hotwords,
        )
        result = []
        for segment in segments:  # a generator: decoding happens here
            words = tuple(
                Word(float(w.start), float(w.end), w.word, float(w.probability))
                for w in (segment.words or ())
                if w.word.strip()
            )
            if words:
                result.append(
                    AsrSegment(float(segment.start), float(segment.end), segment.text, words)
                )
        return result
