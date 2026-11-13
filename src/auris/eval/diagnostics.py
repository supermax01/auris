"""Per-item error analysis from frozen predictions and verified stand-off gold."""

import html
import json
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from auris.eval.critical import findings
from auris.eval.gold import Encounter, Fact, GoldAssertion
from auris.eval.matching import (
    RESOURCE_CLASSES,
    concept_equal,
    eligible,
    match,
    overlap,
    strict_reasons,
)
from auris.eval.metrics import SCORE_VIEWS
from auris.eval.normalization import normalize_dose, vital_unit, vital_value
from auris.eval.predictions import Prediction, record_quantities
from auris.transcript import Transcript


def describe(fact: Fact, transcript: Transcript, gold: Sequence[GoldAssertion]) -> dict[str, Any]:
    data = fact.model_dump(mode="json")
    for span, evidence in zip(fact.evidence, data["evidence"], strict=True):
        span.verify(transcript)
        segment = transcript.segment_by_id(span.segment_id)
        assert segment is not None
        evidence["quote"] = segment.text[span.char_start : span.char_end]
    data["normalized_dose"] = normalize_dose(fact.dose_text)
    if isinstance(fact, Prediction):
        # A gold concept association is explicitly labelled, never invented as
        # an extractor-produced concept ID or acceptable-text annotation.
        data["associated_gold_concept_ids"] = [
            g.gold_concept_id for g in gold if concept_equal(fact, g)
        ]
        data["acceptable_texts"] = None
        data["assertion_canonical_value"] = vital_value(fact.value_text)
        data["assertion_ucum_unit"] = vital_unit(fact.unit_text)
        data["record_quantities"] = record_quantities(fact.resource)
    return data


def _pair_reasons(prediction: Prediction, gold: GoldAssertion, view: str) -> list[str]:
    reasons = []
    if RESOURCE_CLASSES[prediction.kind] != RESOURCE_CLASSES[gold.kind]:
        reasons.append(f"resource class mismatch {prediction.kind!r} vs {gold.kind!r}")
    for field in ("polarity", "experiencer"):
        if getattr(prediction, field) != getattr(gold, field):
            reasons.append(
                f"{field} mismatch {getattr(prediction, field)!r} vs {getattr(gold, field)!r}"
            )
    if not concept_equal(prediction, gold):
        reasons.append(
            f"concept {prediction.allergen_text or prediction.concept_text!r} "
            f"is not adjudicated equivalent to {gold.gold_concept_id!r}"
        )
    if view != "lenient":
        reasons.extend(strict_reasons(prediction, gold, record=view == "record_strict"))
    return reasons


def _related(prediction: Prediction, gold: GoldAssertion) -> bool:
    return (
        concept_equal(prediction, gold)
        or overlap(prediction.evidence, gold.evidence)
        or bool(set(prediction.cited_segment_ids) & {span.segment_id for span in gold.evidence})
    )


def encounter_diagnostics(
    encounter: Encounter, predictions: Sequence[Prediction], transcript: Transcript
) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def add(
        error_type: str,
        view: str,
        gold: Sequence[GoldAssertion],
        prediction: Prediction | None,
        reasons: list[str],
    ) -> None:
        groups[error_type].append(
            {
                "encounter_id": encounter.id,
                "view": view,
                "error_type": error_type,
                "reasons": reasons,
                "gold": [describe(g, transcript, encounter.assertions) for g in gold],
                "prediction": describe(prediction, transcript, encounter.assertions)
                if prediction
                else None,
            }
        )

    for prediction in predictions:
        for category, finding in findings(prediction, encounter.assertions).items():
            stages = ["pre_review"]
            if prediction.export_state == "final_exportable" and not prediction.reviewed:
                stages.append("exported_without_review")
            for stage in stages:
                add(
                    category,
                    stage,
                    finding.gold,
                    prediction,
                    [f"critical: {category} — {finding.reason}"],
                )

    for view in SCORE_VIEWS:
        pairs = match(
            predictions,
            encounter.assertions,
            strict=view != "lenient",
            record=view == "record_strict",
        )
        matched_predictions, matched_gold = {i for i, _ in pairs}, {j for _, j in pairs}
        for j, gold in enumerate(encounter.assertions):
            if j in matched_gold:
                continue
            candidates = [p for p in predictions if _related(p, gold)]
            # Show the closest related candidate and its exact failures; this
            # diagnostic association does not replace the one-to-one assignment.
            closest = min(candidates, key=lambda p: len(_pair_reasons(p, gold, view)), default=None)
            reasons = _pair_reasons(closest, gold, view) if closest else []
            add(
                "false_negative",
                view,
                [gold],
                closest,
                reasons
                or [
                    "unmatched gold: eligible prediction consumed by another gold assertion"
                    if closest
                    and eligible(
                        closest, gold, strict=view != "lenient", record=view == "record_strict"
                    )
                    else "unmatched gold: no related prediction in this encounter"
                ],
            )
        for i, prediction in enumerate(predictions):
            if i in matched_predictions:
                continue
            candidates = [g for g in encounter.assertions if _related(prediction, g)]
            closest = min(
                candidates, key=lambda g: len(_pair_reasons(prediction, g, view)), default=None
            )
            reasons = _pair_reasons(prediction, closest, view) if closest else []
            add(
                "false_positive",
                view,
                [closest] if closest else [],
                prediction,
                reasons
                or [
                    "unmatched prediction: eligible gold consumed by another prediction"
                    if closest
                    else "unmatched prediction: no related gold assertion in this encounter"
                ],
            )
    for i, j in match(predictions, encounter.assertions):
        for view in SCORE_VIEWS[1:]:
            reasons = strict_reasons(
                predictions[i], encounter.assertions[j], record=view == "record_strict"
            )
            if reasons:
                add(
                    "lenient_match_strict_fail",
                    view,
                    [encounter.assertions[j]],
                    predictions[i],
                    [f"strict: {reason}" for reason in reasons],
                )
    return dict(sorted(groups.items()))


def _cell(value: Any) -> str:
    text = (
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    )
    return html.escape(text).replace("|", "&#124;").replace("\n", "<br>").replace("`", "&#96;")


def render_diagnostics(groups: dict[str, list[dict[str, Any]]]) -> list[str]:
    lines = []
    for error_type, records in sorted(groups.items()):
        lines += [f"#### {error_type}", ""]
        for record in records:
            lines += [
                f"**{_cell(record['encounter_id'])} · {_cell(record['view'])}**",
                "",
                *[f"- {_cell(reason)}" for reason in record["reasons"]],
                "",
                "| Field | Gold assertion(s) | Prediction |",
                "|---|---|---|",
            ]
            gold, prediction = record["gold"], record["prediction"]
            fields = (
                "id",
                "kind",
                "concept_text",
                "gold_concept_id",
                "associated_gold_concept_ids",
                "acceptable_texts",
                "polarity",
                "experiencer",
                "temporality",
                "source_speaker_role",
                "value_text",
                "unit_text",
                "canonical_value",
                "ucum_unit",
                "assertion_canonical_value",
                "assertion_ucum_unit",
                "record_quantities",
                "dose_text",
                "normalized_dose",
                "route_text",
                "frequency_text",
                "medication_intent",
                "allergen_text",
                "reaction_manifestation_text",
                "stated_time_text",
                "stated_resolution_text",
                "codes",
                "evidence",
                "quoted_evidence",
                "export_state",
                "reviewed",
                "review_reasons",
                "lanes",
            )
            for field in fields:
                left = [g.get(field) for g in gold]
                right = prediction.get(field) if prediction else None
                if right in (None, [], "") and all(v in (None, [], "") for v in left):
                    continue
                # Evidence quotes retain their segment and exact character range.
                if field == "evidence":

                    def quotes(items: list[dict]) -> list[str]:
                        return [
                            f"{e['segment_id']}[{e['char_start']}:{e['char_end']}]: {e['quote']}"
                            for e in items
                        ]

                    left = [quotes(g["evidence"]) for g in gold]
                    right = quotes(prediction["evidence"]) if prediction else None
                lines.append(
                    f"| {field} | {_cell(left) if gold else '—'} | "
                    f"{_cell(right) if prediction else '—'} |"
                )
            lines.append("")
    return lines
