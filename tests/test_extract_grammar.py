"""Grammar equivalence; all generated documents describe fictional facts."""

import copy
import hashlib
import itertools
import json
import random
import re
from unittest.mock import Mock

import pytest
from extract_helpers import case_input, completion
from gbnf_helpers import grammar_pattern
from pydantic import ValidationError

from auris.assertions import (
    MAX_ASSERTIONS,
    MAX_EVIDENCE_ITEMS,
    ExtractionResult,
    extraction_json_schema,
    parse_extraction_json,
)
from auris.config import load_frozen_config
from auris.extract.backend import ExtractionError, HttpBackend
from auris.extract.grammar import GRAMMAR_VERSIONS, compact_grammar, json_grammar, newline_grammar
from auris.extract.identity import profile_identity
from auris.extract.pipeline import extract, parse_completion
from auris.extract.profiles import PROFILES, profile_contract
from auris.extract.prompt import messages, prompt_text

GRAMMAR_PROFILES = (
    "grammar-compact",
    "grammar-compact-quotes",
    "grammar-newline",
    "grammar-newline-quotes",
)
STRINGS = (
    "Fictional supporting words.",
    'Fictional "quote" \\ slash /',
    "Fictional\nnew line\ttab\rreturn\bbackspace\fform feed",
    "Fictional café Ω 中 🩺",
    "Fictional \x00 control \x1f",
    "Fictional  spaced  words ",
)


def compact(document, *, ascii_only=False):
    return json.dumps(document, ensure_ascii=ascii_only, separators=(",", ":"))


def json_gaps(text):
    """Tokenize test JSON independently; escaped content stays inside strings."""
    tokens = list(
        re.finditer(
            r'"(?:\\.|[^"\\])*"|[{}\[\]:,]|null|true|false'
            r"|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?",
            text,
        )
    )
    assert "".join(token.group() for token in tokens) == text
    return [0, *(token.end() for token in tokens)]


def serialize(document, grammar_factory, *, ascii_only=False):
    text = compact(document, ascii_only=ascii_only)
    if grammar_factory is newline_grammar:
        gaps = set(json_gaps(text))
        return "".join(("\n" if i in gaps else "") + c for i, c in enumerate(text)) + "\n"
    return text


@pytest.fixture(params=(compact_grammar, newline_grammar), ids=("compact", "newline"))
def grammar_factory(request):
    return request.param


def parser_accepts(text, version):
    try:
        parse_completion(completion(content=text), schema_version=version)
    except (ExtractionError, ValidationError):
        return False
    return True


def generated(node, rng):
    """Seeded schema-driven data generation, independent of GBNF construction."""
    if "anyOf" in node:
        return None if rng.randrange(3) == 0 else generated(node["anyOf"][0], rng)
    if "oneOf" in node:
        return generated(rng.choice(node["oneOf"]), rng)
    if "enum" in node:
        return rng.choice(node["enum"])
    match node["type"]:
        case "object":
            return {
                key: generated(child, rng)
                for key, child in node["properties"].items()
                if key in node.get("required", []) or rng.choice((False, True))
            }
        case "array":
            count = rng.randint(node.get("minItems", 0), min(4, node["maxItems"]))
            return [generated(node["items"], rng) for _ in range(count)]
        case "string":
            return rng.choice(STRINGS)
        case _:
            raise AssertionError("unhandled generator node")


@pytest.mark.parametrize("version", ["1", "2"])
@pytest.mark.parametrize("order", ["canonical", "evidence-last"])
def test_seeded_equivalence_all_kinds_nulls_optional_fields_and_invalid_mutations(
    version, order, grammar_factory
):
    schema = extraction_json_schema(version, property_order=order)
    pattern = grammar_pattern(grammar_factory(schema))
    rng = random.Random(810_803)
    variants = schema["properties"]["assertions"]["items"]["oneOf"]
    for iteration in range(72):
        document = generated(schema, rng)
        text = serialize(document, grammar_factory, ascii_only=iteration % 2 == 0)
        assert pattern.fullmatch(text)
        assert parser_accepts(text, version)
    # Cover every kind deterministically, with hundreds of additional generated
    # assertions, optional-key combinations, enum values and string encodings.
    for iteration in range(36):
        for variant in variants:
            assertion = generated(variant, rng)
            document = {"assertions": [assertion]}
            text = serialize(document, grammar_factory, ascii_only=iteration % 2 == 0)
            assert pattern.fullmatch(text), text
            assert parser_accepts(text, version)
            assert parse_extraction_json(text, schema_version=version) == parse_completion(
                completion(content=json.dumps(document, indent=2)), schema_version=version
            )

            mutations = []
            for key in variant["required"]:
                bad = copy.deepcopy(document)
                del bad["assertions"][0][key]
                mutations.append(bad)
            for key in assertion:
                bad = copy.deepcopy(document)
                bad["assertions"][0][key] = 42  # All wire values are strings/null/arrays.
                mutations.append(bad)
            for change in (
                {"kind": "fictional_unsupported_kind"},
                {"invented": "fictional"},
                {"polarity": "fictional_bad_enum"},
                {"evidence": []},
                {"evidence": [{"segment_id": "s1", "quote": ""}]},
                {"evidence": [{"segment_id": "s1", "quote": "Fictional", "extra": None}]},
            ):
                bad = copy.deepcopy(document)
                bad["assertions"][0].update(change)
                mutations.append(bad)
            for bad in mutations:
                text = serialize(bad, grammar_factory)
                assert not pattern.fullmatch(text), text
                assert not parser_accepts(text, version), text


@pytest.mark.parametrize("version", ["1", "2"])
def test_array_bounds_and_existing_post_schema_refusals(version, grammar_factory):
    schema = extraction_json_schema(version)
    pattern = grammar_pattern(grammar_factory(schema))
    rng = random.Random(8)
    assertion = generated(schema["properties"]["assertions"]["items"]["oneOf"][0], rng)
    evidence = {"segment_id": "s1", "quote": "Fictional supporting words."}
    for count in (0, 1, MAX_EVIDENCE_ITEMS, MAX_EVIDENCE_ITEMS + 1):
        value = {**assertion, "evidence": [evidence] * count}
        text = serialize({"assertions": [value]}, grammar_factory)
        assert (
            bool(pattern.fullmatch(text))
            == parser_accepts(text, version)
            == (1 <= count <= MAX_EVIDENCE_ITEMS)
        )
    for count in (0, 1, MAX_ASSERTIONS - 1, MAX_ASSERTIONS, MAX_ASSERTIONS + 1):
        text = serialize({"assertions": [assertion] * count}, grammar_factory)
        grammar_accepts = bool(pattern.fullmatch(text))
        assert grammar_accepts == (count <= MAX_ASSERTIONS)
        # Saturation is deliberately schema-valid and parser-invalid. Preserve
        # the existing safety guard; the full grammar path still refuses it.
        if count == MAX_ASSERTIONS:
            assert len(parse_extraction_json(text, schema_version=version).assertions) == count
        assert (grammar_accepts and parser_accepts(text, version)) == (count < MAX_ASSERTIONS)
    for blank in (" ", "\t\n", "\u2003"):
        for field in ("quote", "segment_id"):
            value = {**assertion, "evidence": [{**evidence, field: blank}]}
            text = serialize({"assertions": [value]}, grammar_factory)
            assert pattern.fullmatch(text)  # minLength=1; nonblank is a model validator.
            assert not parser_accepts(text, version)


def test_all_optional_combinations_including_leading_middle_trailing_and_empty(grammar_factory):
    for required in ([], ["b"], ["a", "c"], ["a", "b", "c"]):
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {key: {"type": "string"} for key in "abc"},
            "required": required,
        }
        pattern = grammar_pattern(grammar_factory(schema))
        for presence in itertools.product((False, True), repeat=3):
            document = {
                key: "fictional" for key, present in zip("abc", presence, strict=True) if present
            }
            assert bool(pattern.fullmatch(serialize(document, grammar_factory))) == set(
                required
            ).issubset(document)
        assert not pattern.fullmatch('{"b":"fictional","a":"fictional","c":"fictional"}')


def test_string_length_means_decoded_scalars_and_empty_arrays_are_supported(grammar_factory):
    pattern = grammar_pattern(grammar_factory({"type": "string", "minLength": 1, "maxLength": 1}))
    for text in ('"x"', '"🩺"', r'"\ud83e\ude7a"', r'"\u0041"', r'"\n"'):
        assert pattern.fullmatch(text)
    for text in ('""', '"xx"', r'"\ud83e"', r'"\ude7a"', r'"\ud83ex"'):
        assert not pattern.fullmatch(text)
    assert grammar_pattern(
        grammar_factory({"type": "array", "maxItems": 0, "items": {"type": "string"}})
    ).fullmatch("[]")


def test_whitespace_escapes_unicode_key_order_and_duplicate_rejections(grammar_factory):
    schema = extraction_json_schema()
    pattern = grammar_pattern(grammar_factory(schema))
    assertion = generated(
        schema["properties"]["assertions"]["items"]["oneOf"][1], random.Random(81)
    )
    text = compact({"assertions": [assertion]})
    assert pattern.fullmatch(text)
    for encoded in (r'"\uD83E\uDE7A"', r'"\u00e9"', r'"\/\\\"\b\f\n\r\t"', '"café 中 Ω 🩺"'):
        escaped = (
            text.replace('"concept_text":null', '"concept_text":' + encoded)
            if assertion["concept_text"] is None
            else text.replace(compact(assertion["concept_text"]), encoded, 1)
        )
        assert pattern.fullmatch(escaped)
        assert parser_accepts(escaped, "1")
    for bad_string in (
        r'"\x20"',
        r'"\uZZZZ"',
        r'"\uD800"',
        r'"\uDC00"',
        '"raw\nnewline"',
        '"\x00"',
    ):
        bad = compact({"assertions": [{**assertion, "concept_text": "PLACEHOLDER"}]}).replace(
            '"PLACEHOLDER"', bad_string
        )
        assert not pattern.fullmatch(bad)
        assert not parser_accepts(bad, "1")
    for bad in (
        " " + text,
        text.replace(":", ": ", 1),
        json.dumps(json.loads(text), indent=2),
        '{"assertions":[],"assertions":[]}',
        '{"assertions":NaN}',
        '{"assertions":[42]}',
        text[:-1] + ',"extra":null}',
        text[:-1],
        text + text,
    ):
        assert not pattern.fullmatch(bad)
    assert bool(pattern.fullmatch(text + "\n")) == (grammar_factory is newline_grammar)
    reversed_assertion = dict(reversed(list(assertion.items())))
    reordered = compact({"assertions": [reversed_assertion]})
    assert parser_accepts(reordered, "1") and not pattern.fullmatch(reordered)
    # Canonical ordering is the requested generation constraint, not a parser change.
    assert pattern.fullmatch(compact({"assertions": [dict(assertion)]}))
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate_json('{"assertions":[42]}', strict=True)


def test_fixed_keys_and_enums_reject_every_escaped_spelling(grammar_factory):
    schema = extraction_json_schema()
    pattern = grammar_pattern(grammar_factory(schema))
    # Regressions for the observed root-key failure, including a fully escaped key.
    for text in (
        r'{"\u0061ssertions":[]}',
        r'{"\u0061\u0073\u0073\u0065\u0072\u0074\u0069\u006f\u006e\u0073":[]}',
    ):
        assert parser_accepts(text, "1")
        assert not pattern.fullmatch(text)
    for variant in schema["properties"]["assertions"]["items"]["oneOf"]:
        assertion = generated(variant, random.Random(81))
        # Make every enum value deterministic, including nullable enums.
        for key, child in variant["properties"].items():
            enum = child["anyOf"][0] if "anyOf" in child else child
            for value in enum.get("enum", []):
                assertion[key] = value
                text = compact({"assertions": [assertion]})
                escaped = '"' + "".join(f"\\u{ord(c):04x}" for c in value) + '"'
                bad = text.replace(compact(value), escaped, 1)
                assert parser_accepts(bad, "1")
                assert not pattern.fullmatch(bad)
        text = compact({"assertions": [assertion]})
        # This includes nested evidence keys and every assertion property.
        for token in re.finditer(r'"([^"\\]+)":', text):
            key = token.group(1)
            for i, char in enumerate(key):
                escaped = key[:i] + f"\\u{ord(char):04x}" + key[i + 1 :]
                bad = text[: token.start(1)] + escaped + text[token.end(1) :]
                assert parser_accepts(bad, "1")
                assert not pattern.fullmatch(bad)


def test_fixed_strings_preserve_literal_unicode_and_slashes(grammar_factory):
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"fictional/café": {"type": "string", "enum": ["fictional/Ω"]}},
        "required": ["fictional/café"],
    }
    pattern = grammar_pattern(grammar_factory(schema))
    text = '{"fictional/café":"fictional/Ω"}'
    assert pattern.fullmatch(text)
    for bad in (
        text.replace("/", r"\/", 1),
        text.replace("é", r"\u00e9"),
        text.replace("Ω", r"\u03A9"),
    ):
        assert json.loads(bad) == json.loads(text)
        assert not pattern.fullmatch(bad)


@pytest.mark.parametrize("value", ['fictional"', "fictional\\", "fictional\n", "\ud800"])
def test_unrepresentable_fixed_strings_fail_instead_of_allowing_escapes(value, grammar_factory):
    for schema in (
        {"type": "string", "enum": [value]},
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {value: {"type": "string"}},
        },
    ):
        with pytest.raises(ValueError, match="without escapes"):
            grammar_factory(schema)


def test_newline_whitespace_at_every_json_gap_and_no_indentation():
    schema = extraction_json_schema()
    pattern = grammar_pattern(newline_grammar(schema))
    assertion = generated(
        schema["properties"]["assertions"]["items"]["oneOf"][1], random.Random(81)
    )
    # Two objects exercise },\n{; two evidence items exercise nested commas.
    assertion["evidence"] *= 2
    for document in ({"assertions": []}, {"assertions": [assertion, assertion]}):
        text = compact(document)
        assert pattern.fullmatch(text)
        gaps = json_gaps(text)
        for position in gaps:
            allowed = text[:position] + "\n" + text[position:]
            assert pattern.fullmatch(allowed)
            assert parser_accepts(allowed, "1")
            for whitespace in (" ", "\t", "\r", "\r\n", "\n\n", "\n ", "\n\t", "\n    "):
                bad = text[:position] + whitespace + text[position:]
                assert parser_accepts(bad, "1")  # Formatting only; parser stays unchanged.
                assert not pattern.fullmatch(bad), (position, whitespace)
        assert pattern.fullmatch(serialize(document, newline_grammar))
        # Mixed gaps are allowed; a newline is optional independently at each gap.
        rng = random.Random(810_804)
        selected = {position for position in gaps if rng.choice((True, False))}
        mixed = "".join(("\n" if i in selected else "") + c for i, c in enumerate(text))
        mixed += "\n" if len(text) in selected else ""
        assert pattern.fullmatch(mixed)
        assert not pattern.fullmatch(json.dumps(document, indent=4))


@pytest.mark.parametrize(
    "schema,text",
    [
        ({"type": "object", "properties": {}, "additionalProperties": False}, "{}"),
        (
            {
                "type": "object",
                "properties": {"optional": {"type": "string"}},
                "additionalProperties": False,
            },
            "{}",
        ),
        ({"type": "array", "items": {"type": "string"}, "maxItems": 0}, "[]"),
        ({"type": "array", "items": {"type": "string"}, "maxItems": 2}, "[]"),
    ],
)
def test_empty_containers_have_only_one_interior_whitespace_slot(schema, text):
    pattern = grammar_pattern(newline_grammar(schema))
    assert pattern.fullmatch(text[:1] + "\n" + text[1:])
    assert not pattern.fullmatch(text[:1] + "\n\n" + text[1:])


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "integer"},
        {"type": "string", "pattern": "x"},
        {"type": "array", "items": {"type": "string"}},
        {"type": "array", "maxItems": 1, "minItems": 2, "items": {"type": "string"}},
        {"type": "object", "properties": {}},
        {"type": "object", "properties": {}, "additionalProperties": False, "required": ["x"]},
        {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        {"oneOf": [{"type": "string"}, {"type": "string"}]},
        {
            "oneOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"kind": {"type": "string", "enum": ["fictional"]}},
                }
            ]
        },
        {"type": "string", "format": "date"},
        {"type": "string", "minLength": -1},
    ],
)
def test_converter_rejects_unimplemented_constraints(schema, grammar_factory):
    with pytest.raises(ValueError):
        grammar_factory(schema)


@pytest.mark.parametrize(
    "mode,version,digest",
    [
        (
            "compact",
            "compact-gbnf-v2",
            "9bc29ac88cef31951b407362dd0ed3860894dcc8b81d3d8eed970545a894e1e8",
        ),
        (
            "newline",
            "newline-gbnf-v1",
            "25436cfa1b5e97577694bbdc1a483c98bdee362a81cfed2247f74e68a4ae82db",
        ),
    ],
)
def test_converter_is_deterministic_and_versioned_without_mutating_schema(mode, version, digest):
    schema = extraction_json_schema()
    original = copy.deepcopy(schema)
    first = json_grammar(schema, whitespace=mode)
    assert first == json_grammar(copy.deepcopy(schema), whitespace=mode)
    assert schema == original
    assert ('ws ::= ""\n' if mode == "compact" else 'ws ::= "\\n"?\n') in first
    assert GRAMMAR_VERSIONS[mode] == version
    assert hashlib.sha256(first.encode()).hexdigest() == digest


@pytest.mark.parametrize("profile", PROFILES)
def test_transport_and_measurement_use_selected_constraint(profile):
    contract = profile_contract(profile)
    schema = extraction_json_schema(contract.schema_version, property_order=contract.property_order)
    grammar = (
        json_grammar(schema, whitespace=contract.grammar_whitespace)
        if contract.grammar_whitespace is not None
        else None
    )
    kwargs = {"grammar": grammar} if grammar is not None else {}
    client = HttpBackend("http://127.0.0.1:8000")
    client.request = Mock(
        side_effect=[{"prompt": "fictional rendered chat"}, {"tokens": [1]}, completion()]
    )
    assert client.count_input_tokens(messages([], profile), schema, **kwargs) == 1
    client.complete(messages([], profile), schema, temperature=0, max_tokens=1024, **kwargs)
    template, _, complete = client.request.call_args_list
    body = complete.args[1]
    assert template.args[1] == {
        key: value
        for key, value in body.items()
        if key not in ("temperature", "max_tokens", "stream")
    }
    assert ("grammar" in body) == (contract.grammar_whitespace is not None)
    assert ("response_format" in body) == (contract.grammar_whitespace is None)
    if grammar is not None:
        assert body["grammar"] == grammar
        assert (
            profile_identity(profile)["grammar_sha256"]
            == hashlib.sha256(grammar.encode()).hexdigest()
        )


@pytest.mark.parametrize(
    "profile,digest",
    [
        ("full-v0", "fcfca5b7bdd5f9b5dc9647907a4fbd0a215aa46b9c72ae89cd92ecc29d01092d"),
        ("lean-v1", "bdd78b0e2c5554e6901f84d5895e28c2334c773ce6fe6a04a66af1e2d0b38ad0"),
        ("abl-optional", "097362dce0e9b1d563ef4cccca06e6e23adb1e13530640e8b24bd4b404ade8a9"),
        ("abl-quotes", "6a450571d7357d1fa368f3b8988f6abb73f9b947e2f88cbfa2f4dd1288e28a8a"),
        ("abl-compact", "b10c0713130d65b428cb4c0fea04de4d283c4f62db4a883ffb029439de1c4a12"),
        ("abl-order", "8d0d142e2344f1f5386eb71ecd9c986cde1e6dbfb68f32fbb30a69a7364a7904"),
    ],
)
def test_prior_profile_request_bytes_match_committed_backend(profile, digest):
    # Captured by executing HEAD's backend before Step 8c, with these inputs.
    contract = profile_contract(profile)
    client = HttpBackend("http://127.0.0.1:8000")
    client.request = Mock(return_value=completion())
    client.complete(
        messages([], profile),
        extraction_json_schema(contract.schema_version, property_order=contract.property_order),
        temperature=0,
        max_tokens=1024,
    )
    body = json.dumps(client.request.call_args.args[1], allow_nan=False).encode()
    assert hashlib.sha256(body).hexdigest() == digest


@pytest.mark.parametrize("profile", GRAMMAR_PROFILES)
def test_pipeline_passes_grammar_to_counting_generation_and_records_identity(profile):
    mode = profile_contract(profile).grammar_whitespace
    config = load_frozen_config().extractor.model_copy(update={"extraction_profile": profile})
    transcript, assertion = case_input("vital_sign")
    client = HttpBackend("http://127.0.0.1:8000")

    def request(path, payload):
        if path == "/tokenize":
            return {"tokens": [0] * 111}
        if path == "/apply-template":
            assert "grammar" in payload and "response_format" not in payload
            return {"prompt": "fictional rendered chat"}
        assert path == "/v1/chat/completions"
        assert "grammar" in payload and "response_format" not in payload
        text = serialize(
            {"assertions": [assertion.model_dump(mode="json")]},
            newline_grammar if mode == "newline" else compact_grammar,
        )
        assert grammar_pattern(payload["grammar"]).fullmatch(text)
        return completion(content=text)

    client.request = Mock(side_effect=request)
    run = extract(client, transcript, config, config.candidate())
    assert run.failure is None
    metadata = run.document()["metadata"]
    assert metadata["grammar_version"] == GRAMMAR_VERSIONS[mode]
    assert metadata["grammar_sha256"] == profile_identity(profile)["grammar_sha256"]
    assert metadata["decoding"]["json_whitespace"] == f"grammar_{mode}_json"
    assert metadata["input_token_comparisons"][0]["status"] == "match"
    assert (run.calls[0]["json_whitespace"]["whitespace_outside_strings"] > 0) == (
        mode == "newline"
    )


def test_grammar_profiles_use_only_the_pre_registered_prompt_bytes():
    assert prompt_text("grammar-compact") == prompt_text("full-v0")
    assert prompt_text("grammar-compact-quotes") == prompt_text("abl-quotes")
    assert prompt_text("grammar-newline") == prompt_text("full-v0")
    assert prompt_text("grammar-newline-quotes") == prompt_text("abl-quotes")
    for profile in GRAMMAR_PROFILES:
        contract = profile_contract(profile)
        assert contract.schema_version == "1" and contract.property_order == "canonical"
        assert not contract.compact_json and contract.grammar_whitespace is not None
        assert "Output compact JSON on a single line." not in prompt_text(profile)
