"""Drug-name spelling after speech-to-text: when a word is respelled, when it is
left alone, what is kept and recorded. The misspellings here are invented."""

import pytest

from auris.config import AsrSpelling, load_frozen_config, with_spelling
from auris.speech.spelling import (
    DrugSpeller,
    correct_transcript,
    levenshtein,
    skeleton,
    spelling_report,
)
from auris.transcript import SpeakerRole, Transcript, TranscriptSegment, WordTiming

INGREDIENTS = ["lisinopril", "furosemide", "loratadine", "fexofenadine", "simvastatin", "atenolol"]
ENGLISH = ["take", "the", "program", "lizard", "tablet", "pressure"]
KNOWN = INGREDIENTS + ["zestril", "ethinyl estradiol"]


def speller():
    return DrugSpeller(INGREDIENTS, ENGLISH, KNOWN)


def test_sound_outline_merges_spellings_of_one_sound():
    assert skeleton("lizinapril") == skeleton("lisinopril")
    assert skeleton("fixofenadine") == skeleton("fexofenadine")
    assert skeleton("phurosemide") == skeleton("furosemide")
    assert levenshtein("kitten", "sitting") == 3


@pytest.mark.parametrize(
    "heard, meant",
    [
        ("lizinapril", "lisinopril"),
        ("loratidine", "loratadine"),
        ("fexofenadeen", "fexofenadine"),
    ],
)
def test_a_misheard_name_becomes_the_ingredient_it_sounds_like(heard, meant):
    assert speller().correct(heard) == meant


@pytest.mark.parametrize(
    "word",
    [
        "program",  # English
        "programs",  # English with a plain ending
        "lizards",
        "zestril",  # already a drug name (a brand)
        "pril",  # too short
        "metformun",  # not near any target here
        "kisinopril",  # different first sound
        "ethinyl",  # a word of a longer drug name
        "lisinoprils",  # a known name with a plural ending
    ],
)
def test_english_known_short_or_distant_words_are_left_alone(word):
    assert speller().correct(word) is None


def test_two_equally_close_ingredients_change_nothing():
    tied = DrugSpeller(["atenolol", "atenolal"], ENGLISH, ["atenolol", "atenolal"])
    assert tied.correct("atenolul") is None


def _transcript(text, words):
    return Transcript(
        segments=(
            TranscriptSegment(
                id="s1",
                speaker_cluster_id="patient",
                role=SpeakerRole.PATIENT,
                start_seconds=0.0,
                end_seconds=float(len(words)),
                text=text,
                words=tuple(
                    WordTiming(word=w, start_seconds=float(i), end_seconds=i + 0.5)
                    for i, w in enumerate(words)
                ),
            ),
            TranscriptSegment(
                id="s2",
                speaker_cluster_id="clinician",
                role=SpeakerRole.CLINICIAN,
                start_seconds=5.0,
                end_seconds=6.0,
                text="Okay.",
                words=(WordTiming(word="Okay.", start_seconds=5.0, end_seconds=6.0),),
            ),
        )
    )


def test_transcript_keeps_what_was_heard_and_lists_every_change():
    heard = _transcript(
        "Lizinapril, and the loratidine.", ["Lizinapril,", "and", "the", "loratidine."]
    )
    fixed, corrections = correct_transcript(heard, speller())
    first = fixed.segments[0]
    assert first.text == "Lisinopril, and the loratadine."
    assert first.raw_text == "Lizinapril, and the loratidine."
    assert [w.word for w in first.words] == ["Lisinopril,", "and", "the", "loratadine."]
    assert [(w.start_seconds, w.end_seconds) for w in first.words] == [
        (w.start_seconds, w.end_seconds) for w in heard.segments[0].words
    ]
    assert fixed.segments[1] == heard.segments[1]
    assert corrections == [
        {"segment": "s1", "heard": "Lizinapril", "corrected": "lisinopril"},
        {"segment": "s1", "heard": "loratidine", "corrected": "loratadine"},
    ]
    assert fixed.content_hash != heard.content_hash
    unchanged, none = correct_transcript(
        _transcript("Take the tablet.", ["Take", "the", "tablet."]), speller()
    )
    assert none == [] and unchanged.segments[0].raw_text is None


def test_spelling_is_off_until_measured_and_recorded_in_provenance():
    config = load_frozen_config()
    assert not config.asr.spelling.enabled and spelling_report(config) is None
    on = with_spelling(config, True)
    report = spelling_report(on)
    assert report["word_list_sha256"] == config.asr.spelling.word_list.sha256
    assert report["rxnorm_release"] == config.terminology.rxnorm.release
    from auris.speech.cli import report_fields

    assert report_fields(on)["spelling"] == report and report_fields(config)["spelling"] is None
    with pytest.raises(ValueError, match="no word list"):
        AsrSpelling(enabled=True)


def test_word_list_is_refused_when_it_does_not_match_its_pin(tmp_path):
    from auris.setup.asr import ensure_word_list, word_list_path

    pin = load_frozen_config().asr.spelling.word_list
    with pytest.raises(ValueError, match="run auris setup asr --fetch"):
        ensure_word_list(pin, tmp_path, fetch=False)
    with pytest.raises(ValueError, match="does not match its pin"):
        ensure_word_list(pin, tmp_path, fetch=True, fetcher=lambda url, d: d.write_text("x\n"))
    assert not word_list_path(pin, tmp_path).exists()


def test_a_saved_respelled_transcript_keeps_what_was_heard(tmp_path):
    heard = _transcript("Lizinapril daily.", ["Lizinapril", "daily."])
    fixed, _ = correct_transcript(heard, speller())
    assert fixed.schema_version == "2"
    path = tmp_path / "t.json"
    fixed.save(path)
    loaded = Transcript.load(path)
    assert loaded.segments[0].raw_text == "Lizinapril daily."
    assert loaded.segments[0].text == "Lisinopril daily."
    assert loaded.content_hash == fixed.content_hash


def test_speller_refuses_an_index_that_is_not_the_pinned_release(tmp_path, monkeypatch):
    from auris.cli import _model_cache
    from auris.speech import spelling
    from auris.terminology import index as index_module

    class Index:
        metadata = {"rxnorm": {"release": "other"}}

        def __init__(self, path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(index_module, "TerminologyIndex", Index)
    path = tmp_path / "index.sqlite"
    path.write_bytes(b"")
    config = with_spelling(load_frozen_config(), True)
    words = __import__("auris.setup.asr", fromlist=["word_list_path"]).word_list_path(
        config.asr.spelling.word_list, _model_cache()
    )
    if not words.exists():
        pytest.skip("word list not fetched")
    with pytest.raises(ValueError, match="pinned RxNorm index"):
        spelling.load_speller(config, _model_cache(), path)
