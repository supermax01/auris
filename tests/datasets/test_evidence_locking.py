"""Untimed receipts and test extraction refusal before any model work."""

import json
import shutil

import pytest
from dataset_helpers import corpus, grid, source
from demo_helpers import FakeValidator, demo_input
from eval_helpers import DATASET, MANIFEST

from auris.assertions import EvidenceItem
from auris.config import load_frozen_config
from auris.datasets.aci import adapt_aci
from auris.datasets.primock import adapt_primock
from auris.datasets.splits import build_splits
from auris.eval.gold import canonical_json
from auris.eval.runs import run_dataset
from auris.evidence import align_evidence_item
from auris.extract.prompt import render_transcript
from auris.fhir import Mapper
from auris.render import NoteMetadata, note_items, render_note
from auris.transcript import Transcript
from auris.validation.report import validate_mappings


def test_untimed_evidence_has_turn_not_audio_and_render_preserves_chunk_turn_numbers():
    transcript = adapt_aci(
        "[doctor] Fictional question. [patient] I have a cough.", source("aci-bench")
    )
    aligned = align_evidence_item(
        EvidenceItem(segment_id="turn-0002", quote="a cough"), transcript
    ).aligned
    assert aligned is not None
    assert (aligned.char_start, aligned.char_end) == (7, 14)
    assert aligned.turn_number == 2 and aligned.audio_source == "untimed"
    assert (
        aligned.audio_start_seconds
        is aligned.audio_end_seconds
        is aligned.audio_approximate
        is None
    )
    rendered = render_transcript(transcript.segments[1:])
    assert "turn 2" in rendered and "00:" not in rendered


def test_human_utterance_evidence_does_not_claim_asr_word_precision():
    transcript = adapt_primock(grid([(2, 8, "Fictional quoted words")]), grid([]), source())
    aligned = align_evidence_item(
        EvidenceItem(segment_id="doctor-0001", quote="quoted words"), transcript
    ).aligned
    assert aligned.audio_source == "human_utterance_bounds" and aligned.audio_approximate
    assert (aligned.audio_start_seconds, aligned.audio_end_seconds) == (2, 8)


def test_untimed_note_receipts_for_aligned_and_failed_quotes(terminology, units):
    original, intake, assertions = demo_input()
    raw = original.model_dump(mode="json")
    raw.update(schema_version="2", timing_mode="untimed", timing_source="none")
    for number, segment in enumerate(raw["segments"], 1):
        segment.update(start_seconds=None, end_seconds=None, words=None, turn_number=number)
    transcript = Transcript.model_validate(raw)
    outcomes, validation = validate_mappings(
        FakeValidator(), intake, Mapper(intake, transcript, terminology, units).map_all(assertions)
    )
    items = note_items(outcomes, transcript, validation)
    note = render_note(
        items,
        NoteMetadata(
            model_id="fictional",
            prompt_version="fictional",
            run_timestamp="2026-11-12T00:00:00Z",
            fictional_demo=False,
        ),
        export_summary="Fictional test",
    )
    assert "[turn " in note and "no audio time is inferred" in note
    assert "[00:" not in note and "--:--" not in note
    # Failed alignment still identifies the actual cited source turn.
    evidence = outcomes[0].evidence
    failed = evidence.items[0].model_copy(update={"aligned": None})
    outcome = outcomes[0].model_copy(
        update={"evidence": evidence.model_copy(update={"items": (failed,)})}
    )
    assert "[turn " in note_items((outcome,), transcript, validation)[0].receipts


def run_without_backend(manifest, tmp_path, *, locked_run=False):
    def forbidden(*args):
        pytest.fail("test data reached a model, mapper or validator")

    return run_dataset(
        manifest,
        tmp_path / "output",
        load_frozen_config(),
        ["qwen3-8b-q4_k_m"],
        server_factory=forbidden,
        mapper_factory=forbidden,
        validator_factory=forbidden,
        ledger=tmp_path / "ledger.jsonl",
        locked_run=locked_run,
    )


def test_first_gold_test_run_refuses_before_extraction_and_records_attempt(tmp_path):
    shutil.copytree(MANIFEST.parent, tmp_path / "data")
    data = DATASET.model_dump(mode="json")
    data["split"] = "test"
    for encounter in data["encounters"]:
        encounter["split"] = "test"
    path = tmp_path / "data/manifest.json"
    path.write_text(canonical_json(data))
    with pytest.raises(ValueError, match="locked-test-run"):
        run_without_backend(path, tmp_path)
    assert not (tmp_path / "output").exists()
    entry = json.loads((tmp_path / "ledger.jsonl").read_text())
    assert entry["action"] == "run_refused" and not entry["repeated"]


def test_public_test_manifest_refuses_without_flag_then_requires_real_annotation(tmp_path):
    _, test = build_splits("primock57", corpus(57), "a" * 64)
    manifest = tmp_path / "public.json"
    manifest.write_text(canonical_json(test.model_dump(mode="json")))
    with pytest.raises(ValueError, match="locked-test-run"):
        run_without_backend(manifest, tmp_path)
    with pytest.raises(ValueError, match="no clinician gold"):
        run_without_backend(manifest, tmp_path, locked_run=True)
    assert not (tmp_path / "output").exists()
    assert json.loads((tmp_path / "ledger.jsonl").read_text())["action"] == "run_refused"


def test_public_gold_run_scans_entire_corpora_before_model(tmp_path, monkeypatch):
    original = DATASET.model_dump(mode="json")
    original["split"] = "dev"
    entry = original["encounters"][0]
    entry["split"] = "dev"
    data_dir = tmp_path / "gold"
    shutil.copytree(MANIFEST.parent, data_dir)
    tpath = data_dir / entry["transcript_path"]
    raw = json.loads(tpath.read_text())
    raw["schema_version"] = "2"
    raw["source"] = source("aci-bench").model_dump(mode="json")
    transcript = Transcript.model_validate(raw)
    tpath.write_text(transcript.to_json())
    entry["transcript_sha256"] = transcript.content_hash
    original["encounters"] = [entry]
    manifest = data_dir / "manifest.json"
    manifest.write_text(canonical_json(original))
    # An unused corpus encounter triggers refusal, even though the selected dev visit is clean.
    outside = adapt_aci("[patient] Distinct corpus sentence with overlap.", source("aci-bench"))
    monkeypatch.setattr("auris.setup.datasets.verified_corpus", lambda *_: {"unused": outside})
    monkeypatch.setattr(
        "auris.eval.runs.prompt_text", lambda: "Distinct corpus sentence with overlap."
    )
    with pytest.raises(ValueError, match="leakage"):
        run_without_backend(manifest, tmp_path)
    saved = json.loads((tmp_path / "output/leakage.json").read_text())
    assert saved["leakage_check_details"]["clinical_policy"] == "warn"
    assert saved["leakage_check"] == "fail"
    assert saved["leakage_check_details"]["corpus_text_count"] == 3
