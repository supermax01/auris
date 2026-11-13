"""Extraction artifacts, fail-closed parsing and export safety; fictional data."""

import json
from types import SimpleNamespace

import pytest
from extract_helpers import case_input, completion

from auris.assertions import extraction_json_schema
from auris.config import load_frozen_config
from auris.extract.backend import ExtractionError, FakeBackend
from auris.extract.pipeline import extract, finish_pipeline, parse_completion
from auris.extract.prompt import (
    WINDOW_INSTRUCTION,
    messages,
    prompt_sha256,
    prompt_text,
    render_transcript,
)
from auris.fhir import Mapper
from auris.fhir.models import LaneState
from auris.validation.models import ValidationRun
from auris.validation.outcomes import classify


def test_prompt_snapshot_and_hash():
    transcript, _ = case_input("vital_sign")
    segment = transcript.segments[0].model_copy(
        update={
            "id": "s3",
            "role": "patient",
            "start_seconds": 42.1,
            "end_seconds": 45.0,
            "text": "I have no cough.",
        }
    )
    assert render_transcript([segment]) == "[s3 | patient | 00:42.1–00:45.0] I have no cough."
    assert messages([segment]) == [
        {"role": "system", "content": prompt_text()},
        {
            "role": "user",
            "content": WINDOW_INSTRUCTION.format(ids="s3")
            + "[s3 | patient | 00:42.1–00:45.0] I have no cough.",
        },
    ]
    assert prompt_sha256() == "0db6b464e45c02bfe5f16ee9f392f4b8456ecb3ceabcef71611fbdabd31ab581"
    assert "fictional" in prompt_text()


@pytest.mark.parametrize(
    "response,reason",
    [
        (completion(finish_reason="length"), "truncated"),
        (completion(content="```json\n{}\n```"), "schema-invalid"),
        (completion(content='{"assertions": [], "invented": 1}'), "schema-invalid"),
        (completion(content='{"assertions": [], "assertions": []}'), "duplicate"),
        (completion(content='{"assertions": NaN}'), "schema-invalid"),
        (completion(content='{"assertions": [42]}'), "schema-invalid"),
        (completion(finish_reason="tool_calls"), "finish normally"),
        ({"choices": []}, "exactly one"),
        ({}, "schema-invalid"),
    ],
)
def test_parse_and_truncation_fail_closed(response, reason):
    with pytest.raises(ExtractionError, match=reason):
        parse_completion(response)


def test_unsupported_thinking_or_saturation_fails():
    response = completion()
    response["choices"][0]["message"]["reasoning_content"] = "fictional thought"
    with pytest.raises(ExtractionError, match="reasoning"):
        parse_completion(response)
    _, assertion = case_input("vital_sign")
    with pytest.raises(ExtractionError, match="assertion bound"):
        parse_completion(completion([assertion] * 64))


def test_truncated_reasoning_is_a_hard_refusal_without_split_retry():
    response = completion(finish_reason="length")
    response["choices"][0]["message"]["reasoning_content"] = "fictional thought"
    transcript, _ = case_input("vital_sign")
    config = load_frozen_config().extractor
    run = extract(FakeBackend([response]), transcript, config, config.candidate())
    assert "reasoning" in run.failure
    assert len(run.calls) == 1 and not run.splits and not run.assertions


def fake_validator():
    return SimpleNamespace(
        validate=lambda resources: ValidationRun(
            resources=tuple(classify(r, (), ()) for r in resources), wall_seconds=0
        )
    )


@pytest.mark.parametrize(
    "case,changes,quote,reason",
    [
        (
            "affirmed_allergy",
            {},
            "I am not allergic to penicillin.",
            "polarity_conflicts_with_quote",
        ),
        (
            "vital_sign",
            {"concept_text": "blood pressure", "value_text": "128 over 82", "polarity": "negated"},
            "Your blood pressure is 128 over 82.",
            "negation_not_in_quote",
        ),
    ],
)
def test_fake_backend_regressions_are_never_final_exportable(
    case, changes, quote, reason, terminology, units, intake, tmp_path
):
    transcript, assertion = case_input(case, changes=changes, quote=quote)
    mapper = Mapper(intake, transcript, terminology, units)
    config = load_frozen_config().extractor
    backend = FakeBackend([completion([assertion])])
    run = extract(backend, mapper.transcript, config, config.candidate())
    outcomes, _ = finish_pipeline(run, mapper, fake_validator(), tmp_path / "run")
    assert run.failure is None and len(backend.calls) == 1
    assert outcomes[0].export_state != "final_exportable"
    assert outcomes[0].is_material and reason in outcomes[0].review_reasons
    assert backend.calls[0]["schema"] == extraction_json_schema()
    assert backend.calls[0]["temperature"] == 0


def test_positive_control_can_export_and_metadata_is_recorded(terminology, units, intake, tmp_path):
    transcript, assertion = case_input("vital_sign")
    mapper = Mapper(intake, transcript, terminology, units)
    config = load_frozen_config().extractor
    backend = FakeBackend([completion([assertion])])
    run = extract(backend, mapper.transcript, config, config.candidate())
    outcomes, _ = finish_pipeline(run, mapper, fake_validator(), tmp_path / "run")
    assert outcomes[0].export_state == "final_exportable"  # Safety tests are not vacuous.
    document = json.loads((tmp_path / "run/extraction.json").read_text())
    metadata = document["metadata"]
    assert metadata["model_id"] == config.candidate().id
    assert metadata["model_sha256"] == config.gguf_sha256
    assert metadata["runtime"]["version"] == "fake (offline test)"
    assert "peak_rss_bytes" in metadata["runtime"]
    assert metadata["prompt_sha256"] == prompt_sha256()
    assert metadata["prompt_tokens"] > 0
    assert metadata["decoding"]["thinking"] is False
    assert metadata["token_usage"][0]["total_tokens"] == 333
    assert metadata["wall_seconds"] > 0
    assert document["calls"][0]["raw_response"] == completion([assertion])
    for path in (tmp_path / "run").iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "run").stat().st_mode & 0o777 == 0o700


def test_failed_run_writes_diagnostics_without_partial_mappings(
    terminology, units, intake, tmp_path
):
    transcript, assertion = case_input("vital_sign")
    mapper = Mapper(intake, transcript, terminology, units)
    config = load_frozen_config().extractor
    backend = FakeBackend([completion([assertion], finish_reason="length")])
    run = extract(backend, mapper.transcript, config, config.candidate())
    outcomes, validation = finish_pipeline(run, mapper, fake_validator(), tmp_path / "failed")
    assert outcomes == () and validation is None and not run.assertions
    assert len(backend.calls) == 1  # No temperature-zero retries.
    document = json.loads((tmp_path / "failed/validation.json").read_text())
    assert document["status"] == "failed" and document["resources"] == []
    assert "truncated" in document["reason"]


def test_merge_conflict_forces_review_even_after_passing_lanes(
    terminology, units, intake, tmp_path
):
    transcript, assertion = case_input("vital_sign")
    mapper = Mapper(intake, transcript, terminology, units)
    config = load_frozen_config().extractor
    run = extract(
        FakeBackend([completion([assertion])]), mapper.transcript, config, config.candidate()
    )
    run.conflict_positions = frozenset({0})
    outcomes, _ = finish_pipeline(run, mapper, fake_validator(), tmp_path / "conflict")
    assert outcomes[0].is_material
    assert "merge_conflict" in outcomes[0].review_reasons
    assert outcomes[0].export_state == "needs_review"


def test_absent_validator_records_not_checked_with_reason(terminology, units, intake, tmp_path):
    from auris.validation.runner import ValidatorRunner

    transcript, assertion = case_input("vital_sign")
    mapper = Mapper(intake, transcript, terminology, units)
    config = load_frozen_config()
    run = extract(
        FakeBackend([completion([assertion])]),
        mapper.transcript,
        config.extractor,
        config.extractor.candidate(),
    )
    runner = ValidatorRunner(config, tmp_path / "missing-validator")
    outcomes, validation = finish_pipeline(run, mapper, runner, tmp_path / "run")
    assert validation.failure
    assert outcomes[0].lanes.base_r4 == LaneState.NOT_CHECKED
    assert outcomes[0].export_state == "pending_validation"


def test_output_reuse_is_rejected(terminology, units, intake, tmp_path):
    transcript, assertion = case_input("vital_sign")
    mapper = Mapper(intake, transcript, terminology, units)
    config = load_frozen_config().extractor
    run = extract(
        FakeBackend([completion([assertion])]), mapper.transcript, config, config.candidate()
    )
    (tmp_path / "stale.json").write_text("fictional")
    with pytest.raises(ExtractionError, match="must be empty"):
        finish_pipeline(run, mapper, fake_validator(), tmp_path)


def test_cli_help_registers_extract(capsys):
    from auris.cli import main

    with pytest.raises(SystemExit) as result:
        main(["extract", "--help"])
    assert result.value.code == 0
    assert "--transcript" in capsys.readouterr().out


def test_schema_failure_is_fail_not_not_checked(terminology, units, intake, tmp_path):
    transcript, _ = case_input("vital_sign")
    mapper = Mapper(intake, transcript, terminology, units)
    config = load_frozen_config().extractor
    run = extract(
        FakeBackend([completion(content='{"assertions": {}}')]),
        transcript,
        config,
        config.candidate(),
    )
    finish_pipeline(run, mapper, fake_validator(), tmp_path / "invalid")
    doc = json.loads((tmp_path / "invalid/validation.json").read_text())
    assert doc["lanes"]["reduced_schema"]["state"] == "FAIL"
    assert doc["lanes"]["evidence"]["state"] == "NOT_CHECKED"


def test_startup_failure_has_diagnostics_and_no_partial_result(tmp_path):
    from auris.extract.pipeline import extract_local

    transcript, _ = case_input("vital_sign")
    config = load_frozen_config().extractor
    run = extract_local(transcript, config, config.candidate(), tmp_path / "missing.gguf")
    assert "model missing" in run.failure
    assert not run.assertions and not run.calls
    assert run.wall_seconds > 0


def test_malformed_response_envelope_does_not_escape_guard():
    response = completion()
    response["choices"][0]["message"] = None
    with pytest.raises(ExtractionError, match="schema-invalid"):
        parse_completion(response)


def test_cli_does_not_print_input_validation_values(tmp_path, capsys):
    from auris.cli import main

    path = tmp_path / "fictional-invalid.json"
    path.write_text('{"segments": "fictional-private-transcript-marker"}')
    status = main(
        [
            "extract",
            "--transcript",
            str(path),
            "--intake",
            str(path),
            "--out",
            str(tmp_path / "run"),
        ]
    )
    assert status == 1
    output = capsys.readouterr()
    assert "invalid transcript or intake" in output.err
    assert "fictional-private-transcript-marker" not in output.err


def test_foreign_segment_reference_fails_only_its_assertion(terminology, units, intake, tmp_path):
    transcript, assertion = case_input("vital_sign")
    data = assertion.model_dump(mode="json")
    data["evidence"][0]["segment_id"] = "s999"
    config = load_frozen_config().extractor
    run = extract(
        FakeBackend(
            [completion(content=json.dumps({"assertions": [data, assertion.model_dump()]}))]
        ),
        transcript,
        config,
        config.candidate(),
    )
    assert run.failure is None and len(run.assertions) == 2
    assert run.reduced_schema_state == LaneState.PASS
    assert run.citation_failures == {0: frozenset({"s999"})}
    outcomes, _ = finish_pipeline(
        run, Mapper(intake, transcript, terminology, units), fake_validator(), tmp_path / "run"
    )
    failed, valid = outcomes
    assert failed.lanes.evidence == LaneState.FAIL
    assert failed.evidence.failures == ("citation_outside_window",)
    assert "citation_outside_window" in failed.review_reasons
    assert failed.export_state == "validation_failed"
    assert valid.export_state == "final_exportable"


@pytest.mark.parametrize("bad_id", ["s0", "nonexistent"])
def test_seventh_call_bad_citation_survives_splits_and_cannot_merge_or_export(
    bad_id, terminology, units, intake, tmp_path
):
    from auris.fhir.models import resource_digest
    from auris.transcript import Transcript

    source, assertion = case_input("vital_sign")
    transcript = Transcript(
        segments=tuple(source.segments[0].model_copy(update={"id": f"s{i}"}) for i in range(16))
    )

    def cited(*ids):
        return assertion.model_copy(
            update={
                "evidence": tuple(
                    assertion.evidence[0].model_copy(update={"segment_id": id_}) for id_ in ids
                )
            }
        )

    # Fictional reproduction of consultation06: four truncations, then an
    # out-of-window citation in call seven; later windows must still run.
    bad = cited("s8", bad_id)
    responses = [
        completion(finish_reason="length"),
        completion([cited("s0")]),
        completion(finish_reason="length"),
        completion(finish_reason="length"),
        completion([cited("s7")]),
        completion(finish_reason="length"),
        completion([bad, cited("s9")]),
        completion([cited("s9")]),
        completion([cited("s12")]),
    ]
    config = load_frozen_config().extractor
    run = extract(FakeBackend(responses), transcript, config, config.candidate())
    assert run.failure is None and len(run.calls) == 9
    assert run.calls[6]["citation_failure_positions"] == [0]
    assert run.citation_failures == {2: frozenset({bad_id})}
    assert len(run.assertions) == 5  # Valid overlap merged; failed assertion retained.
    outcomes, _ = finish_pipeline(
        run, Mapper(intake, transcript, terminology, units), fake_validator(), tmp_path / "run"
    )
    failed = outcomes[2]
    assert failed.lanes.reduced_schema == LaneState.PASS
    assert failed.lanes.evidence == LaneState.FAIL and failed.evidence.label is None
    assert failed.evidence.items[0].aligned is not None
    assert failed.evidence.items[1].failure == "citation_outside_window"
    assert failed.evidence.items[1].aligned is None
    assert "citation_outside_window" in failed.review_reasons
    assert all(o.export_state == "final_exportable" for i, o in enumerate(outcomes) if i != 2)
    assert failed.resource is not None
    approved = failed.model_copy(
        update={"reviewed_resource_digest": resource_digest(failed.resource)}
    )
    assert approved.export_state == "validation_failed"
    saved = json.loads((tmp_path / "run/extraction.json").read_text())
    assert saved["citation_failures"] == [
        {"assertion_position": 2, "reason": "citation_outside_window", "segment_ids": [bad_id]}
    ]


@pytest.mark.parametrize("fictional", [False, True])
def test_cli_runs_the_pipeline_and_selects_model(
    fictional, terminology, units, intake, tmp_path, monkeypatch, capsys
):
    from contextlib import nullcontext

    from auris import terminology as terminology_module
    from auris.cli import main
    from auris.extract import pipeline
    from auris.fhir import contracts
    from auris.validation import runner as runner_module

    transcript, assertion = case_input("vital_sign")
    transcript.save(tmp_path / "transcript.json")
    intake = intake.model_copy(update={"fictional": fictional})
    intake.save(tmp_path / "intake.json")
    config = load_frozen_config().extractor
    selected = []

    def local(transcript, config, model, path):
        selected.append(model.id)
        return extract(FakeBackend([completion([assertion])]), transcript, config, model)

    monkeypatch.setattr(pipeline, "extract_local", local)
    monkeypatch.setattr(contracts, "load_unit_sets", lambda *args: units)
    monkeypatch.setattr(
        terminology_module, "TerminologyIndex", lambda path: nullcontext(terminology)
    )
    runners = []

    def runner(*args, **kwargs):
        runners.append(kwargs)
        return fake_validator()

    monkeypatch.setattr(runner_module, "ValidatorRunner", runner)
    status = main(
        [
            "extract",
            "--transcript",
            str(tmp_path / "transcript.json"),
            "--intake",
            str(tmp_path / "intake.json"),
            "--out",
            str(tmp_path / "run"),
            "--model",
            "gemma-4-e4b-it-q4_0",
        ]
    )
    assert status == 0
    assert selected == [config.candidate("gemma-4-e4b-it-q4_0").id]
    assert runners == [{"allow_example_urls": fictional}]
    assert "wrote extraction.json and validation.json" in capsys.readouterr().out
    assert json.loads((tmp_path / "run/extraction.json").read_text())["status"] == "complete"


def test_later_chunk_failure_discards_all_partial_assertions(monkeypatch):
    from auris.extract import pipeline
    from auris.extract.chunking import Chunk
    from auris.transcript import Transcript

    transcript, assertion = case_input("vital_sign")
    transcript = Transcript(
        segments=(*transcript.segments, transcript.segments[0].model_copy(update={"id": "s2"}))
    )
    config = load_frozen_config().extractor
    monkeypatch.setattr(
        pipeline,
        "plan_chunks",
        lambda *args, **kwargs: (
            Chunk(transcript.segments[:1], 100, "fictional"),
            Chunk(transcript.segments[1:], 100, "fictional"),
        ),
    )
    backend = FakeBackend([completion([assertion]), completion(finish_reason="length")])
    run = extract(backend, transcript, config, config.candidate())
    assert run.failure and not run.assertions and len(run.calls) == 2
    assert "parsed_assertions" in run.calls[0]  # diagnostic only; never mapped/exported


def test_conflicting_chunks_keep_both_assertions_and_force_review(
    terminology, units, intake, tmp_path, monkeypatch
):
    from auris.assertions import parse_assertion
    from auris.extract import pipeline
    from auris.extract.chunking import Chunk
    from auris.transcript import Transcript

    transcript, assertion = case_input("vital_sign")
    segment = transcript.segments[0]
    transcript = Transcript(
        segments=(
            segment.model_copy(update={"id": "s0"}),
            segment,
            segment.model_copy(update={"id": "s2"}),
        )
    )
    conflicting = parse_assertion({**assertion.model_dump(mode="json"), "polarity": "negated"})
    config = load_frozen_config().extractor
    monkeypatch.setattr(
        pipeline,
        "plan_chunks",
        lambda *args, **kwargs: (
            Chunk(transcript.segments[:2], 100, "fictional"),
            Chunk(transcript.segments[1:], 100, "fictional"),
        ),
    )
    run = extract(
        FakeBackend([completion([assertion]), completion([conflicting])]),
        transcript,
        config,
        config.candidate(),
    )
    assert run.conflict_positions == {0, 1} and len(run.assertions) == 2
    mapper = Mapper(intake, transcript, terminology, units)
    outcomes, _ = finish_pipeline(run, mapper, fake_validator(), tmp_path / "run")
    assert all(
        outcome.is_material and "merge_conflict" in outcome.review_reasons for outcome in outcomes
    )
    assert all(outcome.export_state != "final_exportable" for outcome in outcomes)
