"""Joining a speaker's consecutive speech pieces into turns, and the style sentence
for every window: when segments join, when they stay apart, what is recorded."""

import sys
import types

import pytest

from auris.config import load_frozen_config, with_speech_options
from auris.speech.turns import join_turns
from auris.transcript import SpeakerRole, Transcript, TranscriptSegment, WordTiming

C, P = SpeakerRole.CLINICIAN, SpeakerRole.PATIENT


def _segment(number, role, start, end, text):
    words = text.split()
    step = (end - start) / len(words)
    return TranscriptSegment(
        id=f"s{number}",
        speaker_cluster_id=role.value,
        role=role,
        start_seconds=start,
        end_seconds=end,
        text=text,
        words=tuple(
            WordTiming(word=w, start_seconds=start + i * step, end_seconds=start + (i + 1) * step)
            for i, w in enumerate(words)
        ),
    )


def _join(*segments, gap=1.5, longest=60):
    return join_turns(Transcript(segments=segments), gap_seconds=gap, max_seconds=longest)


def test_a_split_sentence_becomes_one_turn():
    joined, joins = _join(
        _segment(1, P, 10.0, 10.4, "I've got"),
        _segment(2, P, 10.6, 11.5, "heart failure."),
    )
    assert joins == 1 and len(joined.segments) == 1
    turn = joined.segments[0]
    assert turn.text == "I've got heart failure." and turn.id == "s1"
    assert (turn.start_seconds, turn.end_seconds) == (10.0, 11.5)
    assert [w.word for w in turn.words] == ["I've", "got", "heart", "failure."]


@pytest.mark.parametrize(
    "segments, reason",
    [
        ((_segment(1, P, 0, 1, "Yes"), _segment(2, C, 1.2, 2, "Okay")), "other speaker"),
        ((_segment(1, P, 0, 1, "Yes"), _segment(2, P, 3.0, 4, "and then")), "long pause"),
        ((_segment(1, P, 0, 50, "a long story"), _segment(2, P, 50.5, 70, "goes on")), "too long"),
        ((_segment(1, P, 0, 2, "overlap"), _segment(2, P, 1.5, 3, "here")), "overlap"),
    ],
)
def test_segments_stay_apart_when_they_should(segments, reason):
    joined, joins = _join(*segments)
    assert joins == 0 and len(joined.segments) == 2, reason


def test_another_speaker_in_between_keeps_turns_apart_and_ids_are_renumbered():
    joined, joins = _join(
        _segment(1, C, 0.0, 1.0, "How are"),
        _segment(2, C, 1.2, 2.0, "you today?"),
        _segment(3, P, 2.1, 2.5, "Fine."),
        _segment(4, C, 2.7, 3.5, "Good, and"),
        _segment(5, C, 3.6, 4.5, "any pain?"),
    )
    assert joins == 2
    assert [s.text for s in joined.segments] == [
        "How are you today?",
        "Fine.",
        "Good, and any pain?",
    ]
    assert [s.id for s in joined.segments] == ["s1", "s2", "s3"]


def test_untimed_or_unchanged_transcripts_are_returned_as_they_are():
    single = Transcript(segments=(_segment(1, C, 0, 1, "Hello"),))
    assert join_turns(single, gap_seconds=1.5, max_seconds=60) == (single, 0)
    assert join_turns(Transcript(), gap_seconds=1.5, max_seconds=60) == (Transcript(), 0)


def test_respelled_segments_keep_their_own_heard_text():
    first = _segment(1, P, 0, 1, "I take")
    second = _segment(2, P, 1.2, 2, "lisinopril").model_copy(update={"raw_text": "lizinapril"})
    transcript = Transcript.model_validate(
        {"schema_version": "2", "segments": [first.model_dump(), second.model_dump()]}
    )
    assert join_turns(transcript, gap_seconds=1.5, max_seconds=60)[1] == 0


def test_both_options_are_off_until_measured_and_recorded_when_on():
    from auris.speech.cli import report_fields

    config = load_frozen_config()
    assert config.asr.decoding.style_every_window is False
    assert config.asr.segmentation.join_turns is False
    assert report_fields(config)["segmentation"] is None
    on = with_speech_options(config, style_every_window=True, join_turns=True)
    fields = report_fields(on)
    assert fields["decoding"]["style_every_window"] is True
    assert fields["segmentation"]["version"] == "join-turns-v1"
    assert report_fields(on) != report_fields(config)
    assert with_speech_options(config) is config


def test_the_style_sentence_goes_to_every_window_with_any_hints(monkeypatch, tmp_path):
    from auris.speech.asr import Transcriber

    calls = []

    class WhisperModel:
        def __init__(self, *a, **k):
            self.hf_tokenizer = types.SimpleNamespace(
                encode=lambda text, add_special_tokens=True: types.SimpleNamespace(ids=text.split())
            )

        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            return iter(()), None

    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WhisperModel)
    )
    decoding = load_frozen_config().asr.decoding
    style = decoding.initial_prompt
    Transcriber(tmp_path, decoding).transcribe(None)
    every = decoding.model_copy(update={"style_every_window": True})
    Transcriber(tmp_path, every).transcribe(None)
    Transcriber(tmp_path, every, hotwords="lisinopril, metformin").transcribe(None)
    assert [c["hotwords"] for c in calls] == [None, style, f"{style} lisinopril, metformin"]
    # Exactly once per window: with the style in every window it is not also the start prompt.
    assert [c["initial_prompt"] for c in calls] == [style, None, None]


def test_style_plus_hints_over_whispers_limit_is_refused_with_a_clear_message(
    monkeypatch, tmp_path
):
    from auris.speech.asr import MAX_HINT_TOKENS, Transcriber

    class WhisperModel:
        def __init__(self, *a, **k):
            self.hf_tokenizer = types.SimpleNamespace(
                encode=lambda text, add_special_tokens=True: types.SimpleNamespace(ids=text.split())
            )

    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WhisperModel)
    )
    every = load_frozen_config().asr.decoding.model_copy(update={"style_every_window": True})
    with pytest.raises(ValueError, match="style sentence plus the speech hints"):
        Transcriber(tmp_path, every, hotwords=" ".join(["x"] * MAX_HINT_TOKENS))


def test_the_pipeline_joins_before_respelling_and_reports_the_joins():
    from auris.speech.transcribe import _shaped

    config = with_speech_options(load_frozen_config(), join_turns=True)
    transcript = Transcript(
        segments=(_segment(1, P, 0, 1, "I've got"), _segment(2, P, 1.1, 2, "a cough."))
    )
    report = {}
    shaped = _shaped(transcript, report, None, config.asr.segmentation)
    assert report["turns_joined"] == 1 and len(shaped.segments) == 1
    untouched = _shaped(transcript, {}, None, load_frozen_config().asr.segmentation)
    assert untouched is transcript
