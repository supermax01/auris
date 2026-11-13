"""Within-model profile comparisons, separate from A5 larger-model selection."""

from collections import Counter, defaultdict
from typing import Any


def profile_measurements(metadata: dict[str, Any]) -> dict[str, Any]:
    rows = list(metadata["measurements_by_encounter"].values())
    assertions = sum(r.get("assertion_count", 0) for r in rows)
    known = bool(rows) and all(r.get("output_tokens") is not None for r in rows)
    tokens = sum(r["output_tokens"] for r in rows) if known else None
    quote_count = sum(r.get("quote_characters", {}).get("count", 0) for r in rows)
    quote_chars = sum(r.get("quote_characters", {}).get("total", 0) for r in rows)
    reasons: Counter[str] = Counter()
    for row in rows:
        reasons.update(row.get("evidence_failures_by_reason", {}))
    failed = sum(r.get("evidence_failed_assertions", 0) for r in rows)
    return {
        "output_tokens": tokens,
        "assertion_count": assertions,
        "output_tokens_per_assertion": tokens / assertions
        if tokens is not None and assertions
        else None,
        "mean_quote_characters": quote_chars / quote_count if quote_count else None,
        "evidence_failed_assertions": failed,
        "evidence_failure_rate": failed / assertions if assertions else None,
        "evidence_failures_by_reason": dict(sorted(reasons.items())),
    }


def compare_profiles(models: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[str, dict[str, str]] = defaultdict(dict)
    for identity, model in models.items():
        metadata = model["metadata"]
        if "extraction_profile" in metadata:
            grouped[metadata["model"]["id"]][metadata["extraction_profile"]] = identity
    comparisons = {}
    for model_id, profiles in grouped.items():
        if not {"full-v0", "lean-v1"} <= profiles.keys():
            continue
        baseline, candidate = (models[profiles[p]] for p in ("full-v0", "lean-v1"))
        full, lean = (profile_measurements(m["metadata"]) for m in (baseline, candidate))
        before, after = full["output_tokens_per_assertion"], lean["output_tokens_per_assertion"]
        saving = 1 - after / before if before and after is not None else None
        delta = candidate["lenient"]["micro"]["f1"] - baseline["lenient"]["micro"]["f1"]
        no_increase = all(
            candidate["critical_errors"][stage][reason]["count"] <= result["count"]
            for stage, reasons in baseline["critical_errors"].items()
            for reason, result in reasons.items()
        )
        comparisons[model_id] = {
            "baseline": profiles["full-v0"],
            "candidate": profiles["lean-v1"],
            "full-v0": full,
            "lean-v1": lean,
            "output_tokens_per_assertion_saving_fraction": saving,
            "token_target_met": saving >= 0.35 if saving is not None else None,
            "delta_micro_lenient_f1": delta,
            "f1_target_met": delta >= -0.02 - 1e-12,
            "no_critical_error_increase": no_increase,
            "evidence_failure_rate_delta": (
                lean["evidence_failure_rate"] - full["evidence_failure_rate"]
                if all(m["evidence_failure_rate"] is not None for m in (full, lean))
                else None
            ),
            "evidence_target": (
                "inspect reason counts and rates for material rise; no automatic threshold"
            ),
        }
    return comparisons
