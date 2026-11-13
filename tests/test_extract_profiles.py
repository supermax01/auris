"""Versioned lean extraction contracts. All consultations here are fictional."""

import hashlib
import json
from pathlib import Path
from typing import get_args

import pytest
from demo_helpers import FakeValidator
from extract_helpers import CASES, case_input, completion, fixture_transcript_texts
from pydantic import ValidationError

from auris.assertions import (
    ASSERTION_MODELS,
    OPTIONAL_DETAIL_FIELDS,
    check_schema_subset,
    extraction_json_schema,
    parse_assertion,
    write_extraction_json_schema,
)
from auris.config import ExtractorConfig, load_frozen_config
from auris.eval.predictions import freeze_prediction
from auris.extract.backend import ExtractionError, FakeBackend
from auris.extract.identity import profile_identity
from auris.extract.leakage import check_leakage
from auris.extract.lean_replay import estimate_response, lean_response
from auris.extract.measurements import extraction_measurements, json_whitespace_stats
from auris.extract.pipeline import extract, finish_pipeline, parse_completion
from auris.extract.profiles import PROFILES, profile_contract
from auris.extract.prompt import prompt_sha256, prompt_text
from auris.extract.tokens import estimate_tokens
from auris.fhir import Mapper
from auris.transcript import Transcript

ROOT = Path(__file__).resolve().parents[1]


def test_v1_schema_is_unchanged_and_both_versions_export(tmp_path):
    full = extraction_json_schema()
    assert hashlib.sha256(json.dumps(full, sort_keys=True).encode()).hexdigest() == (
        "88ddd54be2af2da644fdbd9b352fe7c4b201e11211de2207befacc001ce69bff"
    )
    # Order-sensitive snapshots protect the existing full-v0 / lean-v1 contracts.
    assert hashlib.sha256(json.dumps(full).encode()).hexdigest() == (
        "be28b50e4010e72aebc0a3287b1a51e9036e7ae07c30f4dbc64551a1063ff7ac"
    )
    assert hashlib.sha256(json.dumps(extraction_json_schema("2")).encode()).hexdigest() == (
        "2d88ef01fa5dcc5b8cce8d3baf26f4212be1277369274b4914bc1efc75af2b66"
    )
    for version in ("1", "2"):
        schema = extraction_json_schema(version)
        assert check_schema_subset(schema) == []
        path = tmp_path / f"schema-v{version}.json"
        write_extraction_json_schema(path, schema_version=version)
        assert json.loads(path.read_text()) == schema
    # The only schema change is the required list, at each existing variant.
    lean = extraction_json_schema("2")
    for variant in lean["properties"]["assertions"]["items"]["oneOf"]:
        kind = variant["properties"]["kind"]["enum"][0]
        original = next(
            v
            for v in full["properties"]["assertions"]["items"]["oneOf"]
            if v["properties"]["kind"]["enum"] == [kind]
        )
        assert set(original["required"]) - set(variant["required"]) == (
            OPTIONAL_DETAIL_FIELDS & original["properties"].keys()
        )
        assert {k: v for k, v in original.items() if k != "required"} == {
            k: v for k, v in variant.items() if k != "required"
        }


@pytest.mark.parametrize("model", ASSERTION_MODELS)
def test_v2_optional_defaults_and_required_classifications(model):
    kind = get_args(model.model_fields["kind"].annotation)[0]
    data = dict.fromkeys(model.model_fields)
    data.update(kind=kind, evidence=[{"segment_id": "s1", "quote": "Fictional support."}])
    omitted = {k: v for k, v in data.items() if k not in OPTIONAL_DETAIL_FIELDS}
    assert parse_assertion(omitted, schema_version="2") == parse_assertion(data)
    for key in omitted:
        missing = {k: v for k, v in omitted.items() if k != key}
        with pytest.raises(ValidationError):
            parse_assertion(missing, schema_version="2")
    if len(omitted) != len(data):
        with pytest.raises(ValidationError):
            parse_assertion(omitted)  # Full-v0 still refuses missing detail keys.


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_absent_equals_null_through_mapping_and_scoring(case, profile, terminology, units, intake):
    transcript, full = case_input(case["name"])
    contract = profile_contract(profile)
    wire = completion([full])
    if contract.schema_version == "2":
        wire = lean_response(wire)
    lean = parse_completion(wire, schema_version=contract.schema_version).assertions[0]
    assert lean == full
    mapper = Mapper(intake, transcript, terminology, units)
    first, second = (mapper.map_assertion(a, 0) for a in (full, lean))
    assert first.model_dump_json() == second.model_dump_json()
    assert (
        freeze_prediction(full, first).model_dump_json()
        == freeze_prediction(lean, second).model_dump_json()
    )


@pytest.mark.parametrize("indent", [None, 2, 4])
def test_lean_compact_and_pretty_fallback(indent):
    _, assertion = case_input("ordered_medication")
    wire = json.loads(lean_response(completion([assertion]))["choices"][0]["message"]["content"])
    response = completion(
        content=json.dumps(wire, indent=indent, separators=(",", ":") if indent is None else None)
    )
    assert parse_completion(response, schema_version="2").assertions == (assertion,)


@pytest.mark.parametrize(
    "content",
    [
        '{"assertions":[],"assertions":[]}',
        '{"assertions":NaN}',
        '{"assertions":[42]}',
        '```json\n{"assertions":[]}\n```',
    ],
)
def test_lean_parser_keeps_fail_closed_guards(content):
    with pytest.raises(ExtractionError):
        parse_completion(completion(content=content), schema_version="2")


@pytest.mark.parametrize("profile", PROFILES)
def test_profile_leakage_against_authored_examples(profile):
    assert (
        prompt_sha256("lean-v1")
        == "3a475634e959f5da2ac5b1ba6c622bf211fbd89777fd7bf86b2de4b0826e23e4"
    )
    corpus = list(fixture_transcript_texts())
    for directory in (ROOT / "examples", ROOT / "eval/harness-check"):
        for path in directory.rglob("*transcript*.json"):
            corpus.extend(s.text for s in Transcript.load(path).segments)
    corpus.extend(
        line[2:]
        for line in (ROOT / "docs/annotation/guidelines.md").read_text().splitlines()
        if line.startswith("> ")
    )
    report = check_leakage(prompt_text(profile), corpus)
    assert report.passed, report.model_dump_json(indent=2)


@pytest.mark.parametrize(
    "name,digest",
    [
        ("extraction_v1", "d87f75a09bf8718eb3f5ecab3789b62236a22949a990402e0100dedf1127be23"),
        ("extraction_v2", "a66c105f84af129589e5047c1eb3b4457a13b527cebee495030cb7f05cffa93e"),
        ("extraction_v3", "0db6b464e45c02bfe5f16ee9f392f4b8456ecb3ceabcef71611fbdabd31ab581"),
    ],
)
def test_prior_prompts_are_byte_identical(name, digest):
    data = (ROOT / "src/auris/extract/prompts" / f"{name}.md").read_bytes()
    assert hashlib.sha256(data).hexdigest() == digest


def test_whitespace_diagnostics_preserve_spaces_and_escapes_inside_quotes():
    compact = json.dumps(
        {"assertions": [{"quote": 'Fictional words: "quoted" \\ path\nnext'}]},
        separators=(",", ":"),
    )
    assert json_whitespace_stats(compact) == {
        "single_line": True,
        "compact": True,
        "whitespace_outside_strings": 0,
    }
    pretty = json.dumps(json.loads(compact), indent=2)
    measured = json_whitespace_stats(pretty)
    assert not measured["single_line"] and not measured["compact"]
    assert measured["whitespace_outside_strings"] > 0


def test_total_cost_counts_discarded_calls_and_unknown_usage():
    transcript, assertion = case_input("vital_sign")
    calls = [{"actual_output_tokens": 100, "output_discarded": True}, {"actual_output_tokens": 50}]
    measured = extraction_measurements([assertion], calls, transcript, {})
    assert measured["output_tokens_per_assertion"] == 150
    assert measured["quote_characters"]["count"] == 1
    calls.append({"actual_output_tokens": None})
    assert extraction_measurements([assertion], calls, transcript, {})["output_tokens"] is None


@pytest.mark.parametrize("profile", PROFILES)
def test_profile_threads_through_planning_generation_metadata(profile):
    contract = profile_contract(profile)
    config = load_frozen_config().extractor.model_copy(update={"extraction_profile": profile})
    transcript, assertion = case_input("vital_sign")
    response = completion([assertion])
    if contract.schema_version == "2":
        response = lean_response(response)
        response["usage"] = {"prompt_tokens": 111, "completion_tokens": 120}
    backend = FakeBackend([response], input_tokenizer=lambda *_: 111)
    run = extract(backend, transcript, config, config.candidate())
    assert run.failure is None
    schema = extraction_json_schema(contract.schema_version, property_order=contract.property_order)
    assert backend.calls[0]["schema"] == schema
    for actual, expected in zip(
        backend.calls[0]["schema"]["properties"]["assertions"]["items"]["oneOf"],
        schema["properties"]["assertions"]["items"]["oneOf"],
        strict=True,
    ):
        assert list(actual["properties"]) == list(expected["properties"])
    assert backend.calls[0]["messages"][0]["content"] == prompt_text(profile)
    metadata = run.document()["metadata"]
    assert metadata["extraction_profile"] == profile
    assert metadata["schema_version"] == contract.schema_version
    assert metadata["prompt_version"] == contract.prompt_version
    assert metadata["prompt_sha256"] == prompt_sha256(profile)
    assert all(metadata[key] == value for key, value in profile_identity(profile).items())
    assert metadata["decoding"]["json_whitespace"] == (
        f"grammar_{contract.grammar_whitespace}_json"
        if contract.grammar_whitespace is not None
        else "prompt_compact_json"
        if contract.compact_json
        else "server_default"
    )
    assert (
        metadata["measurements"]["output_tokens_per_assertion"]
        == response["usage"]["completion_tokens"]
    )


@pytest.mark.parametrize("quote,expected", [("cough", 1), ("no cough", 0)])
def test_short_quotes_keep_strict_negation_guard(
    quote, expected, terminology, units, intake, tmp_path
):
    transcript, assertion = case_input("negated_symptom")
    shortened = assertion.model_copy(
        update={"evidence": (assertion.evidence[0].model_copy(update={"quote": quote}),)}
    )
    response = lean_response(completion([shortened]))
    response["usage"] = {"prompt_tokens": 111}
    config = load_frozen_config().extractor.model_copy(update={"extraction_profile": "lean-v1"})
    backend = FakeBackend([response], input_tokenizer=lambda *_: 111)
    run = extract(backend, transcript, config, config.candidate())
    assert run.measurements["evidence_failures_by_reason"]["omitted_negation_context"] == expected
    outcomes, _ = finish_pipeline(
        run, Mapper(intake, transcript, terminology, units), FakeValidator(), tmp_path / "run"
    )
    assert run.measurements["evidence_failed_assertions"] == expected
    assert run.measurements["evidence_scope"] == "quote alignment and consistency"
    assert outcomes[0].lanes.evidence == ("FAIL" if expected else "PASS")
    assert run.measurements["output_tokens"] is None  # Missing usage is unknown.


def test_final_evidence_measurements_include_consistency(terminology, units, intake, tmp_path):
    transcript, assertion = case_input("vital_sign", changes={"value_text": "9999"})
    config = load_frozen_config().extractor.model_copy(update={"extraction_profile": "lean-v1"})
    response = completion([assertion])
    backend = FakeBackend([response], input_tokenizer=lambda *_: 111)
    run = extract(backend, transcript, config, config.candidate())
    assert run.measurements["evidence_failed_assertions"] == 0
    outcomes, _ = finish_pipeline(
        run, Mapper(intake, transcript, terminology, units), FakeValidator(), tmp_path / "run"
    )
    assert outcomes[0].consistency.failures
    assert run.measurements["evidence_failed_assertions"] == 1
    for reason in outcomes[0].consistency.failures:
        assert run.measurements["evidence_failures_by_reason"][reason] == 1


def test_serialization_estimate_replays_without_changing_quotes():
    _, assertion = case_input("ordered_medication")
    data = {"assertions": [assertion.model_dump(mode="json")]}
    response = completion(content=json.dumps(data, indent=2))
    report = estimate_response(response, estimate_tokens)
    assert report["canonical_assertions_identical"] and not report["quote_shortening"]
    assert report["saving_fraction"] > 0
    assert report["assertion_count"] == 1


def test_profile_config_rejects_typos_and_retains_legacy_default():
    data = load_frozen_config().extractor.model_dump(mode="json")
    del data["extraction_profile"]
    assert ExtractorConfig.model_validate(data).extraction_profile == "full-v0"
    with pytest.raises(ValidationError):
        ExtractorConfig.model_validate({**data, "extraction_profile": "lean-typo"})
    for profile in PROFILES:
        selected = ExtractorConfig.model_validate({**data, "extraction_profile": profile})
        assert selected.extraction_profile == profile


@pytest.mark.parametrize("profile", PROFILES)
def test_profile_schema_export_and_property_order(profile, tmp_path):
    contract = profile_contract(profile)
    schema = extraction_json_schema(contract.schema_version, property_order=contract.property_order)
    assert check_schema_subset(schema) == []
    output = tmp_path / f"{profile}.json"
    write_extraction_json_schema(
        output, schema_version=contract.schema_version, property_order=contract.property_order
    )
    exported = json.loads(output.read_text())
    canonical = extraction_json_schema(contract.schema_version)
    assert exported == canonical  # Property order is the only possible difference.
    for actual, original in zip(
        exported["properties"]["assertions"]["items"]["oneOf"],
        canonical["properties"]["assertions"]["items"]["oneOf"],
        strict=True,
    ):
        original_keys = list(original["properties"])
        expected = (
            [key for key in original_keys if key != "evidence"] + ["evidence"]
            if profile == "abl-order"
            else original_keys
        )
        assert list(actual["properties"]) == expected
        kind = actual["properties"]["kind"]["enum"][0]
        assert profile_identity(profile)["assertion_property_order"][kind] == expected
        assert actual["required"] == original["required"]


def test_ablation_prompts_change_only_the_predefined_factor():
    full = prompt_text()
    assert prompt_text("abl-optional") == full + "\nOmit fields that were not said.\n"
    assert prompt_text("abl-compact") == full + "\nOutput compact JSON on a single line.\n"
    old_quote = next(line for line in full.splitlines() if line.startswith("- evidence:"))
    new_quote = next(
        line for line in prompt_text("lean-v1").splitlines() if line.startswith("- evidence:")
    )
    assert prompt_text("abl-quotes") == full.replace(old_quote, new_quote)
    assert prompt_text("abl-order") == prompt_text("lean-v1")


def test_profile_identities_are_distinct():
    identities = [profile_identity(p) for p in PROFILES]
    assert len({json.dumps(i, sort_keys=True) for i in identities}) == len(PROFILES)
    # Grammar is a decoding factor; the new profiles deliberately share prompts.
    assert len(
        {
            (
                i["schema_version"],
                i["prompt_sha256"],
                i["schema_property_order"],
                i.get("grammar_sha256"),
            )
            for i in identities
        }
    ) == len(PROFILES)


@pytest.mark.parametrize(
    "profile,digest",
    [
        ("abl-optional", "0804045f2674259d20d6c9b3a73888f8ddd2780a3eb9ba02ae172478dd093600"),
        ("abl-quotes", "fcb8438d00ae90cc7841beada9ae67314772d7ed572e44d32461250a8cb40521"),
        ("abl-compact", "657b885e92b4052b6189f7917da6fa4559ea0d8e9fed37a981b029c04f86350f"),
    ],
)
def test_ablation_prompt_hashes_are_pinned(profile, digest):
    assert prompt_sha256(profile) == digest
