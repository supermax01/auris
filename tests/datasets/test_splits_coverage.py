"""Model-free counts and predeclared split selection, using fictional data only."""

from collections import Counter

import pytest
from dataset_helpers import corpus, source

from auris.datasets.aci import adapt_aci
from auris.datasets.audit import write_frozen
from auris.datasets.coverage import DrugLexicon, aggregate, count_candidates
from auris.datasets.splits import build_splits, strata, stratified_sample
from auris.extract.leakage import PromptLeakageError, check_leakage


def test_primock_split_determinism_and_proportional_length_strata():
    data = corpus(57)
    first = build_splits("primock57", data, "a" * 64)
    assert first == build_splits("primock57", dict(reversed(list(data.items()))), "a" * 64)
    dev, test = first
    assert (len(dev.encounters), len(test.encounters)) == (17, 40)
    assert not {e.id for e in dev.encounters} & {e.id for e in test.encounters}
    assert Counter(e.length_stratum for e in dev.encounters) == {"short": 6, "medium": 6, "long": 5}
    assert len({e.id for s in first for e in s.encounters}) == 57
    assert dev.gold_status == "none (awaiting clinician annotation)"
    assert first != build_splits("primock57", data, "a" * 64, seed=1730)
    assert all(e.transcript_sha256 == data[e.id].content_hash for s in first for e in s.encounters)


def test_aci_required_upstream_cohorts_and_train_one_per_stratum():
    data = {}
    for split, size in (("train", 55), ("valid", 20), ("test1", 40), ("test2", 40), ("test3", 40)):
        group = corpus(size, dataset="aci-bench", split=split)
        # Source text must distinguish otherwise identical fictional cohort visits.
        for key, transcript in group.items():
            raw = transcript.model_dump(mode="json")
            raw["segments"][0]["text"] += split
            from auris.transcript import Transcript

            data[key] = Transcript.model_validate(raw)
    dev, test = build_splits("aci-bench", data, "a" * 64)
    assert Counter(e.upstream_split for e in dev.encounters) == {"valid": 20, "train": 3}
    assert Counter(e.length_stratum for e in dev.encounters if e.upstream_split == "train") == {
        "short": 1,
        "medium": 1,
        "long": 1,
    }
    assert Counter(e.upstream_split for e in test.encounters) == {
        "test1": 20,
        "test2": 20,
        "test3": 20,
    }
    assert not {e.id for e in dev.encounters} & {e.id for e in test.encounters}


def test_small_population_and_no_overwrite(tmp_path):
    assert stratified_sample({}, 0, seed=1) == set()
    with pytest.raises(ValueError):
        stratified_sample(corpus(2), 3, seed=1)
    assert len(stratified_sample(corpus(2), 1, seed=1)) == 1
    assert set(strata(corpus(3)).values()) == {"short", "medium", "long"}
    output = tmp_path / "frozen.json"
    write_frozen(output, "original")
    write_frozen(output, "original")
    with pytest.raises(ValueError, match="not overwritten"):
        write_frozen(output, "changed")
    assert output.read_text() == "original"


def test_common_terms_warn_but_sentences_always_refuse():
    report = check_leakage(
        "Keep asthma evidence grounded.", ["I have asthma."], clinical_policy="warn"
    )
    assert report.passed and report.metadata()["leakage_check_details"][
        "clinical_warning_counts"
    ] == {"asthma": 1}
    assert not check_leakage("Keep asthma evidence grounded.", ["I have asthma."]).passed
    for text in ("Keep asthma evidence grounded.", "Please keep asthma evidence grounded."):
        report = check_leakage("Keep asthma evidence grounded.", [text], clinical_policy="warn")
        with pytest.raises(PromptLeakageError):
            report.raise_if_failed()


def test_lexical_counter_all_categories_without_model_or_codes():
    transcript = adapt_aci(
        "[doctor] Blood pressure is 120 over 80. Pulse is eighty. Temperature is 37 degrees. "
        "You have a fictional condition. We ruled out a second condition. "
        "I will prescribe paracetamol 500 mg. "
        "[patient] I take acetaminophen. I have a cough. "
        "My mother has a fictional condition. I am allergic to penicillin. No drug allergies.",
        source("aci-bench"),
    )
    counts = count_candidates(transcript, DrugLexicon(["acetaminophen", "penicillin"]))
    assert counts == {
        "vital_sign": 3,
        "symptom": 1,
        "clinician_assessed_problem": 1,
        "clinician_ruled_out_problem": 1,
        "patient_reported_condition": 1,
        "explicit_new_order": 1,
        "reported_medication": 1,
        "allergy_statement": 1,
        "allergy_denial": 1,
        "family_history_cue": 1,
        "drug_name_mention": 3,
        "dosed_order_candidate": 1,
    }
    assert aggregate({"fictional": transcript}, DrugLexicon(["acetaminophen"]))["vital_sign"] == {
        "candidates": 3,
        "encounters": 1,
    }


def test_rxnorm_fixture_index_and_synonym_counts(terminology):
    lexicon = DrugLexicon.from_index(terminology)
    assert lexicon.count("paracetamol acetaminophen") == 2
    assert DrugLexicon(["acetaminophen"]).count("acetaminophenish") == 0
    assert DrugLexicon(["alpha", "alpha beta"]).count("alpha beta alpha") == 2
    transcript = adapt_aci(
        "[doctor] No order for acetaminophen 500 mg. Temperature is normal.", source("aci-bench")
    )
    counts = count_candidates(transcript, lexicon)
    assert (
        counts["explicit_new_order"] == counts["dosed_order_candidate"] == counts["vital_sign"] == 0
    )
