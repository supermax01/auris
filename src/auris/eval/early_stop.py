"""Read-only, quote-free early-stop diagnostics from verified frozen artifacts."""

import json
from collections import Counter
from pathlib import Path
from typing import Any

from auris.eval.gold import relative_file
from auris.eval.runs import load_run
from auris.extract.measurements import length_stats


def _completion_tokens(call: dict[str, Any]) -> int | None:
    if "actual_output_tokens" in call:
        value = call["actual_output_tokens"]
    else:
        # Older frozen runs retain usage without the normalized token field.
        usage = call.get("usage")
        value = usage.get("completion_tokens") if isinstance(usage, dict) else None
    return value if type(value) is int and value >= 0 else None


def _finish_reason(call: dict[str, Any]) -> str:
    reason = call.get("finish_reason")
    if reason is None:
        raw = call.get("raw_response")
        choices = raw.get("choices") if isinstance(raw, dict) else None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            reason = choices[0].get("finish_reason")
    return reason if isinstance(reason, str) and reason else "unknown"


def early_stop_report(output: Path) -> dict[str, Any]:
    """Count merged assertions per visit and usage/stops across *all* attempts.

    Failed visits stay in both visit denominators. Unknown usage remains null,
    including visits with no calls. The single-assertion share is conditional
    on reference visits containing at least three assertions; it is a diagnostic,
    not a determination that a model stopped prematurely.
    """
    document, dataset, predictions = load_run(output)
    models = {}
    for identity in document["models"]:
        metadata = document["model_metadata"][identity]
        visits = {}
        reasons: Counter[str] = Counter()
        known_tokens: list[int] = []
        total_calls = known_calls = 0
        for encounter in dataset.encounters:
            extraction_path = f"{identity}/{encounter.id}/extraction.json"
            if extraction_path not in document["artifacts"]:
                raise ValueError("frozen extraction artifact missing from manifest")
            extraction = json.loads(relative_file(output, extraction_path).read_text("utf-8"))
            calls = extraction["calls"]
            counts = [_completion_tokens(call) for call in calls]
            visit_reasons = Counter(_finish_reason(call) for call in calls)
            reasons.update(visit_reasons)
            total_calls += len(calls)
            known_calls += sum(count is not None for count in counts)
            tokens = sum(counts) if calls and all(n is not None for n in counts) else None
            if tokens is not None:
                known_tokens.append(tokens)
            visits[encounter.id] = {
                "assertions": len(predictions[identity].get(encounter.id, ())),
                "reference_assertions": len(encounter.assertions),
                "failed": bool(extraction["failure"]),
                "completion_tokens": tokens,
                "call_count": len(calls),
                "finish_reasons": dict(sorted(visit_reasons.items())),
            }
        eligible = [v for v in visits.values() if v["reference_assertions"] >= 3]
        single = sum(v["assertions"] == 1 for v in eligible)
        token_stats = length_stats(known_tokens)
        token_stats["known_total"] = token_stats["total"]
        if len(known_tokens) != len(visits):
            token_stats["total"] = None
        models[identity] = {
            "model_id": metadata["model"]["id"],
            # Do not backfill modern identities into legacy frozen runs.
            "extraction_profile": metadata.get("extraction_profile"),
            "visit_count": len(visits),
            "failed_visits": sum(v["failed"] for v in visits.values()),
            "assertions_per_visit": length_stats([v["assertions"] for v in visits.values()]),
            "single_assertion_reference_ge3": {
                "visits": single,
                "reference_ge3_visits": len(eligible),
                "share": single / len(eligible) if eligible else None,
            },
            "completion_tokens": {
                **token_stats,
                "visits_with_unknown_usage": len(visits) - len(known_tokens),
                "calls_with_usage": known_calls,
                "call_count": total_calls,
            },
            "finish_reasons": dict(sorted(reasons.items())),
            "visits": visits,
        }
    return {
        "schema_version": "1",
        "run_id": document["run_id"],
        "notice": document["notice"],
        "scope": (
            "merged assertions, including failed visits; single-assertion share among reference "
            "visits with >=3 assertions; completion tokens and finish reasons include all "
            "attempts, discarded output and overlapping windows; token statistics describe "
            "visits with complete usage, total is null if any visit's usage is unknown"
        ),
        "models": models,
    }
