"""Quote-free Step 11 evidence/consistency/review replay; no inference or Java.

Frozen merged assertions, citation failures and conflicts stay fixed. Reuse
recorded validator lanes only for byte-identical FHIR resources. Never promote
partial/failed visits into predictions or write source transcript/quote text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auris._storage import prepare_output_directory, write_private_text
from auris.annotation.pilot import fictional_intake
from auris.assertions import parse_assertion
from auris.config import load_frozen_config
from auris.evidence import DISFLUENCY_FILLERS_VERSION, EVIDENCE_CHECKER_VERSION, AlignedEvidence
from auris.extract.consultation_replay import _carry_lanes
from auris.fhir import Mapper
from auris.fhir.contracts import load_unit_sets
from auris.fhir.models import MappingOutcome
from auris.terminology import TerminologyIndex
from auris.transcript import Transcript


def _span(outcome: AlignedEvidence) -> dict[str, Any]:
    return outcome.model_dump(exclude={"alignment_method"})


def replay_evidence(
    document: dict[str, Any],
    validation: dict[str, Any],
    before_mapper: Mapper,
    after_mapper: Mapper,
) -> dict[str, Any]:
    """Recheck every frozen item; fail loudly on baseline or exact-span drift."""
    if before_mapper.evidence_checker_version != "evidence-v1" or (
        after_mapper.evidence_checker_version != "evidence-v2"
    ):
        raise ValueError("replay requires evidence-v1 and evidence-v2 mappers")
    if (
        document["metadata"]["transcript_sha256"] != before_mapper.transcript.content_hash
        or before_mapper.transcript != after_mapper.transcript
    ):
        raise ValueError("replay transcript hash mismatch")
    report: dict[str, Any] = {
        "complete": not bool(document.get("failure")),
        "assertions": 0,
        "evidence_items": 0,
        "v1_aligned_items": 0,
        "v1_aligned_span_changes": 0,
        "item_transitions": Counter(),
        "changed_items": Counter(),
        "evidence_lane_transitions": Counter(),
        "export_state_transitions": Counter(),
        "changed_export_states": Counter(),
        "consistency_changes": 0,
        "review_reason_changes": 0,
        "changed_resources_requiring_validation": 0,
        "material_assertions_recovered": 0,
        "remaining_failures": Counter(),
    }
    if not report["complete"]:
        return report
    rows = validation["resources"][3:]
    assertions = tuple(parse_assertion(a) for a in document["assertions"])
    if len(rows) != len(assertions):
        raise ValueError("replay validation rows differ from frozen assertions")
    invalid = {
        row["assertion_position"]: frozenset(row["segment_ids"])
        for row in document.get("citation_failures", [])
    }
    known = frozenset(s.id for s in before_mapper.transcript.segments)
    conflicts = frozenset(document.get("merge_conflict_positions", []))

    def map_one(mapper: Mapper, position: int) -> MappingOutcome:
        outcome = mapper.map_assertion(
            assertions[position], position, allowed_segment_ids=known - invalid.get(position, set())
        )
        if position in conflicts:
            outcome = outcome.model_copy(
                update={
                    "is_material": True,
                    "review_reasons": (*outcome.review_reasons, "merge_conflict"),
                }
            )
        return outcome

    for position, row in enumerate(rows):
        before, after = map_one(before_mapper, position), map_one(after_mapper, position)
        old, new = _carry_lanes(row, before, before), _carry_lanes(row, before, after)
        if (
            old.id != row["id"]
            or old.export_state != row["export_state"]
            or (old.lanes.evidence.value != row["lanes"]["evidence"]["state"])
        ):
            raise ValueError(f"replay baseline differs at assertion {position}")
        report["assertions"] += 1
        report["changed_resources_requiring_validation"] += before.resource != after.resource
        report["consistency_changes"] += before.consistency != after.consistency
        report["review_reason_changes"] += before.review_reasons != after.review_reasons
        report["material_assertions_recovered"] += (
            before.is_material
            and before.lanes.evidence == "FAIL"
            and after.lanes.evidence == "PASS"
        )
        report["evidence_lane_transitions"][f"{before.lanes.evidence}->{after.lanes.evidence}"] += 1
        transition = f"{old.export_state}->{new.export_state}"
        report["export_state_transitions"][transition] += 1
        if old.export_state != new.export_state:
            report["changed_export_states"][transition] += 1
        for first, second in zip(before.evidence.items, after.evidence.items, strict=True):
            report["evidence_items"] += 1
            old_state = first.failure.value if first.failure else first.aligned.alignment_method
            new_state = second.failure.value if second.failure else second.aligned.alignment_method
            report["item_transitions"][f"{old_state}->{new_state}"] += 1
            if old_state != new_state:
                report["changed_items"][f"{old_state}->{new_state}"] += 1
            if first.aligned is not None:
                report["v1_aligned_items"] += 1
                if second.aligned is None or _span(first.aligned) != _span(second.aligned):
                    report["v1_aligned_span_changes"] += 1
            if second.failure:
                report["remaining_failures"][second.failure.value] += 1
    if report["v1_aligned_span_changes"]:
        raise ValueError("an exact-v1 aligned item changed span under v2")
    return report


def aggregate(reports: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, Any] = {"complete_runs": 0, "failed_runs_skipped": 0}
    for report in reports:
        totals["complete_runs" if report["complete"] else "failed_runs_skipped"] += 1
        for key, value in report.items():
            if key == "complete":
                continue
            if isinstance(value, dict):
                totals.setdefault(key, Counter()).update(value)
            else:
                totals[key] = totals.get(key, 0) + value
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, nargs="+")
    parser.add_argument("--cache", type=Path, default=Path("cache"))
    parser.add_argument("--out", type=Path, default=Path("out/step11-replay"))
    args = parser.parse_args()
    snapshot_utc = datetime.now(UTC).isoformat()
    config = load_frozen_config()
    pin = config.fhir.package("hl7.fhir.r4.core")
    assert pin is not None
    units = load_unit_sets(args.cache / "fhir-packages" / pin.filename, pin.sha256)
    reports = {}
    inventory = {}
    with TerminologyIndex(args.cache / "terminology" / "index.sqlite") as terminology:
        for root in args.runs:
            paths = sorted(root.glob("*/*/extraction.json"))
            inventory[root.name] = {
                "frozen_extractions": len(paths),
                "directories_without_extraction": [
                    f"{p.parent.name}/{p.name}"
                    for p in sorted(root.glob("*/*"))
                    if p.is_dir() and not (p / "extraction.json").exists()
                ],
            }
            for path in paths:
                transcript = Transcript.load(
                    args.cache / "datasets" / "prepared" / "primock57" / f"{path.parent.name}.json"
                )
                intake = fictional_intake("primock57", path.parent.name)
                mappers = [
                    Mapper(intake, transcript, terminology, units, evidence_checker_version=version)
                    for version in ("evidence-v1", "evidence-v2")
                ]
                validation_path = path.with_name("validation.json")
                extraction_bytes = path.read_bytes()
                validation_bytes = validation_path.read_bytes()
                report = replay_evidence(
                    json.loads(extraction_bytes), json.loads(validation_bytes), *mappers
                )
                report["source_hashes"] = {
                    "extraction_sha256": hashlib.sha256(extraction_bytes).hexdigest(),
                    "validation_sha256": hashlib.sha256(validation_bytes).hexdigest(),
                    "transcript_sha256": transcript.content_hash,
                }
                reports[f"{root.name}/{path.parent.parent.name}/{path.parent.name}"] = report
    # Hashes are provenance, not counters.
    counts = [
        {k: v for k, v in report.items() if k != "source_hashes"} for report in reports.values()
    ]
    result = {
        "method": "frozen-remap-no-inference-no-remerge-no-java",
        "snapshot_utc": snapshot_utc,
        "baseline_checker": "evidence-v1",
        "checker_version": EVIDENCE_CHECKER_VERSION,
        "disfluency_fillers_version": DISFLUENCY_FILLERS_VERSION,
        "inventory": inventory,
        "runs": reports,
        "totals": aggregate(counts),
    }
    prepare_output_directory(args.out)
    write_private_text(args.out / "evidence-replay.json", json.dumps(result, indent=2) + "\n")
    print(json.dumps({"inventory": inventory, "totals": result["totals"]}, indent=2))


if __name__ == "__main__":
    main()
