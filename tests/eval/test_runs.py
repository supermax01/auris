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
from auris.extract.identity import profile_identity
from auris.extract.profiles import PROFILES
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
        profiles=None,
        extractor_seed=None,
    ):
        config = load_frozen_config()
        if extractor_seed is not None:
            config = config.model_copy(
                update={"extractor": config.extractor.model_copy(update={"seed": extractor_seed})}
            )
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
            profiles=profiles,
        )
        return output, result, events

    return run


def test_same_model_two_profiles_compare_and_replay(run_factory, tmp_path):
    candidate = load_frozen_config().extractor.candidate()
    output, run, events = run_factory(models=[candidate.id], profiles=["full-v0", "lean-v1"])
    assert run["models"] == [f"{candidate.id}@full-v0", f"{candidate.id}@lean-v1"]
    assert events == [(action, candidate.id) for _ in range(2) for action in ("start", "stop")]
    assert all(p["leakage_check"] == "pass" for p in run["leakage"]["profiles"].values())
    frozen = {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
    score_run(output, ledger=tmp_path / "ledger.jsonl")
    report = compare_run(output)
    comparison = report["profile_comparisons"][candidate.id]
    assert comparison["delta_micro_lenient_f1"] == 0
    assert comparison["f1_target_met"] and comparison["no_critical_error_increase"]
    assert comparison["evidence_failure_rate_delta"] == 0
    assert comparison["output_tokens_per_assertion_saving_fraction"] == 0
    for profile in ("full-v0", "lean-v1"):
        metadata = report["models"][f"{candidate.id}@{profile}"]["metadata"]
        assert metadata["extraction_profile"] == profile
        assert len(metadata["measurements_by_encounter"]) == 20
        assert metadata["measurements_by_encounter"]["hc-01"]["quote_characters"]["count"] > 0
    assert all(p.read_bytes() == value for p, value in frozen.items())
    before = [(output / name).read_bytes() for name in ("report.json", "report.md")]
    compare_run(output)
    assert before == [(output / name).read_bytes() for name in ("report.json", "report.md")]
    assert "Profile comparisons" in before[1].decode()


def test_all_profiles_freeze_identity_and_early_stop_is_verified_read_only(
    run_factory, tmp_path, monkeypatch, capsys
):
    candidate = load_frozen_config().extractor.candidate()
    output, run, _ = run_factory(models=[candidate.id], profiles=list(PROFILES))
    assert run["models"] == [f"{candidate.id}@{p}" for p in PROFILES]
    assert all(a["leakage_check"] == "pass" for a in run["leakage"]["profiles"].values())
    for identity, metadata in run["model_metadata"].items():
        expected = profile_identity(metadata["extraction_profile"])
        assert all(metadata[key] == value for key, value in expected.items())
        extraction = json.loads((output / identity / "hc-01/extraction.json").read_text())
        assert all(extraction["metadata"][key] == value for key, value in expected.items())
    frozen = {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
    monkeypatch.setattr(
        "auris.extract.backend.ManagedLlamaServer", lambda *_: pytest.fail("analysis inference")
    )
    monkeypatch.setattr(
        "auris.terminology.TerminologyIndex", lambda *_: pytest.fail("analysis terminology")
    )
    assert main(["eval", "early-stop", "--run", str(output)]) == 0
    text = capsys.readouterr().out
    report = json.loads(text)
    assert report["notice"] == run["notice"]
    for row in report["models"].values():
        assert row["assertions_per_visit"]["mean"] == 3
        assert row["single_assertion_reference_ge3"] == {
            "visits": 0,
            "reference_ge3_visits": 18,
            "share": 0,
        }
        assert row["completion_tokens"]["total"] == 20 * 222
        assert row["finish_reasons"] == {"stop": 20}
    assert main(["eval", "early-stop", "--run", str(output)]) == 0
    assert capsys.readouterr().out == text
    assert frozen == {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
    score_run(output, ledger=tmp_path / "ledger.jsonl")
    for row in json.loads((output / "scores.json").read_text())["models"].values():
        assert row["lenient"]["micro"]["f1"] == 1
    frozen_input = output / run["models"][0] / "hc-01/extraction.json"
    frozen_input.write_text(frozen_input.read_text() + " ")
    assert main(["eval", "early-stop", "--run", str(output)]) == 1
    assert "frozen artifact hash mismatch" in capsys.readouterr().err


def test_ablation_run_flags_select_requested_models_and_profiles():
    from auris.cli import _build_parser

    args = [
        "eval",
        "run",
        "--dataset",
        str(MANIFEST),
        "--model",
        "bonsai-2-27b-q2_0",
        "--model",
        "gemma-4-e4b-it-q4_0",
        "--out",
        "cache/eval-runs/step-8b-ablation",
    ]
    for profile in PROFILES:
        args.extend(["--extraction-profile", profile])
    parsed = _build_parser().parse_args(args)
    assert parsed.model == ["bonsai-2-27b-q2_0", "gemma-4-e4b-it-q4_0"]
    assert parsed.extraction_profile == list(PROFILES)


def test_newline_run_flags_select_the_pre_registered_grid():
    from auris.cli import _build_parser

    models = [
        "bonsai-2-27b-q2_0",
        "gemma-4-e4b-it-q4_0",
        "qwen3-8b-q4_k_m",
        "bonsai-2-9b-q2_0",
    ]
    profiles = ["grammar-newline", "grammar-newline-quotes"]
    args = ["eval", "run", "--dataset", str(MANIFEST)]
    for model in models:
        args.extend(["--model", model])
    for profile in profiles:
        args.extend(["--extraction-profile", profile])
    args.extend(["--out", "cache/eval-runs/step-8d-newline"])
    parsed = _build_parser().parse_args(args)
    assert parsed.model == models
    assert parsed.extraction_profile == profiles


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


def test_each_selected_profile_is_audited_before_inference(run_factory, tmp_path, monkeypatch):
    from auris.extract.prompt import prompt_text

    transcript = TRANSCRIPTS[DATASET.encounters[0].id]
    monkeypatch.setattr(
        "auris.eval.runs.prompt_text",
        lambda profile="full-v0": (
            transcript.segments[1].text if profile == "lean-v1" else prompt_text()
        ),
    )
    with pytest.raises(ValueError, match="leakage"):
        run_factory(profiles=["full-v0", "lean-v1"])
    data = json.loads((tmp_path / "run/leakage.json").read_text())
    assert data["leakage_check"] == "fail"
    assert data["profiles"]["full-v0"]["leakage_check"] == "pass"
    assert data["profiles"]["lean-v1"]["leakage_check"] == "fail"
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
@pytest.mark.parametrize("profiles", [None, ["full-v0", "lean-v1"]])
def test_run_cli_uses_frozen_candidates_and_managed_flags(
    tmp_path, terminology, units, monkeypatch, capsys, fail, profiles
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
    for profile in profiles or []:
        args.extend(["--extraction-profile", profile])
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
        event
        for candidate in config.extractor.candidates[:2]
        for _ in profiles or ["full-v0"]
        for event in (candidate.id, "unloaded")
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
    del document["extraction_profiles"]
    for field in (
        "extraction_profile",
        "schema_version",
        "prompt_version",
        "prompt_sha256",
        "schema_property_order",
        "assertion_property_order",
        "measurements_by_encounter",
    ):
        del document["model_metadata"][model][field]
    del document["model_metadata"][model]["failure_reasons"]
    document["artifacts"][f"{model}/predictions.json"] = digest(path.read_bytes())
    (output / "run.json").write_text(canonical_json(document))
    frozen_run, frozen_predictions = (output / "run.json").read_bytes(), path.read_bytes()
    result = score_run(output, ledger=tmp_path / "ledger.jsonl")
    assert result["scorer_version"] == "3"
    assert result["inference_scorer"] == {"version": "1", "sha256": "a" * 64}
    assert result["models"][model]["metadata"]["failure_reasons"] == {}
    compare_run(output)
    old_reports = [(output / name).read_bytes() for name in ("report.json", "report.md")]
    assert "profile_comparisons" not in json.loads(old_reports[0])
    assert "extraction_profile" not in result["models"][model]["metadata"]
    assert (output / "run.json").read_bytes() == frozen_run
    assert path.read_bytes() == frozen_predictions
    first = (output / "scores.json").read_bytes()
    score_run(output, ledger=tmp_path / "ledger.jsonl")
    assert (output / "scores.json").read_bytes() == first
    compare_run(output)
    assert old_reports == [(output / name).read_bytes() for name in ("report.json", "report.md")]


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


def test_card_seed_and_quarantines_freeze_without_predictions(run_factory, tmp_path):
    candidate = load_frozen_config().extractor.candidate()
    dataset, transcripts = load_dataset(MANIFEST)
    responses = []
    for encounter in dataset.encounters:
        raw = [g.extraction_dict(transcripts[encounter.id]) for g in encounter.assertions]
        # A primitive malformed item cannot enter matching, mapping or scoring.
        responses.append(completion(content=json.dumps({"assertions": [*raw, 42]})))
    output, run, _ = run_factory(
        models=[candidate.id], profiles=["full-v0-card"], extractor_seed=2, responses=responses
    )
    identity = f"{candidate.id}@full-v0-card@seed-2"
    assert run["models"] == [identity]
    metadata = run["model_metadata"][identity]
    assert metadata["sampling_parameters"]["seed"] == 2
    assert metadata["quarantined_item_count"] == 20
    predictions = json.loads((output / identity / "predictions.json").read_text())
    assert sum(map(len, predictions.values())) == 60
    scores = score_run(output, ledger=tmp_path / "ledger.jsonl")
    assert scores["models"][identity]["lenient"]["micro"]["f1"] == 1
    assert scores["models"][identity]["metadata"]["quarantined_item_count"] == 20
    assert run["default_model"] == identity
    assert compare_run(output)["models"][identity]["metadata"]["quarantined_item_count"] == 20
