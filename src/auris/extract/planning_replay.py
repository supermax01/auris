"""Quote-free counterfactual planning from pre-Step-6e extraction metadata.

Run with ``python -m auris.extract.planning_replay cache/primock-dev``.
No inference, tokenizer, transcript loading or writes. This is a simulation,
not a performance result: identical windows reuse recorded completions and
truncations. Unseen windows project a completed enclosing window's output by
segment count, or use the conservative planning estimate if none exists.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auris.extract.chunking import split_segments
from auris.extract.tokens import OUTPUT_ESTIMATE, estimate_output_tokens, output_budget


@dataclass(frozen=True)
class _Window:
    ids: tuple[str, ...]
    input_tokens: int
    transcript_tokens: int


def simulate_planning(document: dict[str, Any]) -> dict[str, Any]:
    """Replay output refinement of the unchanged original context windows.

    Old metadata lacks raw transcript token counts. Subtract its measured
    system prompt count from rendered input (leaving chat framing in the
    estimate). Unrecorded children's remaining tokens scale by segment count;
    recorded children always use their own measured input. This approximation
    can change boundaries, so exact counts require a fresh model run.
    """
    metadata = document["metadata"]
    if "output_estimate" in metadata["decoding"]:
        raise ValueError("replay requires pre-Step-6e metadata with original context windows")
    calls = document["calls"]
    if not calls:
        raise ValueError("replay requires recorded extraction calls")
    by_ids = {tuple(call["segment_ids"]): call for call in calls}
    prompt_tokens = metadata["prompt_tokens"]
    decoding = metadata["decoding"]

    def window(ids: tuple[str, ...], parent: _Window | None = None) -> _Window:
        if ids in by_ids:
            input_tokens = by_ids[ids]["reserved_input_tokens"]
            return _Window(ids, input_tokens, max(0, input_tokens - prompt_tokens))
        assert parent is not None
        transcript_tokens = math.ceil(parent.transcript_tokens * len(ids) / len(parent.ids))
        return _Window(ids, prompt_tokens + transcript_tokens, transcript_tokens)

    def budget(current: _Window) -> int:
        return output_budget(
            current.input_tokens,
            n_ctx=decoding["n_ctx"],
            max_output_tokens=decoding["max_output_tokens"],
        )

    def planned_output(current: _Window) -> int:
        return estimate_output_tokens(current.transcript_tokens, len(current.ids))

    def refine(current: _Window) -> list[_Window]:
        if planned_output(current) <= budget(current) or len(current.ids) <= 1:
            return [current]
        return [
            child for ids in split_segments(current.ids) for child in refine(window(ids, current))
        ]

    roots = [window(tuple(c["segment_ids"])) for c in calls if "." not in c["window_id"]]
    planned = [child for root in roots for child in refine(root)]
    simulated: list[dict[str, Any]] = []
    resolved: set[tuple[str, ...]] = set()

    def attempt(current: _Window) -> None:
        if current.ids in resolved:
            return
        resolved.add(current.ids)
        recorded = by_ids.get(current.ids)
        if recorded is None:
            enclosing = [
                call
                for ids, call in by_ids.items()
                if set(current.ids).issubset(ids) and not call.get("output_discarded")
            ]
            parent = min(enclosing, key=lambda c: len(c["segment_ids"]), default=None)
            output_tokens = (
                math.ceil(
                    parent["usage"]["completion_tokens"]
                    * len(current.ids)
                    / len(parent["segment_ids"])
                )
                if parent is not None
                else planned_output(current)
            )
            simulated.append(
                {
                    "segments": len(current.ids),
                    "output_tokens": output_tokens,
                    "source": "scaled_completed_parent" if parent else "planning_estimate",
                    "truncated": False,
                    "unresolved": planned_output(current) > budget(current),
                }
            )
            return
        truncated = bool(recorded.get("output_discarded"))
        simulated.append(
            {
                "segments": len(current.ids),
                "output_tokens": recorded["usage"]["completion_tokens"],
                "source": "recorded_identical_window",
                "truncated": truncated,
                "unresolved": truncated and len(current.ids) <= 1,
            }
        )
        if truncated and len(current.ids) > 1:
            for ids in split_segments(current.ids):
                attempt(window(ids, current))

    for current in planned:
        attempt(current)
    return {
        "kind": "counterfactual_estimate_not_measured",
        "source_status": document["status"],
        "output_estimate": OUTPUT_ESTIMATE,
        "input_estimate": "recorded input minus system prompt; unseen children scaled by segments",
        "original_calls": len(calls),
        "original_output_tokens": sum(c["usage"]["completion_tokens"] for c in calls),
        "original_discarded_tokens": sum(
            c["usage"]["completion_tokens"] for c in calls if c.get("output_discarded")
        ),
        "planned_windows": len(planned),
        "simulated_calls": len(simulated),
        "simulated_output_tokens": sum(c["output_tokens"] for c in simulated),
        "reused_calls": sum(c["source"] == "recorded_identical_window" for c in simulated),
        "estimated_calls": sum(c["source"] != "recorded_identical_window" for c in simulated),
        "simulated_discarded_tokens": sum(c["output_tokens"] for c in simulated if c["truncated"]),
        "unresolved_windows": sum(c["unresolved"] for c in simulated),
        "windows": simulated,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recorded_runs", type=Path)
    args = parser.parse_args()
    reports = {}
    for path in sorted(args.recorded_runs.glob("*/extraction.json")):
        reports[path.parent.name] = simulate_planning(json.loads(path.read_text("utf-8")))
    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main()
