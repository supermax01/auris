"""Speech stage (audio to transcript) without the speech models: assembly, roles,
who-spoke-when helpers, scoring, setup pins and the transcribe command's guards."""

import hashlib
import os
import stat

import pytest

from auris.config import AsrConversion, ConvertedFile, PinnedFile, load_frozen_config
from auris.speech.asr import AsrSegment, Word
from auris.speech.roles import SpeakerRole, assign_roles, score
from auris.speech.transcribe import Piece, _split, build_transcript
from auris.transcript import Transcript, TranscriptSegment


def words(*items):
    return tuple(Word(start, end, text, 0.9) for start, end, text in items)


# --- Assembly ---------------------------------------------------------------


def test_segments_split_at_speaker_changes_and_long_pauses():
    segment = AsrSegment(
        0.0,
        9.0,
        "",
        words(
            (0.0, 0.4, " How"),
            (0.4, 0.8, " are"),
            (0.8, 1.2, " you?"),
            (1.3, 1.6, " Fine."),
            (4.0, 4.5, " Thanks."),
        ),
    )
    pieces = _split(segment, ["a", "a", "a", "b", "b"])
    assert [(p.speaker, p.text) for p in pieces] == [
        ("a", "How are you?"),
        ("b", "Fine."),
        ("b", "Thanks."),
    ]
    unattributed = _split(segment, [None] * 5)
    assert {p.speaker for p in unattributed} == {"unattributed"}


def test_build_transcript_orders_by_time_and_keeps_word_timings_monotonic():
    patient = Piece(
        "patient", list(words((2.0, 2.5, " I"), (2.4, 2.3, " have"), (2.6, 3.0, " pain.")))
    )
    clinician = Piece("clinician", list(words((0.5, 1.0, " Hello."))))
    empty = Piece("clinician", list(words((5.0, 5.1, " "))))
    transcript = build_transcript([patient, clinician, empty], {"clinician": SpeakerRole.CLINICIAN})
    assert [s.id for s in transcript.segments] == ["s1", "s2"]
    first, second = transcript.segments
    assert (first.role, first.text) == (SpeakerRole.CLINICIAN, "Hello.")
    assert second.role == SpeakerRole.UNKNOWN  # no role given: a person decides
    timings = [(w.start_seconds, w.end_seconds) for w in second.words]
    assert timings == sorted(timings) and all(end >= start for start, end in timings)
    assert second.start_seconds == 2.0 and second.end_seconds == 3.0
    assert Transcript.model_validate_json(transcript.to_json()) == transcript


# --- Who is who -------------------------------------------------------------


CLINICIAN_LINES = [
    "Hello, how can I help you today?",
    "Have you had any fever?",
    "Do you have any allergies to medication?",
    "I'll prescribe you some antibiotics, one tablet twice a day.",
    "Any questions? Come back if it gets worse.",
]
PATIENT_LINES = [
    "I've had a cough for three days.",
    "No, I don't think so. My chest hurts a bit.",
    "I'm not sure, I take ibuprofen sometimes.",
    "Okay, thank you doctor.",
]


def test_clinician_scores_higher_than_patient():
    assert score(CLINICIAN_LINES) > score(PATIENT_LINES)
    roles, scores, margin = assign_roles({"speaker1": PATIENT_LINES, "speaker2": CLINICIAN_LINES})
    assert roles == {"speaker2": SpeakerRole.CLINICIAN, "speaker1": SpeakerRole.PATIENT}
    assert scores[0].speaker == "speaker2" and margin > 0


def test_roles_stay_unknown_when_too_close_or_one_speaker():
    same = ["We talked about it.", "It was fine."]
    roles, _, margin = assign_roles({"speaker1": same, "speaker2": list(same)})
    assert set(roles.values()) == {SpeakerRole.UNKNOWN} and margin == 0
    roles, _, _ = assign_roles({"speaker1": CLINICIAN_LINES})
    assert roles == {"speaker1": SpeakerRole.UNKNOWN}


def test_minor_speaker_is_not_given_a_main_role():
    lines = {"speaker1": CLINICIAN_LINES * 4, "speaker2": PATIENT_LINES * 4, "speaker3": ["Hi."]}
    roles, _, _ = assign_roles(lines)
    assert roles["speaker3"] == SpeakerRole.UNKNOWN
    assert roles["speaker1"] == SpeakerRole.CLINICIAN


# --- Who spoke when (pure helpers) ------------------------------------------


def test_windows_cover_regions_and_skip_tiny_ones():
    from auris.speech.diarize import WINDOW_SECONDS, windows

    spans = windows([(0.0, 0.2), (1.0, 2.0), (3.0, 6.6)], duration=10.0)
    assert (spans[0].start, spans[0].end, spans[0].region) == (1.0, 2.0, 1)  # its own audio only
    long = [s for s in spans if s.region == 2]
    assert all(abs((s.end - s.start) - WINDOW_SECONDS) < 1e-9 for s in long)
    assert long[0].start == 3.0 and long[-1].end == pytest.approx(6.6)  # the tail is covered
    assert not any(s.end <= 0.2 for s in spans)


def test_word_assignment_votes_and_leaves_far_words_unattributed():
    from auris.speech.diarize import Window, assign_words

    spans = [Window(0.0, 1.5), Window(0.75, 2.25), Window(1.5, 3.0)]
    labels = [0, 0, 1]
    result = assign_words(
        list(words((0.2, 0.4, "a"), (2.6, 2.8, "b"), (9.0, 9.2, "c"))), spans, labels
    )
    assert result == [0, 1, None]


def test_smoothing_removes_single_window_flicker():
    from auris.speech.diarize import smooth

    assert smooth([0, 0, 0, 1, 0, 0, 1, 1, 1, 1]) == [0, 0, 0, 0, 0, 0, 1, 1, 1, 1]
    assert smooth([0, 0, 1, 1, 0, 0]) == [0, 0, 1, 1, 0, 0]  # boundaries stay put
    # A lone reply in its own speech region survives between the other speaker's windows.
    assert smooth([0, 0, 1, 0, 0], regions=[0, 0, 1, 2, 2]) == [0, 0, 1, 0, 0]


def test_clustering_separates_two_voices_deterministically():
    np = pytest.importorskip("numpy")
    from auris.speech.diarize import cluster

    rng = np.random.default_rng(7)
    a = rng.normal([1, 0, 0], 0.05, size=(20, 3))
    b = rng.normal([0, 1, 0], 0.05, size=(20, 3))
    points = np.concatenate([b[:3], a, b[3:]])
    points /= np.linalg.norm(points, axis=1, keepdims=True)
    labels = cluster(points, 2)
    assert labels[0] == 0  # numbered by first appearance
    assert len(set(labels[:3]) | set(labels[23:])) == 1 and len(set(labels[3:23])) == 1
    assert labels[3] != labels[0]
    assert np.array_equal(labels, cluster(points, 2))


# --- Scoring ----------------------------------------------------------------


def _segment(id, role, start, end, text, timed_words=None):
    return TranscriptSegment(
        id=id,
        speaker_cluster_id=role,
        role=role,
        start_seconds=start,
        end_seconds=end,
        text=text,
        words=timed_words,
    )


# --- Setup pins and guards --------------------------------------------------


def test_frozen_config_pins_every_speech_file():
    config = load_frozen_config()
    names = {pin.name for pin in config.asr.laptop.source_files}
    assert {"model.safetensors", "config.json", "tokenizer.json", "normalizer.json"} <= names
    assert {f.name for f in config.asr.laptop.conversion.files} >= {"model.bin", "vocabulary.json"}
    assert config.asr.decoding.temperature == 0 and config.asr.decoding.vad_filter
    assert config.asr.decoding.initial_prompt


@pytest.mark.parametrize("prompt", ["I have had a cough.", "Take 500 mg.", "Any allergies?"])
def test_decoding_prompt_must_say_nothing_clinical(prompt):
    from auris.config import AsrDecoding

    with pytest.raises(ValueError, match="no clinical words"):
        AsrDecoding(initial_prompt=prompt)


def test_pinned_file_needs_exactly_one_pin():
    with pytest.raises(ValueError, match="exactly one"):
        PinnedFile(name="x.bin", bytes=1)
    with pytest.raises(ValueError, match="exactly one"):
        PinnedFile(name="x.bin", bytes=1, sha256="0" * 64, git_blob_sha1="0" * 40)


def test_source_files_are_verified_by_git_blob_id_and_sha256(tmp_path):
    from auris.setup.asr import git_blob_sha1, verify_pinned

    path = tmp_path / "config.json"
    path.write_bytes(b'{"a": 1}\n')
    blob = hashlib.sha1(b"blob 9\0" + path.read_bytes()).hexdigest()  # noqa: S324
    assert git_blob_sha1(path) == blob
    verify_pinned(path, PinnedFile(name="config.json", bytes=9, git_blob_sha1=blob))
    with pytest.raises(ValueError, match="Git blob"):
        verify_pinned(path, PinnedFile(name="config.json", bytes=9, git_blob_sha1="0" * 40))
    with pytest.raises(ValueError, match="size"):
        verify_pinned(path, PinnedFile(name="config.json", bytes=10, git_blob_sha1=blob))
    with pytest.raises(ValueError, match="SHA-256"):
        verify_pinned(path, PinnedFile(name="config.json", bytes=9, sha256="0" * 64))


def test_setup_refuses_a_conversion_that_does_not_match_its_pin(tmp_path):
    from auris.setup.asr import setup_asr

    config = load_frozen_config().asr
    pins = {p.name: p for p in config.laptop.source_files}

    def fetcher(url, destination):
        raise AssertionError("no download expected")

    with pytest.raises(ValueError, match="missing; run auris setup asr --fetch"):
        setup_asr(config, tmp_path, fetch=False, fetcher=fetcher)

    def converter(source, destination, conversion: AsrConversion):
        destination.mkdir()
        for pin in conversion.files:
            (destination / pin.name).write_text("not the pinned bytes")

    tiny = config.model_copy(
        update={"laptop": config.laptop.model_copy(update={"source_files": (pins["config.json"],)})}
    )
    source = (
        tmp_path
        / "asr"
        / (f"{config.laptop.source_model.replace('/', '--')}--{config.laptop.revision[:12]}")
        / "source"
    )
    source.mkdir(parents=True)
    assert isinstance(pins["config.json"], PinnedFile)
    (source / "config.json").write_text("wrong")
    with pytest.raises(ValueError, match="size mismatch"):
        setup_asr(tiny, tmp_path, converter=converter)
    assert ConvertedFile(name="model.bin", sha256="a" * 64).name == "model.bin"


def test_transcribe_refuses_a_shared_output_folder_before_any_work(tmp_path, monkeypatch):
    from auris.cli import main

    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o755)
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    called = []
    monkeypatch.setattr("auris.setup.asr.asr_paths", lambda *a: called.append(a))
    code = main(["transcribe", "--audio", "visit.wav", "--out", str(shared / "t.json")])
    assert code != 0 and not called


def test_transcribe_rejects_unknown_roles(capsys):
    from auris.cli import main

    with pytest.raises(SystemExit):
        main(["transcribe", "--audio", "nurse=a.wav", "--out", "t.json"])
    assert "role must be one of" in capsys.readouterr().err


def test_tracks_need_a_role_for_every_track_or_none(tmp_path):
    from auris.speech.transcribe import Track, transcribe_tracks

    with pytest.raises(ValueError, match="every track"):
        transcribe_tracks(
            [Track(tmp_path / "a.wav", SpeakerRole.CLINICIAN), Track(tmp_path / "b.wav")], None
        )


def test_words_in_a_stretch_too_short_to_fingerprint_stay_unattributed():
    from auris.speech.diarize import Window, assign_words

    spans = [Window(0.0, 1.5, 0), Window(2.5, 4.0, 2)]
    lone_yes = list(words((1.9, 2.1, "yes")))
    assert assign_words(lone_yes, spans, [0, 1]) == [0]  # nearest window, other speaker
    assert assign_words(lone_yes, spans, [0, 1], unwindowed=[(1.8, 2.15)]) == [None]
