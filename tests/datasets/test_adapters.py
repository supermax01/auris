"""Syntax, raw references, ordering and conservative speaker handling."""

from pathlib import Path

import pytest
from dataset_helpers import grid, source
from pydantic import ValidationError

from auris.datasets.aci import adapt_aci, parse_turns
from auris.datasets.primock import adapt_primock, parse_textgrid, strip_markup
from auris.transcript import Transcript

FIXTURES = Path(__file__).parents[1] / "fixtures/datasets"


def test_attributed_extracts_and_praat_quotes():
    intervals = parse_textgrid((FIXTURES / "primock-extract.TextGrid").read_text(), tier="doctor")
    assert len(intervals) == 2 and intervals[0].text == ""
    assert intervals[1].start == 2.5334561157322537
    assert "<UNSURE>Hello how</UNSURE>" in intervals[1].text
    assert parse_turns((FIXTURES / "aci-extract.txt").read_text())[0].role == "patient"
    assert (
        parse_textgrid(grid([(0, 1, 'said "hello"\nagain')]), tier="doctor")[0].text
        == 'said "hello"\nagain'
    )


@pytest.mark.parametrize(
    "broken",
    [
        lambda s: s.replace("size = 1", "size = 2", 1),
        lambda s: s.replace("IntervalTier", "TextTier"),
        lambda s: s.replace("intervals [1]", "intervals [2]"),
        lambda s: s + "unexpected",
        lambda s: s.replace("xmin = 1", "xmin = -1"),
        lambda s: s.replace("xmax = 2", "xmax = 0"),
    ],
)
def test_malformed_textgrids_refuse(broken):
    with pytest.raises(ValueError):
        parse_textgrid(broken(grid([(1, 2, "Fictional")], tier="Doctor")), tier="doctor")


def test_markup_keeps_all_spoken_words_and_counts_only_tags():
    raw = "No<UNSURE> drug allergy</UNSURE>. <UNIN/> word <INAUDIBLE_SPEECH/>"
    clean, removed = strip_markup(raw)
    assert clean == "No drug allergy . word"
    assert removed == {"<UNSURE>": 1, "</UNSURE>": 1, "<UNIN/>": 1, "<INAUDIBLE_SPEECH/>": 1}


def test_merge_start_order_overlaps_ties_silence_and_raw_markers():
    doctor = grid([(0, 1, ""), (1, 4, "Fictional <UNSURE>words</UNSURE>"), (4, 5, "<UNIN/>")])
    patient = grid([(1, 2, "Fictional reply"), (2, 6, "Overlapping reply")])
    transcript = adapt_primock(doctor, patient, source())
    assert [s.id for s in transcript.segments] == [
        "doctor-0002",
        "patient-0001",
        "patient-0002",
        "doctor-0003",
    ]
    assert [s.role for s in transcript.segments] == ["clinician", "patient", "patient", "clinician"]
    assert transcript.segments[-1].text == "" and transcript.segments[-1].raw_text == "<UNIN/>"
    assert transcript.source.silence_intervals_dropped == 1
    assert transcript.source.markup_characters_removed == len("<UNSURE></UNSURE><UNIN/>")
    assert transcript.timing_source == "human_utterance_bounds"
    assert Transcript.from_json(transcript.to_json()).content_hash == transcript.content_hash
    changed = transcript.model_copy(
        update={"source": transcript.source.model_copy(update={"commit": "b" * 40})}
    )
    assert changed.content_hash != transcript.content_hash


def test_aci_empty_turn_preamble_companion_annotation_and_no_invented_time():
    text = (
        "Fictional opening\n[doctor] Question\n[patient]\n"
        "[patient_guest] Answer [ inaudible 00:09:25 ]"
    )
    transcript = adapt_aci(text, source("aci-bench"))
    assert [s.role for s in transcript.segments] == ["unknown", "clinician", "patient", "companion"]
    assert [s.turn_number for s in transcript.segments] == [1, 2, 3, 4]
    assert all(s.start_seconds is None and s.end_seconds is None for s in transcript.segments)
    assert transcript.segments[2].text == ""
    assert "inaudible" in transcript.segments[-1].text


@pytest.mark.parametrize(
    "text",
    [
        "no tags",
        "[doctor missing] hello",
        "[doctor] hi [nurse] hello",
        "[doctor] hi [patient",
        "[doctor] [Patient:] answer",
    ],
)
def test_aci_malformed_and_unknown_tags_refuse(text):
    with pytest.raises(ValueError):
        parse_turns(text)


def test_untimed_requires_explicit_mode_and_consecutive_order():
    transcript = adapt_aci("[doctor] Fictional. [patient] Reply.", source("aci-bench"))
    raw = transcript.model_dump(mode="json")
    raw["timing_mode"] = "timed"
    with pytest.raises(ValidationError):
        Transcript.model_validate(raw)
    raw = transcript.model_dump(mode="json")
    raw["segments"][1]["turn_number"] = 8
    with pytest.raises(ValidationError):
        Transcript.model_validate(raw)
    raw = transcript.model_dump(mode="json")
    raw["segments"][0]["start_seconds"] = 0
    with pytest.raises(ValidationError):
        Transcript.model_validate(raw)


def test_legacy_serialization_respects_pydantic_exclusions():
    transcript = Transcript()
    assert transcript.model_dump(exclude_none=True) == {"schema_version": "1", "segments": ()}
    assert transcript.model_dump(exclude={"segments"}) == {"schema_version": "1"}
