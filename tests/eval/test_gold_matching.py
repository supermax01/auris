"""Assignment verified against exhaustive optima and independent gold concepts."""

import itertools
import json
import random
from collections import Counter
from pathlib import Path

import pytest
from eval_helpers import DATASET, MANIFEST, TRANSCRIPTS, prediction
from extract_helpers import fixture_transcript_texts
from pydantic import ValidationError

from auris.eval.gold import Code, Dataset, GoldAssertion, load_dataset
from auris.eval.matching import concept_equal, eligible, hungarian, match, normalize
from auris.extract.leakage import _sentences, check_leakage
from auris.extract.prompt import prompt_text
from auris.transcript import Transcript


def brute(matrix):
    n, m = len(matrix), len(matrix[0])
    if n > m:
        return brute(list(zip(*matrix, strict=True)))
    return max(
        sum(matrix[i][j] for i, j in enumerate(cols))
        for cols in itertools.permutations(range(m), n)
    )


def test_hungarian_against_seeded_brute_force():
    rng = random.Random(9317)
    for _ in range(600):
        n, m = rng.randint(1, 6), rng.randint(1, 6)
        matrix = [[rng.randint(-8, 8) / 2 for _ in range(m)] for _ in range(n)]
        pairs = hungarian(matrix)
        assert len(pairs) == min(n, m)
        assert len({i for i, _ in pairs}) == len(pairs)
        assert len({j for _, j in pairs}) == len(pairs)
        assert sum(matrix[i][j] for i, j in pairs) == brute(matrix)
        assert hungarian(matrix) == pairs
    assert hungarian([]) == hungarian([[], []]) == ()


@pytest.mark.parametrize("matrix", [[[1], [1, 2]], [[float("nan")]], [[float("inf")]]])
def test_bad_assignment_matrices(matrix):
    with pytest.raises(ValueError):
        hungarian(matrix)


def test_only_adjudicated_texts_define_concept_equivalence():
    gold = (
        DATASET.encounters[6]
        .assertions[0]
        .model_copy(
            update={
                "gold_concept_id": "auris:same-medicine",
                "acceptable_texts": ("medicine", "the same medicine"),
            }
        )
    )
    assert concept_equal(prediction(gold, concept_text="Medicine!"), gold)
    assert concept_equal(prediction(gold, concept_text="the same medicine"), gold)
    unrelated = gold.model_copy(
        update={"gold_concept_id": "auris:other", "acceptable_texts": ("unrelated drug",)}
    )
    assert not concept_equal(prediction(gold, concept_text="medicine"), unrelated)
    # No mapper synonym or drug identity can enter gold equivalence implicitly.
    assert not concept_equal(prediction(gold, concept_text="amoxicillin"), gold)
    assert normalize("−.5") != normalize("5")
    assert normalize("1.5") != normalize("15")


def test_code_identity_and_strict_evidence():
    gold = (
        DATASET.encounters[2]
        .assertions[1]
        .model_copy(
            update={"gold_concept_id": "rxnorm:723", "codes": (Code(system="rxnorm", code="723"),)}
        )
    )
    coded = prediction(gold, concept_text="unlisted wording")
    assert concept_equal(coded, gold)
    assert eligible(coded, gold, strict=True)
    assert not eligible(coded.model_copy(update={"evidence": ()}), gold, strict=True)
    assert eligible(coded.model_copy(update={"evidence": ()}), gold)
    wrong_code = prediction(gold, codes=(Code(system="rxnorm", code="not-the-gold-code"),))
    assert eligible(wrong_code, gold)
    assert not eligible(wrong_code, gold, strict=True)
    assert eligible(prediction(gold, codes=()), gold, strict=True)


def test_strict_vitals_orders_and_class_key():
    vital = DATASET.encounters[2].assertions[2]
    assert eligible(
        prediction(vital, value_text="126.0/78.00", unit_text="mmHg"), vital, strict=True
    )
    for updates in (
        {"value_text": "125/78", "unit_text": "mmHg"},
        {"unit_text": "unknown unit"},
        {"value_text": None, "unit_text": "mmHg"},
        {"unit_text": None},
    ):
        proposed = prediction(vital, **updates)
        assert eligible(proposed, vital)
        assert not eligible(proposed, vital, strict=True)
    order = DATASET.encounters[2].assertions[1]
    for field in ("dose_text", "route_text", "frequency_text"):
        assert not eligible(prediction(order, **{field: "different"}), order, strict=True)
        assert eligible(prediction(order, **{field: "different"}), order)
    condition = DATASET.encounters[1].assertions[2]
    assert eligible(prediction(condition, kind="patient_reported_condition"), condition)
    assert not eligible(prediction(condition, experiencer="other"), condition)
    assert not eligible(prediction(condition, polarity="negated"), condition)


def test_maximum_cardinality_and_duplicate_predictions():
    gold = DATASET.encounters[0].assertions[1]
    other = gold.model_copy(update={"id": "second", "acceptable_texts": ("ache", "pain")})
    predictions = [prediction(gold), prediction(gold, concept_text="pain", id="second")]
    assert len(match(predictions, [gold, other])) == 2
    assert len(match([prediction(gold), prediction(gold, id="duplicate")], [gold])) == 1
    assert match([], [gold]) == ()


def test_harness_is_fictional_complete_and_uncontaminated():
    assert len(DATASET.encounters) == 20
    assert all(e.fictional and e.license == "Apache-2.0" for e in DATASET.encounters)
    assert all(6 <= len(t.segments) <= 20 for t in TRANSCRIPTS.values())
    counts = Counter(a.kind for e in DATASET.encounters for a in e.assertions)
    assert len(counts) == 9 and sum(counts.values()) == 60
    assert (
        min(Counter(t for e in DATASET.encounters for t in e.critical_error_targets).values()) >= 2
    )
    texts = [s.text for t in TRANSCRIPTS.values() for s in t.segments]
    check_leakage(prompt_text(), texts, clinical_terms=DATASET.clinical_watchlist).raise_if_failed()
    existing = list(fixture_transcript_texts())
    existing += [s.text for s in Transcript.load(Path("examples/transcript.json")).segments]
    sentences = {s for text in existing for s in _sentences(text)}
    assert not sentences.intersection(s for text in texts for s in _sentences(text))
    assert '"quote"' not in MANIFEST.read_text()
    assert sum(len(e.out_of_scope) for e in DATASET.encounters) == 2


def test_hash_and_span_refusals(tmp_path):
    import shutil

    shutil.copytree(MANIFEST.parent, tmp_path / "data")
    manifest = tmp_path / "data/manifest.json"
    raw = json.loads(manifest.read_text())
    raw["encounters"][0]["assertions"][0]["evidence"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="evidence hash"):
        load_dataset(manifest)
    raw = json.loads(MANIFEST.read_text())
    raw["encounters"][0]["transcript_sha256"] = "0" * 64
    manifest.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="content hash"):
        load_dataset(manifest)
    raw = json.loads(MANIFEST.read_text())
    raw["encounters"][0]["transcript_path"] = "../outside.json"
    manifest.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="within"):
        load_dataset(manifest)


def test_gold_rejects_inappropriate_fields_and_duplicate_encounters():
    gold = DATASET.encounters[0].assertions[1].model_dump(mode="json")
    gold["dose_text"] = "12 mg"
    with pytest.raises(ValidationError, match="does not belong"):
        GoldAssertion.model_validate(gold)
    raw = DATASET.model_dump(mode="json")
    raw["encounters"].append(raw["encounters"][0])
    with pytest.raises(ValidationError, match="duplicate encounter"):
        Dataset.model_validate(raw)
