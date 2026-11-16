"""Drug-name hints for speech-to-text: the fixed list, switching it, passing it to
Whisper, refusing a list Whisper would cut, and counting drug words the
transcript made up (the hints' main risk)."""

import re
import sys
import types

import pytest

from auris.config import AsrHints, load_frozen_config, with_hints
from auris.speech.cli import report_fields


def _plain(text):
    return re.sub(r"[^\w\s]", "", text.lower())


HINTS = {"enabled": False, "source": "test list", "words": ("lisinopril", "co-amoxiclav")}


# --- The list ---------------------------------------------------------------


@pytest.mark.parametrize(
    "change, message",
    [
        ({"words": ("Lisinopril",)}, "lower-case"),
        ({"words": ("tab 5",)}, "lower-case"),
        ({"words": ("lisinopril", "lisinopril")}, "unique"),
        ({"source": " "}, "where it comes from"),
        ({"enabled": True, "words": ()}, "list is empty"),
    ],
)
def test_hint_list_is_checked(change, message):
    with pytest.raises(ValueError, match=message):
        AsrHints.model_validate({**HINTS, **change})


def test_hints_give_whisper_text_only_when_enabled():
    off = AsrHints.model_validate(HINTS)
    assert off.prompt is None and off.report() is None
    on = AsrHints.model_validate({**HINTS, "enabled": True})
    assert on.prompt == "lisinopril, co-amoxiclav"
    assert on.report() == {
        "source": "test list",
        "words": ["lisinopril", "co-amoxiclav"],
        "prompt": "lisinopril, co-amoxiclav",
    }


def test_switching_hints_changes_only_the_hints_and_shows_in_provenance():
    config = load_frozen_config()
    assert with_hints(config, None) is config
    assert with_hints(config, config.asr.hints.enabled) is config
    if not config.asr.hints.words:
        with pytest.raises(ValueError, match="list is empty"):
            with_hints(config, True)
        config = config.model_copy(
            update={"asr": config.asr.model_copy(update={"hints": AsrHints.model_validate(HINTS)})}
        )
    on, off = with_hints(config, True), with_hints(config, False)
    assert on.asr.hints.enabled and not off.asr.hints.enabled
    assert on.asr.decoding == off.asr.decoding == config.asr.decoding
    assert report_fields(off)["hints"] is None
    assert report_fields(on)["hints"]["words"] == list(config.asr.hints.words)
    assert report_fields(on) != report_fields(off)  # saved transcripts are never mixed up


def test_frozen_hint_list_is_sourced_and_free_of_everyday_words():
    hints = load_frozen_config().asr.hints
    if hints.words:
        assert hints.source.strip()
        assert not set(hints.words) & {"water", "oxygen", "potassium", "sodium", "calcium"}


# --- Passing the list to Whisper -------------------------------------------


class _Tokenizer:
    def __init__(self, per_word):
        self.per_word = per_word

    def encode(self, text, add_special_tokens=True):
        assert add_special_tokens is False  # counted the way faster-whisper counts
        return types.SimpleNamespace(ids=[0] * (self.per_word * len(text.split())))


def _fake_whisper(monkeypatch, per_word=1):
    calls = []

    class WhisperModel:
        def __init__(self, *args, **kwargs):
            self.hf_tokenizer = _Tokenizer(per_word)

        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            return iter(()), None

    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WhisperModel)
    )
    return calls


def test_transcriber_gives_whisper_the_hints(monkeypatch, tmp_path):
    from auris.speech.asr import Transcriber

    calls = _fake_whisper(monkeypatch)
    decoding = load_frozen_config().asr.decoding
    Transcriber(tmp_path, decoding).transcribe(None)
    Transcriber(tmp_path, decoding, hotwords="lisinopril, metformin").transcribe(None)
    assert [call["hotwords"] for call in calls] == [None, "lisinopril, metformin"]
    assert calls[1]["initial_prompt"] == decoding.initial_prompt


def test_transcriber_refuses_hints_whisper_would_cut_short(monkeypatch, tmp_path):
    from auris.speech.asr import MAX_HINT_TOKENS, Transcriber

    _fake_whisper(monkeypatch, per_word=1)
    decoding = load_frozen_config().asr.decoding
    Transcriber(tmp_path, decoding, hotwords=" ".join(["x"] * MAX_HINT_TOKENS))
    with pytest.raises(ValueError, match="Whisper keeps only"):
        Transcriber(tmp_path, decoding, hotwords=" ".join(["x"] * (MAX_HINT_TOKENS + 1)))


# --- Counting drug words the transcript made up -----------------------------


# --- Building the list ------------------------------------------------------


def test_ranked_names_become_spoken_ingredients():
    from auris.speech.hints import ingredients

    assert ingredients("metoprolol succinate er") == ["metoprolol"]
    assert ingredients("acetaminophen-hydrocodone") == ["acetaminophen", "hydrocodone"]
    assert ingredients("fluticasone/umeclidinium/vilanterol") == [
        "fluticasone",
        "umeclidinium",
        "vilanterol",
    ]
    assert ingredients("fluticasone nasal") == ["fluticasone"]
    assert ingredients("insulin glargine") == ["insulin glargine"]
    assert ingredients("potassium chloride") == []


def test_hint_words_keep_rank_order_once_each_and_stop_at_the_budget():
    from auris.speech.hints import hint_words

    def count(text):
        return len(text.split())

    ranked = ["lisinopril", "fluticasone nasal", "water", "fluticasone", "iron", "losartan", "x"]
    assert hint_words(ranked, count, 10) == ("lisinopril", "fluticasone", "losartan")
    assert hint_words(ranked, count, 2) == ("lisinopril", "fluticasone")


def test_frozen_hint_list_is_rebuilt_exactly_from_its_source_within_whisper_limit():
    import csv
    from pathlib import Path

    from auris.cli import _model_cache, _repo_root
    from auris.setup.asr import asr_root
    from auris.speech.asr import MAX_HINT_TOKENS
    from auris.speech.hints import hint_words

    config = load_frozen_config()
    path = asr_root(config.asr, _model_cache()) / "source/tokenizer.json"
    if not path.is_file():
        pytest.skip("Whisper tokenizer unavailable; run auris setup asr --fetch")
    tokenizers = pytest.importorskip("tokenizers")
    tokenizer = tokenizers.Tokenizer.from_file(str(path))

    def count(text):
        return len(tokenizer.encode(text, add_special_tokens=False).ids)

    ranking = Path(_repo_root() / "data/asr-hints/top_drugs.tsv")
    with ranking.open(encoding="utf-8") as handle:
        rows = sorted(csv.DictReader(handle, delimiter="\t"), key=lambda r: int(r["rank"]))
    rebuilt = hint_words((r["generic_name"] for r in rows), count, MAX_HINT_TOKENS)
    assert rebuilt == config.asr.hints.words
    assert count(" " + ", ".join(rebuilt)) <= MAX_HINT_TOKENS
