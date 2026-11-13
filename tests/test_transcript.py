"""Transcript model tests (Step 2a). All data is fictional."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from auris.transcript import Transcript, TranscriptSegment, WordTiming

FIXTURE = Path(__file__).parent / "fixtures" / "transcript_example.json"


def _segment(**overrides: object) -> TranscriptSegment:
    data: dict[str, object] = {
        "id": "s1",
        "speaker_cluster_id": "spk-a",
        "role": "patient",
        "start_seconds": 0.0,
        "end_seconds": 2.0,
        "text": "I have a cough.",
    }
    data.update(overrides)
    return TranscriptSegment.model_validate(data)


def test_fixture_loads_and_round_trips(tmp_path: Path) -> None:
    transcript = Transcript.load(FIXTURE)
    assert len(transcript.segments) == 2
    assert transcript.segments[1].words is not None
    out = tmp_path / "roundtrip.json"
    transcript.save(out)
    reloaded = Transcript.load(out)
    assert reloaded == transcript
    assert reloaded.content_hash == transcript.content_hash


def test_content_hash_is_stable_across_json_round_trip() -> None:
    transcript = Transcript(segments=(_segment(),))
    assert Transcript.from_json(transcript.to_json()).content_hash == transcript.content_hash


def test_content_hash_changes_when_text_changes() -> None:
    base = Transcript(segments=(_segment(),))
    changed = Transcript(segments=(_segment(text="I have no cough."),))
    assert changed.content_hash != base.content_hash


def test_content_hash_changes_when_timing_changes() -> None:
    base = Transcript(segments=(_segment(),))
    changed = Transcript(segments=(_segment(start_seconds=0.5),))
    assert changed.content_hash != base.content_hash


def test_empty_transcript_still_hashes() -> None:
    empty = Transcript()
    assert len(empty.content_hash) == 64
    assert Transcript.from_json(empty.to_json()) == empty


def test_segment_by_id() -> None:
    transcript = Transcript(segments=(_segment(), _segment(id="s2", text="Hello.")))
    assert transcript.segment_by_id("s2") == transcript.segments[1]
    assert transcript.segment_by_id("missing") is None


def test_duplicate_segment_ids_refused() -> None:
    with pytest.raises(ValidationError, match="duplicate segment id"):
        Transcript(segments=(_segment(), _segment()))


def test_end_before_start_refused() -> None:
    with pytest.raises(ValidationError, match="end_seconds"):
        _segment(start_seconds=5.0, end_seconds=4.0)


def test_negative_seconds_refused() -> None:
    with pytest.raises(ValidationError, match="start_seconds"):
        _segment(start_seconds=-1.0)


def test_unknown_role_refused() -> None:
    with pytest.raises(ValidationError):
        _segment(role="doctor")


def test_segments_are_immutable() -> None:
    segment = _segment()
    with pytest.raises(ValidationError):
        segment.text = "changed"  # type: ignore[misc]


def test_empty_word_list_refused() -> None:
    with pytest.raises(ValidationError, match="rather than empty"):
        _segment(words=())


def test_unordered_words_refused() -> None:
    words = (
        WordTiming(word="cough", start_seconds=1.0, end_seconds=1.4),
        WordTiming(word="a", start_seconds=0.5, end_seconds=0.7),
    )
    with pytest.raises(ValidationError, match="ordered"):
        _segment(words=words)


def test_word_timings_default_to_approximate() -> None:
    word = WordTiming(word="cough", start_seconds=1.0, end_seconds=1.4)
    assert word.approximate is True


@pytest.mark.parametrize("field", ["start_seconds", "end_seconds"])
@pytest.mark.parametrize("value", [-1.0, float("inf"), float("-inf"), float("nan")])
def test_all_timing_fields_refuse_invalid_seconds(field: str, value: float) -> None:
    with pytest.raises(ValidationError, match=field):
        _segment(**{field: value})
    data = {"word": "cough", "start_seconds": 0.0, "end_seconds": 1.0, field: value}
    with pytest.raises(ValidationError, match=field):
        WordTiming.model_validate(data)


@pytest.mark.parametrize("start,end", [(0.0, 1.5), (1.5, 3.0)])
def test_word_timings_must_be_inside_segment(start: float, end: float) -> None:
    word = WordTiming(word="cough", start_seconds=start, end_seconds=end)
    with pytest.raises(ValidationError, match="within the segment"):
        _segment(start_seconds=1.0, end_seconds=2.0, words=(word,))


def test_word_end_times_must_be_ordered() -> None:
    words = (
        WordTiming(word="a", start_seconds=0.0, end_seconds=1.5),
        WordTiming(word="cough", start_seconds=0.5, end_seconds=1.0),
    )
    with pytest.raises(ValidationError, match="ordered"):
        _segment(words=words)


def test_word_timings_cannot_claim_to_be_exact() -> None:
    with pytest.raises(ValidationError, match="approximate"):
        WordTiming(word="cough", start_seconds=0.0, end_seconds=1.0, approximate=False)


@pytest.mark.parametrize("word", ["", " "])
def test_word_text_cannot_be_blank(word: str) -> None:
    with pytest.raises(ValidationError):
        WordTiming(word=word, start_seconds=0.0, end_seconds=1.0)


def test_nested_transcript_data_is_immutable() -> None:
    transcript = Transcript.load(FIXTURE)
    with pytest.raises(ValidationError, match="frozen_instance"):
        transcript.segments = ()
    word = transcript.segments[1].words[0]
    with pytest.raises(ValidationError, match="frozen_instance"):
        word.start_seconds = 0.0


def test_hash_uses_canonical_utf8_json_and_sha256() -> None:
    transcript = Transcript(segments=(_segment(text="Fictional café visit."),))
    payload = transcript.model_dump(mode="json")
    expected = b"auris.transcript.v1\n" + json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    assert transcript.canonical_bytes() == expected
    assert transcript.content_hash == hashlib.sha256(expected).hexdigest()
    assert Transcript.from_json(json.dumps(payload, indent=7, sort_keys=True)).content_hash == (
        transcript.content_hash
    )


@pytest.mark.parametrize(
    "change",
    [
        {"id": "s2"},
        {"speaker_cluster_id": "spk-b"},
        {"role": "clinician"},
        {"start_seconds": 0.5},
        {"end_seconds": 3.0},
        {"text": "I have no cough."},
        {"words": [{"word": "cough", "start_seconds": 1.0, "end_seconds": 1.5}]},
    ],
)
def test_hash_covers_every_segment_field(change: dict[str, object]) -> None:
    assert (
        Transcript(segments=(_segment(**change),)).content_hash
        != Transcript(segments=(_segment(),)).content_hash
    )


def test_hash_covers_segment_order_and_word_timing_edits() -> None:
    original = Transcript.load(FIXTURE)
    assert (
        Transcript(segments=tuple(reversed(original.segments))).content_hash
        != original.content_hash
    )
    data = original.model_dump(mode="json")
    data["segments"][1]["words"][0]["end_seconds"] = 4.15
    assert Transcript.model_validate(data).content_hash != original.content_hash


def test_overlapping_speech_is_allowed() -> None:
    transcript = Transcript(segments=(_segment(), _segment(id="s2", start_seconds=1.0)))
    assert len(transcript.segments) == 2


def test_save_uses_private_workdir_and_replaces_file_privately(tmp_path: Path) -> None:
    path = tmp_path / "workdir" / "transcript.json"
    transcript = Transcript.load(FIXTURE)
    transcript.save(path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    path.chmod(0o644)
    transcript.save(path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert Transcript.load(path) == transcript
    assert list(path.parent.iterdir()) == [path]


def test_save_refuses_shared_workdir(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    with pytest.raises(ValueError, match="0700"):
        Transcript().save(shared / "transcript.json")
    assert not list(shared.iterdir())
