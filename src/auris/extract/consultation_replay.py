"""Read-only Step 9 replay of frozen dev extractions. Never runs inference or Java.

Only counts, ids and states reach stdout. Source transcript/quote text stays in
the private cache. Recorded validator lanes are reused only for unchanged FHIR
resources; this estimates review eligibility, never clinical correctness.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from auris.annotation.pilot import fictional_intake
from auris.assertions import AssertionBase, parse_assertion
from auris.config import load_frozen_config
from auris.extract.citations import classify_citation, resolve_citations
from auris.fhir import Mapper
from auris.fhir.contracts import load_unit_sets
from auris.fhir.models import LaneState, MappingOutcome, resource_digest
from auris.reason_codes import CheckReason
from auris.terminology import TerminologyIndex
from auris.transcript import Transcript


def _key(assertion: AssertionBase) -> str:
    return json.dumps(assertion.model_dump(mode="json"), sort_keys=True)


def _carry_lanes(
    row: dict[str, Any], before: MappingOutcome, after: MappingOutcome
) -> MappingOutcome:
    if before.resource != after.resource:
        return after  # A changed resource needs a fresh validator run.
    lanes = after.lanes.model_copy(
        update={
            name: LaneState(row["lanes"][name]["state"])
            for name in ("base_r4", "us_core_profile", "terminology")
        }
    )
    return after.model_copy(
        update={
            "lanes": lanes,
            "validation_digest": resource_digest(after.resource) if after.resource else None,
        }
    )


def replay_consultation(
    document: dict[str, Any], validation: dict[str, Any], mapper: Mapper
) -> dict[str, Any]:
    """Count every bad citation and remap each frozen, merged assertion in place.

    Do not remerge after alias repair: positions remain paired with the original
    validation rows. This avoids counting deduplication as improved recall.
    """
    # This historical counterfactual measures Step 9 only. Step 11 has its own
    # v1/v2 replay; do not silently include its disfluency recoveries here.
    mapper = Mapper(
        mapper.intake,
        mapper.transcript,
        mapper.terminology,
        mapper.unit_sets,
        evidence_checker_version="evidence-v1",
    )
    if document["metadata"]["transcript_sha256"] != mapper.transcript.content_hash:
        raise ValueError("replay transcript hash mismatch")
    categories: Counter[str] = Counter(
        {
            category: 0
            for category in (
                "adjacent_or_overlap",
                "earlier_window",
                "malformed_or_nonexistent",
                "id_format_confusion",
            )
        }
    )
    resolutions: dict[str, dict[str, tuple[AssertionBase, frozenset[str]]]] = defaultdict(dict)
    earlier: set[str] = set()
    known = frozenset(s.id for s in mapper.transcript.segments)
    for call in document["calls"]:
        segments = tuple(mapper.transcript.segment_by_id(id_) for id_ in call["segment_ids"])
        if any(s is None for s in segments):
            raise ValueError("replay window has unknown segments")
        allowed = frozenset(call["segment_ids"])
        for raw in call.get("parsed_assertions", []):
            assertion = parse_assertion(raw)
            for item in assertion.evidence:
                category = classify_citation(
                    item.segment_id, segments, mapper.transcript, frozenset(earlier)
                )
                if category != "in_window":
                    categories[category] += 1
            (resolved,), _ = resolve_citations((assertion,), segments, known_segment_ids=known)
            invalid = frozenset(
                e.segment_id for e in resolved.evidence if e.segment_id not in allowed
            )
            resolutions[_key(assertion)][_key(resolved)] = resolved, invalid
        earlier.update(allowed)
    report: dict[str, Any] = {
        "citation_categories": dict(categories),
        "complete": not bool(document.get("failure")),
        "concept_cases": 0,
        "concept_evidence_to_pass": 0,
        "concept_state_changes": Counter(),
        "citation_cases": 0,
        "citation_ids_resolved": 0,
        "citation_evidence_to_pass": 0,
        "citation_state_changes": Counter(),
        "combined_state_changes": Counter(),
        "citation_combined_evidence_to_pass": 0,
        "citation_combined_state_changes": Counter(),
        "citation_after_evidence_failures": Counter(),
    }
    if document.get("failure"):
        return report  # Partial parsed calls remain diagnostics, never predictions.
    rows = validation["resources"][3:]  # Three supplied intake anchors.
    assertions = tuple(parse_assertion(a) for a in document["assertions"])
    if len(rows) != len(assertions):
        raise ValueError("replay validation rows differ from frozen assertions")
    invalid_by_position = {
        row["assertion_position"]: frozenset(row["segment_ids"])
        for row in document.get("citation_failures", [])
    }
    conflicts = frozenset(document.get("merge_conflict_positions", []))

    def map_one(assertion: AssertionBase, position: int, invalid: frozenset[str]) -> MappingOutcome:
        outcome = mapper.map_assertion(assertion, position, allowed_segment_ids=known - invalid)
        if position in conflicts:
            outcome = outcome.model_copy(
                update={
                    "is_material": True,
                    "review_reasons": (*outcome.review_reasons, CheckReason.MERGE_CONFLICT),
                }
            )
        return outcome

    for position, (assertion, row) in enumerate(zip(assertions, rows, strict=True)):
        invalid = invalid_by_position.get(position, frozenset())
        before = map_one(assertion, position, invalid)
        concept = _carry_lanes(row, before, before)
        baseline = concept
        if CheckReason.CONCEPT_TEXT_NOT_IN_QUOTE in before.consistency.review_reasons:
            baseline = concept.model_copy(
                update={
                    "lanes": concept.lanes.model_copy(update={"evidence": LaneState.FAIL}),
                }
            )
        if before.id != row["id"] or baseline.export_state != row["export_state"]:
            raise ValueError(f"replay baseline differs at assertion {position}")
        has_concept_failure = any(
            issue.get("details", {}).get("text") == CheckReason.CONCEPT_TEXT_NOT_IN_QUOTE
            for issue in row["lanes"]["evidence"]["issues"]
        )
        transition = f"{row['export_state']}->{concept.export_state}"
        if has_concept_failure:
            report["concept_cases"] += 1
            report["concept_evidence_to_pass"] += concept.lanes.evidence == LaneState.PASS
            report["concept_state_changes"][transition] += 1
        resolved, remaining = assertion, invalid
        candidates = resolutions.get(_key(assertion), {})
        if invalid and len(candidates) == 1:
            resolved, remaining = next(iter(candidates.values()))
        after = map_one(resolved, position, remaining)
        combined = _carry_lanes(row, before, after)
        # Isolate citation repair by restoring the old hard concept rule.
        citation = combined
        if CheckReason.CONCEPT_TEXT_NOT_IN_QUOTE in after.consistency.review_reasons:
            citation = combined.model_copy(
                update={"lanes": combined.lanes.model_copy(update={"evidence": LaneState.FAIL})}
            )
        if invalid:
            report["citation_cases"] += 1
            report["citation_ids_resolved"] += sum(
                a.segment_id != b.segment_id
                for a, b in zip(assertion.evidence, resolved.evidence, strict=True)
            )
            report["citation_evidence_to_pass"] += citation.lanes.evidence == LaneState.PASS
            report["citation_combined_evidence_to_pass"] += (
                combined.lanes.evidence == LaneState.PASS
            )
            report["citation_combined_state_changes"][
                f"{row['export_state']}->{combined.export_state}"
            ] += 1
            report["citation_state_changes"][f"{row['export_state']}->{citation.export_state}"] += 1
            report["citation_after_evidence_failures"].update(after.evidence.failures)
            report["citation_after_evidence_failures"].update(after.consistency.failures)
            if CheckReason.CONCEPT_TEXT_NOT_IN_QUOTE in after.consistency.review_reasons:
                report["citation_after_evidence_failures"][
                    CheckReason.CONCEPT_TEXT_NOT_IN_QUOTE
                ] += 1
        report["combined_state_changes"][f"{row['export_state']}->{combined.export_state}"] += 1
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path)
    parser.add_argument("--cache", type=Path, default=Path("cache"))
    args = parser.parse_args()
    config = load_frozen_config()
    pin = config.fhir.package("hl7.fhir.r4.core")
    assert pin is not None
    units = load_unit_sets(args.cache / "fhir-packages" / pin.filename, pin.sha256)
    reports = {}
    with TerminologyIndex(args.cache / "terminology" / "index.sqlite") as terminology:
        for path in sorted(args.runs.glob("*/*/extraction.json")):
            transcript = Transcript.load(
                args.cache / "datasets" / "prepared" / "primock57" / f"{path.parent.name}.json"
            )
            mapper = Mapper(
                fictional_intake("primock57", path.parent.name), transcript, terminology, units
            )
            report = replay_consultation(
                json.loads(path.read_text()),
                json.loads(path.with_name("validation.json").read_text()),
                mapper,
            )
            reports[f"{path.parent.parent.name}/{path.parent.name}"] = report
    totals: dict[str, Any] = {}
    for report in reports.values():
        for key, value in report.items():
            if key == "complete":
                continue
            if isinstance(value, dict):
                totals.setdefault(key, Counter()).update(value)
            else:
                totals[key] = totals.get(key, 0) + value
    print(
        json.dumps(
            {"method": "frozen-remap-no-inference-no-remerge", "runs": reports, "totals": totals},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
