"""Counterfactual accounting using fictional, quote-free call metadata."""

import pytest

from auris.extract.planning_replay import simulate_planning


def recorded_call(window_id, ids, input_tokens, output_tokens, *, truncated=False):
    return {
        "window_id": window_id,
        "segment_ids": ids,
        "reserved_input_tokens": input_tokens,
        "usage": {"completion_tokens": output_tokens},
        "output_discarded": truncated,
    }


def recorded_run(calls):
    return {
        "status": "complete",
        "metadata": {
            "prompt_tokens": 900,
            "decoding": {"n_ctx": 8192, "max_output_tokens": 4096},
        },
        "calls": calls,
    }


def test_replay_skips_recorded_truncated_parent_and_reuses_measured_children():
    ids = [f"fictional-{i}" for i in range(100)]
    report = simulate_planning(
        recorded_run(
            [
                recorded_call("0", ids, 5900, 2036, truncated=True),
                recorded_call("0.0", ids[:51], 3450, 2400),
                recorded_call("0.1", ids[49:], 3450, 2200),
            ]
        )
    )
    assert report["original_calls"] == 3 and report["original_output_tokens"] == 6636
    assert report["planned_windows"] == report["simulated_calls"] == report["reused_calls"] == 2
    assert report["simulated_output_tokens"] == 4600
    assert report["estimated_calls"] == report["simulated_discarded_tokens"] == 0


def test_replay_retains_unpredictable_truncation_and_labels_unseen_outputs_estimated():
    ids = [f"fictional-{i}" for i in range(6)]
    report = simulate_planning(recorded_run([recorded_call("0", ids, 1200, 4096, truncated=True)]))
    assert report["planned_windows"] == 1 and report["simulated_calls"] == 3
    assert report["estimated_calls"] == 2 and report["reused_calls"] == 1
    assert report["simulated_output_tokens"] == 4096 + 2 * (128 + 250)
    assert report["simulated_discarded_tokens"] == 4096
    assert report["kind"] == "counterfactual_estimate_not_measured"
    assert report["unresolved_windows"] == 0


def test_replay_projects_unseen_children_from_successful_parent_with_overlap():
    ids = [f"fictional-{i}" for i in range(100)]
    report = simulate_planning(recorded_run([recorded_call("0", ids, 5900, 1800)]))
    assert report["planned_windows"] == report["simulated_calls"] == 2
    assert report["estimated_calls"] == 2 and report["reused_calls"] == 0
    assert report["simulated_output_tokens"] == 1836  # Two 51/100 projections.
    assert all(w["source"] == "scaled_completed_parent" for w in report["windows"])


def test_replay_rejects_already_planned_runs():
    document = recorded_run([])
    document["metadata"]["decoding"]["output_estimate"] = {}
    with pytest.raises(ValueError, match="pre-Step-6e"):
        simulate_planning(document)
