"""A5 gates are conjunctive; fictional fixtures never select a production model."""

import pytest
from eval_helpers import DATASET, one_dataset, prediction

from auris.eval.metrics import score_model
from auris.eval.reports import selection


def selection_input():
    # Structural selection arithmetic only: explicitly fictional synthetic counts.
    encounters = tuple(
        DATASET.encounters[0].model_copy(
            update={
                "id": f"fake-{i}",
                "source": "PriMock57" if i < 17 else "ACI-Bench",
                "split": "dev",
            }
        )
        for i in range(40)
    )
    dataset = DATASET.model_copy(update={"split": "dev", "encounters": encounters})

    def model(tp):
        return {
            "coverage": {"declared_kinds": ["symptom"], "declared_fact_coverage": 0.5},
            "lenient": {
                "micro": {"f1": tp / 100},
                "micro_ci": {"f1": {"half_width": 0.02}},
                "encounter_counts": {e.id: {"symptom": (tp, 100, 100)} for e in encounters},
            },
            "critical_errors": {
                stage: {"wrong_person": {"count": 0}}
                for stage in ("pre_review", "exported_without_review")
            },
            "metadata": {
                "whole_pipeline_budget_measurement": {
                    "reference": "laptop",
                    "peak_bytes": 10 * 1024**3,
                    "seconds_per_600_audio_seconds": 1000,
                }
            },
        }

    from auris.eval.critical import CRITICAL_ERRORS

    models = {"default": model(70), "comparator": model(73)}
    for item in models.values():
        for stage in item["critical_errors"]:
            item["critical_errors"][stage] = {name: {"count": 0} for name in CRITICAL_ERRORS}
    return dataset, {"models": models}


def test_exact_three_point_selection_gate():
    dataset, scores = selection_input()
    result = selection(dataset, scores, "default", seed=1, samples=40)
    assert result["verdict"] == "replacement eligible"
    comparison = result["comparisons"]["comparator"]
    assert comparison["eligible_to_replace_default"]
    assert comparison["delta_micro_lenient_f1"] == pytest.approx(0.03)
    assert comparison["paired_delta_ci"]["low"] == pytest.approx(0.03)
    assert comparison["paired_delta_ci"]["high"] == pytest.approx(0.03)


@pytest.mark.parametrize(
    "gate",
    [
        "delta",
        "coverage",
        "pre_review",
        "exported_without_review",
        "memory",
        "time",
        "unknown_budget",
        "cohort",
    ],
)
def test_each_gate_prevents_replacement(gate):
    dataset, scores = selection_input()
    candidate = scores["models"]["comparator"]
    if gate == "delta":
        candidate["lenient"]["micro"]["f1"] = 0.729
    elif gate == "coverage":
        candidate["coverage"]["declared_fact_coverage"] = 0.6
    elif gate in ("pre_review", "exported_without_review"):
        candidate["critical_errors"][gate]["wrong_person"]["count"] = 1
    elif gate == "memory":
        candidate["metadata"]["whole_pipeline_budget_measurement"]["peak_bytes"] = 12 * 1024**3
    elif gate == "time":
        candidate["metadata"]["whole_pipeline_budget_measurement"][
            "seconds_per_600_audio_seconds"
        ] = 1201
    elif gate == "unknown_budget":
        candidate["metadata"] = {"runtime": {"peak_rss_bytes": 1}}
    else:
        dataset = dataset.model_copy(update={"split": "test"})
    result = selection(dataset, scores, "default", seed=1, samples=20)
    assert not result["comparisons"]["comparator"]["eligible_to_replace_default"]


def test_wide_intervals_label_default_provisional():
    dataset, scores = selection_input()
    scores["models"]["default"]["lenient"]["micro_ci"]["f1"]["half_width"] = 0.03001
    result = selection(dataset, scores, "default", seed=1, samples=20)
    assert result["default_label"] == "provisional"
    scores["models"]["default"]["lenient"]["micro_ci"]["f1"]["half_width"] = 0.03
    assert (
        selection(dataset, scores, "default", seed=1, samples=20)["default_label"]
        == "not provisional"
    )


def test_fictional_verdict_even_without_default():
    assert (
        selection(DATASET, {"models": {}}, "absent", seed=1, samples=20)["verdict"]
        == "not applicable: fictional harness-check set"
    )


def test_source_stratification_and_deterministic_class_ci():
    first = DATASET.encounters[0].model_copy(update={"source": "fictional-source-a"})
    second = DATASET.encounters[1].model_copy(update={"source": "fictional-source-b"})
    dataset = one_dataset(first, second)
    predictions = {first.id: tuple(prediction(g) for g in first.assertions), second.id: ()}
    report = score_model(dataset, predictions, seed=23, samples=30)
    assert report == score_model(dataset, predictions, seed=23, samples=30)
    assert report["by_source"]["fictional-source-a"]["lenient"]["micro"]["f1"] == 1
    assert report["by_source"]["fictional-source-b"]["lenient"]["micro"]["f1"] == 0
    assert report["lenient"]["per_class_f1_ci"]["AllergyIntolerance"]["seed"] == 23
