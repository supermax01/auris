"""Hand-built early-stop diagnostics; all underlying reference visits are fictional."""

import json

import pytest
from eval_helpers import DATASET

from auris.eval.early_stop import early_stop_report


@pytest.fixture
def frozen_counts(tmp_path, monkeypatch):
    encounters = tuple(
        e.model_copy(update={"assertions": e.assertions[:count]})
        for e, count in zip(DATASET.encounters[:4], (3, 3, 2, 0), strict=True)
    )
    dataset = DATASET.model_copy(update={"encounters": encounters})
    rows = [
        {
            "calls": [
                {
                    "actual_output_tokens": 70,
                    "raw_response": {"choices": [{"finish_reason": "stop"}]},
                },
                {"actual_output_tokens": 30, "finish_reason": "length", "output_discarded": True},
            ],
            "failure": None,
        },
        {"calls": [], "failure": "Fictional startup failure"},
        {
            "calls": [
                {"usage": {"completion_tokens": 10}, "finish_reason": "stop"},
                {"actual_output_tokens": -1, "finish_reason": "tool_calls"},
            ],
            "failure": None,
        },
        {"calls": [{"actual_output_tokens": 0}], "failure": None},
    ]
    identities = ("fictional-model@abl-optional", "fictional-model@abl-order")
    document = {
        "run_id": "fictional-run",
        "notice": "Fictional diagnostics; not accuracy evidence.",
        "models": identities,
        "model_metadata": {
            identity: {"model": {"id": "fictional-model"}, "extraction_profile": profile}
            for identity, profile in zip(identities, ("abl-optional", "abl-order"), strict=True)
        },
        "artifacts": {},
    }
    predictions = {}
    for identity in identities:
        predictions[identity] = {}
        for encounter, row, count in zip(encounters, rows, (1, 0, 1, 1), strict=True):
            relative = f"{identity}/{encounter.id}/extraction.json"
            target = tmp_path / relative
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(row))
            document["artifacts"][relative] = "verified-by-load-run"
            predictions[identity][encounter.id] = (None,) * count
    monkeypatch.setattr(
        "auris.eval.early_stop.load_run", lambda _: (document, dataset, predictions)
    )
    return tmp_path, document, dataset, predictions


def test_hand_built_counts_usage_denominators_and_finish_reasons(frozen_counts):
    output, document, dataset, _ = frozen_counts
    encounters = dataset.encounters
    report = early_stop_report(output)
    assert report["notice"] == document["notice"]
    assert set(report["models"]) == set(document["models"])
    for identity, row in report["models"].items():
        assert row["model_id"] == "fictional-model"
        assert row["extraction_profile"] == identity.split("@")[1]
        assert row["visit_count"] == 4 and row["failed_visits"] == 1
        assert row["assertions_per_visit"] == {
            "count": 4,
            "total": 3,
            "min": 0,
            "max": 1,
            "mean": 0.75,
            "median": 1.0,
        }
        assert row["single_assertion_reference_ge3"] == {
            "visits": 1,
            "reference_ge3_visits": 2,
            "share": 0.5,
        }
        assert row["completion_tokens"] == {
            "count": 2,
            "total": None,
            "known_total": 100,
            "min": 0,
            "max": 100,
            "mean": 50,
            "median": 50,
            "visits_with_unknown_usage": 2,
            "calls_with_usage": 4,
            "call_count": 5,
        }
        assert row["finish_reasons"] == {"stop": 2, "length": 1, "tool_calls": 1, "unknown": 1}
        visits = row["visits"]
        assert visits[encounters[0].id]["completion_tokens"] == 100
        assert visits[encounters[1].id]["completion_tokens"] is None
        assert visits[encounters[2].id]["completion_tokens"] is None
        assert visits[encounters[3].id]["completion_tokens"] == 0


def test_no_eligible_visits_and_legacy_identity_remain_unknown(frozen_counts, monkeypatch):
    output, document, dataset, predictions = frozen_counts
    for metadata in document["model_metadata"].values():
        del metadata["extraction_profile"]
    # A different frozen reference can contain only short visits.
    short = dataset.model_copy(
        update={
            "encounters": tuple(e.model_copy(update={"assertions": ()}) for e in dataset.encounters)
        }
    )
    monkeypatch.setattr("auris.eval.early_stop.load_run", lambda _: (document, short, predictions))
    report = early_stop_report(output)
    for row in report["models"].values():
        assert row["extraction_profile"] is None
        assert row["single_assertion_reference_ge3"] == {
            "visits": 0,
            "reference_ge3_visits": 0,
            "share": None,
        }


def test_missing_extraction_manifest_entry_refused(frozen_counts):
    output, document, _, _ = frozen_counts
    document["artifacts"].clear()
    with pytest.raises(ValueError, match="missing from manifest"):
        early_stop_report(output)
