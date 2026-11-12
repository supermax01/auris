"""Prompt isolation and the pre-inference gate; all examples are fictional."""

import hashlib
import json
from importlib.resources import files

import pytest
from extract_helpers import fixture_transcript_texts

from auris.extract import PromptLeakageError, check_leakage
from auris.extract.prompt import PROMPT_VERSION, prompt_text


def test_active_prompt_has_no_fixture_leakage():
    report = check_leakage(prompt_text(), fixture_transcript_texts())
    assert report.passed, report.model_dump_json(indent=2)
    assert report.metadata()["leakage_check"] == "pass"


def test_original_prompt_is_unchanged_and_catches_the_reported_contamination():
    original = files("auris.extract").joinpath("prompts/extraction_v1.md").read_bytes()
    assert hashlib.sha256(original).hexdigest() == (
        "d87f75a09bf8718eb3f5ecab3789b62236a22949a990402e0100dedf1127be23"
    )
    report = check_leakage(original.decode(), fixture_transcript_texts())
    matches = {hit.corpus_sentence for hit in report.exact_sentences}
    assert {"my mother has diabetes", "i have diabetes", "i have no cough"} <= matches
    assert report.near_duplicates
    assert {hit.term for hit in report.clinical_content} >= {
        "penicillin",
        "diabetes",
        "pneumonia",
        "amoxicillin",
        "cough",
    }
    with pytest.raises(PromptLeakageError, match="evaluation refused"):
        report.raise_if_failed()


@pytest.mark.parametrize(
    "prompt,corpus",
    [
        ('Clinician: "ALPHA, beta gamma delta!" -> example.', "Alpha beta gamma delta."),
        ("“Alpha beta gamma delta.” -> example.", "Alpha beta gamma delta"),
        ("Rule. Alpha beta\tgamma delta. More rules.", "Alpha beta gamma delta?"),
        ("Café today.", "Cafe\u0301 TODAY!"),
        ("I don’t agree.", "I don't agree!"),
        ("Short.", "SHORT!"),
        ("Alpha beta 9.4 units.", "alpha beta 9.4 UNITS"),
    ],
)
def test_normalized_sentences_include_short_ones_and_quoted_examples(prompt, corpus):
    report = check_leakage(prompt, [corpus], clinical_terms=())
    assert len(report.exact_sentences) == 1
    assert not report.near_duplicates
    assert report.exact_sentences[0].jaccard == 1


@pytest.mark.parametrize(
    "prompt,corpus,score",
    [
        ("Alpha beta gamma delta.", "Alpha beta gamma epsilon.", 0.6),
        ("Alpha beta gamma delta.", "Delta gamma beta alpha.", 1.0),
        ("Alpha alpha beta gamma.", "Gamma beta alpha alpha.", 1.0),
        ("I do take medicine.", "I do not take medicine.", 0.8),
    ],
)
def test_jaccard_is_inclusive_uses_sets_and_keeps_negations(prompt, corpus, score):
    report = check_leakage(prompt, [corpus], clinical_terms=())
    assert not report.exact_sentences
    assert len(report.near_duplicates) == 1
    assert report.near_duplicates[0].jaccard == score


@pytest.mark.parametrize(
    "prompt,corpus",
    [
        ("Alpha beta gamma delta.", "Alpha beta epsilon zeta."),
        ("Alpha beta gamma.", "Alpha beta gamma delta."),
        ("Alpha beta gamma delta.", "Alpha beta gamma."),
    ],
)
def test_near_duplicates_require_four_tokens_on_both_sides_and_threshold(prompt, corpus):
    assert check_leakage(prompt, [corpus], clinical_terms=()).passed


def test_decimal_values_do_not_split_into_matching_sentence_fragments():
    report = check_leakage("Reading 9.4 units.", ["Reading 8.4 units."], clinical_terms=())
    assert report.passed


def test_clinical_watchwords_find_content_even_when_sentence_similarity_is_low():
    report = check_leakage("Penicillin.", ["Someone mentioned PENICILLIN yesterday."])
    assert not report.exact_sentences and not report.near_duplicates
    assert [(hit.corpus_index, hit.term) for hit in report.clinical_content] == [(0, "penicillin")]


def test_custom_multiword_terms_use_whole_tokens_and_must_occur_on_both_sides():
    prompt = "Alpha-beta. Gamma. Penicillinase."
    corpus = ["Alpha beta appeared here.", "Alphabetagamma.", "Penicillin mentioned elsewhere."]
    report = check_leakage(
        prompt, iter(corpus), clinical_terms=["ALPHA beta", "penicillin", "gamma"]
    )
    assert [(hit.corpus_index, hit.term) for hit in report.clinical_content] == [(0, "alpha beta")]


def test_multiple_sentences_and_corpus_positions_are_reported_deterministically():
    report = check_leakage("Alpha. Beta!", ["Other.", "Alpha? Beta; Alpha."])
    assert [(hit.corpus_index, hit.corpus_sentence) for hit in report.exact_sentences] == [
        (1, "alpha"),
        (1, "beta"),
    ]
    assert report == check_leakage(
        "Alpha. Beta!", (text for text in ["Other.", "Alpha? Beta; Alpha."])
    )


@pytest.mark.parametrize("corpus", [[], [""], ["..."], ["Valid.", " "]])
def test_empty_or_blank_corpus_cannot_pass(corpus):
    with pytest.raises(ValueError, match="nonempty corpus"):
        check_leakage("Prompt.", corpus)


def test_blank_prompt_cannot_pass():
    with pytest.raises(ValueError, match="nonblank prompt"):
        check_leakage("", ["Fictional."])


def test_bare_strings_cannot_silently_be_checked_as_individual_characters():
    with pytest.raises(TypeError, match="collections of texts"):
        check_leakage("Alpha.", "Alpha")
    with pytest.raises(TypeError, match="collections of texts"):
        check_leakage("Alpha.", ["Alpha"], clinical_terms="Alpha")


def test_metadata_records_failure_and_fingerprints_without_corpus_text():
    report = check_leakage("Alpha beta gamma delta.", ["Alpha beta gamma delta."])
    metadata = report.metadata()
    assert metadata["leakage_check"] == "fail"
    details = metadata["leakage_check_details"]
    assert details["exact_sentence_count"] == 1
    assert details["jaccard_threshold"] == 0.6 and details["min_sentence_tokens"] == 4
    assert details["corpus_text_count"] == 1
    assert "alpha" not in json.dumps(metadata).casefold()
    changed = check_leakage("Alpha beta gamma delta.", ["Different corpus."])
    assert changed.prompt_sha256 == report.prompt_sha256
    assert changed.corpus_sha256 != report.corpus_sha256
    changed.raise_if_failed()


def test_fixture_discovery_covers_standalone_and_nested_transcripts(tmp_path):
    from extract_helpers import case_input

    transcript, _ = case_input("vital_sign")
    nested = tmp_path / "nested"
    nested.mkdir()
    transcript.save(tmp_path / "standalone.json")
    (nested / "gold.json").write_text(
        json.dumps({"cases": [{"transcript": transcript.model_dump()}]})
    )
    (tmp_path / "unrelated.json").write_text('{"text": "not a transcript"}')
    assert fixture_transcript_texts(tmp_path) == (transcript.segments[0].text,) * 2


def test_empty_fixture_directory_cannot_pass(tmp_path):
    with pytest.raises(ValueError, match="no fixture"):
        fixture_transcript_texts(tmp_path)


def test_smoke_test_refuses_leakage_before_runtime_and_records_failure(tmp_path, monkeypatch):
    import test_extract_llm as smoke

    monkeypatch.setattr(smoke, "REPORT", tmp_path / "report.json")
    monkeypatch.setattr(smoke, "prompt_text", lambda: fixture_transcript_texts()[0])

    def unexpected_runtime():
        pytest.fail("runtime must not be consulted after a failed leakage check")

    monkeypatch.setattr(smoke, "find_llama_server", unexpected_runtime)
    with pytest.raises(PromptLeakageError):
        smoke.test_real_qwen_contrastive_smoke(None, None, None)
    report = json.loads(smoke.REPORT.read_text())
    assert report["status"] == "failed" and report["leakage_check"] == "fail"
    assert report["prompt_version"] == PROMPT_VERSION
    assert report["test_type"] == "contrastive_smoke_test"
    assert "not an accuracy claim" in report["interpretation"]
    assert report["cases"] == []


def test_smoke_skip_records_completed_leakage_check(tmp_path, monkeypatch):
    import test_extract_llm as smoke

    monkeypatch.setattr(smoke, "ROOT", tmp_path)
    monkeypatch.setattr(smoke, "REPORT", tmp_path / "report.json")
    with pytest.raises(pytest.skip.Exception, match="model missing"):
        smoke.test_real_qwen_contrastive_smoke(None, None, None)
    report = json.loads(smoke.REPORT.read_text())
    assert report["status"] == "skipped" and report["leakage_check"] == "pass"
    assert report["prompt_version"] == "extraction_v2"
