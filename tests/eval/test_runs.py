"""Offline managed-server lifecycle, locked tests, leakage and frozen replay."""

import json
import shutil
from contextlib import contextmanager

import pytest
from demo_helpers import FakeValidator
from eval_helpers import DATASET, MANIFEST, TRANSCRIPTS
from extract_helpers import completion

from auris.assertions import parse_assertion
from auris.cli import main
from auris.config import load_frozen_config
from auris.eval.gold import canonical_json, digest, load_dataset
from auris.eval.ledger import register_test_use
from auris.eval.reports import FICTIONAL_BANNER, compare_run
from auris.eval.runs import run_dataset, score_run
from auris.extract.backend import FakeBackend
from auris.fhir import Mapper


@pytest.fixture
def run_factory(tmp_path, terminology, units):
    def run(
        *,
        manifest=MANIFEST,
        output=None,
        responses=None,
        confirmed=False,
        locked_run=False,
        models=None,
    ):
        config = load_frozen_config()
        dataset, transcripts = load_dataset(manifest)
        events = []

        @contextmanager
        def server(candidate):
            events.append(("start", candidate.id))
            answers = (
                responses
                if responses is not None
                else [
                    completion(
                        [
                            parse_assertion(g.extraction_dict(transcripts[e.id]))
                            for g in e.assertions
                        ]
                    )
                    for e in dataset.encounters
                ]
            )
            backend = FakeBackend(answers, input_tokenizer=lambda *_: 111)
            backend.runtime.peak_rss_bytes = 100
            try:
                yield backend
            finally:
                events.append(("stop", candidate.id))

        def validator(fictional):
            assert events[-1][0] == "stop", "validator must start after model unload"
            return FakeValidator()

        output = output or tmp_path / "run"
        result = run_dataset(
            manifest,
            output,
            config,
            models or [c.id for c in config.extractor.candidates],
            server_factory=server,
            mapper_factory=lambda intake, transcript: Mapper(
                intake, transcript, terminology, units
            ),
            validator_factory=validator,
            ledger=tmp_path / "ledger.jsonl",
            confirmed=confirmed,
            locked_run=locked_run,
            samples=25,
        )
        return output, result, events

    return run


def test_fake_run_replay_compare_byte_identical(run_factory, tmp_path, monkeypatch, capsys):
    output, run, events = run_factory()
    assert [e[0] for e in events] == ["start", "stop"] * len(run["models"])
    assert run["leakage"]["leakage_check"] == "pass"
    assert len(run["models"]) == len(load_frozen_config().extractor.candidates)
    assert all(not m["failed_encounters"] for m in run["model_metadata"].values())
    # Replay cannot touch a model or terminology index, even if caches are absent.
    monkeypatch.setattr(
        "auris.extract.backend.ManagedLlamaServer", lambda *_: pytest.fail("replay inference")
    )
    monkeypatch.setattr(
        "auris.terminology.TerminologyIndex", lambda *_: pytest.fail("replay terminology")
    )
    assert main(["eval", "score", "--run", str(output)]) == 0
    first = (output / "scores.json").read_bytes()
    assert main(["eval", "score", "--run", str(output)]) == 0
    assert (output / "scores.json").read_bytes() == first
    assert main(["eval", "compare", "--run", str(output)]) == 0
    report = compare_run(output)
    json_bytes, md_bytes = (
        (output / "report.json").read_bytes(),
        (output / "report.md").read_bytes(),
    )
    compare_run(output)
    assert (output / "report.json").read_bytes() == json_bytes
    assert (output / "report.md").read_bytes() == md_bytes
    assert FICTIONAL_BANNER in md_bytes.decode()
    assert "## Error analysis" in md_bytes.decode()
    assert "assertion-strict" in md_bytes.decode() and "record-strict" in md_bytes.decode()
    assert report["scorer_version"] == "3"
    assert report["selection"]["verdict"] == "not applicable: fictional harness-check set"
    assert "not applicable: fictional harness-check set" in capsys.readouterr().out
    for model in report["models"].values():
        assert model["lenient"]["micro"]["f1"] == 1
        assert all(e["count"] == 0 for e in model["critical_errors"]["pre_review"].values())
        assert model["metadata"]["runtime"]["peak_rss_bytes"] == 100
        assert "error_analysis" in model
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in output.rglob("*") if p.is_file())


def test_frozen_hash_refuses_mutation(run_factory, tmp_path):
    output, _, _ = run_factory(models=[load_frozen_config().extractor.candidate().id])
    frozen = output / "dataset/hc-01/transcript.json"
    frozen.write_text(frozen.read_text() + " ")
    with pytest.raises(ValueError, match="artifact hash"):
        score_run(output, ledger=tmp_path / "ledger.jsonl")


def test_failure_remains_in_gold_denominator(run_factory, tmp_path):
    output, run, _ = run_factory(
        responses=[completion(content="invalid")] * 20,
        models=[load_frozen_config().extractor.candidate().id],
    )
    scores = score_run(output, ledger=tmp_path / "ledger.jsonl")
    model = scores["models"][run["models"][0]]
    assert model["lenient"]["micro"]["gold_n"] == 60
    assert model["lenient"]["micro"]["f1"] == 0
    assert len(model["metadata"]["failed_encounters"]) == 20


def test_leakage_refuses_before_runtime_and_records_result(run_factory, tmp_path, monkeypatch):
    transcript = TRANSCRIPTS[DATASET.encounters[0].id]
    monkeypatch.setattr("auris.eval.runs.prompt_text", lambda: transcript.segments[1].text)
    with pytest.raises(ValueError, match="leakage"):
        run_factory()
    data = json.loads((tmp_path / "run/leakage.json").read_text())
    assert data["leakage_check"] == "fail"
    assert not (tmp_path / "run/run.json").exists()


def test_test_split_run_once_score_once_and_record_confirmation(run_factory, tmp_path):
    shutil.copytree(MANIFEST.parent, tmp_path / "test-data")
    manifest = tmp_path / "test-data/manifest.json"
    data = DATASET.model_dump(mode="json")
    data["split"] = "test"
    for e in data["encounters"]:
        e["split"] = "test"
    manifest.write_text(canonical_json(data))
    output, _, _ = run_factory(
        manifest=manifest, models=[load_frozen_config().extractor.candidate().id], locked_run=True
    )
    ledger = tmp_path / "ledger.jsonl"
    score_run(output, ledger=ledger)
    before = (output / "scores.json").read_bytes()
    with pytest.raises(ValueError, match="confirm-locked-test-rerun"):
        score_run(output, ledger=ledger)
    score_run(output, ledger=ledger, confirmed=True)
    assert before == (output / "scores.json").read_bytes()
    with pytest.raises(ValueError, match="confirm-locked-test-rerun"):
        run_factory(manifest=manifest, output=tmp_path / "second")
    entries = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [e["action"] for e in entries] == [
        "run",
        "score",
        "score_refused",
        "score",
        "run_refused",
    ]
    assert entries[-2]["confirmed_rerun"] and entries[-2]["repeated"]
    dataset, _ = load_dataset(manifest)
    # Changing split IDs cannot conceal overlapping held-out transcripts.
    renamed = dataset.model_copy(update={"split_id": "renamed"})
    with pytest.raises(ValueError, match="locked test"):
        register_test_use(ledger, renamed, "new-run", "run")
    register_test_use(ledger, renamed, "new-run", "run", confirmed=True)
    with pytest.raises(ValueError, match="reservation"):
        register_test_use(ledger, renamed, "unreserved", "score", confirmed=True)


def test_dev_and_harness_do_not_lock(tmp_path):
    for split in ("dev", "harness-check"):
        dataset = DATASET.model_copy(update={"split": split})
        for _ in range(2):
            assert (
                register_test_use(tmp_path / "ledger.jsonl", dataset, "same", "score")[
                    "locked_test"
                ]
                is False
            )
    assert not (tmp_path / "ledger.jsonl").exists()


def test_compare_can_perform_initial_score_and_receipt_rejects_edits(run_factory, tmp_path):
    output, _, _ = run_factory(models=[load_frozen_config().extractor.candidate().id])
    assert not (output / "scores.json").exists()
    assert main(["eval", "compare", "--run", str(output)]) == 0
    (output / "scores.json").write_text((output / "scores.json").read_text() + " ")
    with pytest.raises(ValueError, match="receipt mismatch"):
        compare_run(output)


@pytest.mark.parametrize("fail", [False, True])
def test_run_cli_uses_frozen_candidates_and_managed_flags(
    tmp_path, terminology, units, monkeypatch, capsys, fail
):
    from contextlib import nullcontext

    from auris.cli import _build_parser
    from auris.extract.backend import RuntimeMetadata

    events = []
    config = load_frozen_config()

    class Manager:
        def __init__(self, candidate, path, *, n_ctx):
            assert path.name == candidate.gguf_filename
            assert n_ctx == config.extractor.n_ctx
            assert candidate.runtime_flags == config.extractor.candidate(candidate.id).runtime_flags
            self.runtime = RuntimeMetadata(version="fake")
            events.append(candidate.id)

        def __enter__(self):
            answers = [completion([])] * 20
            if fail:
                reasoning = completion([])
                reasoning["choices"][0]["message"]["reasoning_content"] = "Fictional reasoning"
                answers[:3] = [reasoning, reasoning, completion(content="invalid")]
            return FakeBackend(answers, input_tokenizer=lambda *_: 111)

        def __exit__(self, *_):
            events.append("unloaded")

    monkeypatch.setattr("auris.extract.backend.ManagedLlamaServer", Manager)
    monkeypatch.setattr("auris.terminology.TerminologyIndex", lambda *_: nullcontext(terminology))
    monkeypatch.setattr("auris.fhir.contracts.load_unit_sets", lambda *_: units)
    monkeypatch.setattr("auris.validation.runner.ValidatorRunner", lambda *_, **kw: FakeValidator())
    out = tmp_path / "cli-run"
    args = [
        "eval",
        "run",
        "--dataset",
        str(MANIFEST),
        "--model",
        config.extractor.candidates[0].id,
        "--model",
        config.extractor.candidates[1].id,
        "--out",
        str(out),
        "--bootstrap-samples",
        "10",
    ]
    assert main(args) == int(fail)
    stderr = capsys.readouterr().err
    saved = json.loads((out / "run.json").read_text())
    for model in saved["models"]:
        reasons = saved["model_metadata"][model]["failure_reasons"]
        assert sum(reasons.values()) == (3 if fail else 0)
        if fail:
            assert f"{model}: 3 failed encounters" in stderr
            assert reasons["extractor returned reasoning despite thinking-off configuration"] == 2
            for reason, count in reasons.items():
                assert f"{count}: {reason}" in stderr
        else:
            assert stderr == ""
    assert events == [
        config.extractor.candidates[0].id,
        "unloaded",
        config.extractor.candidates[1].id,
        "unloaded",
    ]
    assert (
        _build_parser()
        .parse_args(["eval", "score", "--run", str(out), "--confirm-locked-test-rerun"])
        .confirm_locked_test_rerun
    )


def test_version_one_replay_enriches_from_frozen_artifacts_without_mutation(run_factory, tmp_path):
    output, document, _ = run_factory(models=[load_frozen_config().extractor.candidate().id])
    model = document["models"][0]
    path = output / model / "predictions.json"
    rows = json.loads(path.read_text())
    for predictions in rows.values():
        for p in predictions:
            for field in ("resource", "review_reasons", "quoted_evidence"):
                del p[field]
    path.write_text(canonical_json(rows))
    document["scorer_version"] = "1"
    document["scorer_sha256"] = "a" * 64
    del document["model_metadata"][model]["failure_reasons"]
    document["artifacts"][f"{model}/predictions.json"] = digest(path.read_bytes())
    (output / "run.json").write_text(canonical_json(document))
    frozen_run, frozen_predictions = (output / "run.json").read_bytes(), path.read_bytes()
    result = score_run(output, ledger=tmp_path / "ledger.jsonl")
    assert result["scorer_version"] == "3"
    assert result["inference_scorer"] == {"version": "1", "sha256": "a" * 64}
    assert result["models"][model]["metadata"]["failure_reasons"] == {}
    compare_run(output)
    assert (output / "run.json").read_bytes() == frozen_run
    assert path.read_bytes() == frozen_predictions
    first = (output / "scores.json").read_bytes()
    score_run(output, ledger=tmp_path / "ledger.jsonl")
    assert (output / "scores.json").read_bytes() == first


def test_v2_run_replays_both_scorers_without_mutating_predictions(
    run_factory, tmp_path, monkeypatch
):
    output, document, _ = run_factory(models=[load_frozen_config().extractor.candidate().id])
    document["scorer_version"] = "2"
    (output / "run.json").write_text(canonical_json(document))
    frozen = {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
    monkeypatch.setattr(
        "auris.extract.backend.ManagedLlamaServer", lambda *_: pytest.fail("replay inference")
    )
    for version in ("2", "3"):
        assert main(["eval", "score", "--run", str(output), "--scorer-version", version]) == 0
        report = compare_run(output)
        assert report["scorer_version"] == version
        assert report["inference_scorer"]["version"] == "2"
        for model in report["models"].values():
            assert ("noncritical_errors" in model) == (version == "3")
        assert all(p.read_bytes() == contents for p, contents in frozen.items())
