"""Exact fictional note and truthful labels, receipts and review visibility."""

import re

import pytest
from demo_helpers import EXPECTED, FIXTURES, FakeValidator, demo_input

from auris.fhir import Mapper
from auris.fhir.models import LaneState, resource_digest
from auris.render import NoteMetadata, note_items, render_note
from auris.run import export_decision
from auris.validation.report import apply_validation, validate_mappings


@pytest.fixture
def demo_outcomes(terminology, units):
    transcript, intake, assertions = demo_input()
    outcomes, validation = validate_mappings(
        FakeValidator(), intake, Mapper(intake, transcript, terminology, units).map_all(assertions)
    )
    return transcript, intake, outcomes, validation


def metadata():
    return NoteMetadata(
        model_id="qwen3-8b-q4_k_m",
        prompt_version="extraction_v2",
        run_timestamp=EXPECTED["run_timestamp"],
        fictional_demo=True,
    )


def test_demo_outcomes_and_exact_note(demo_outcomes):
    transcript, intake, outcomes, validation = demo_outcomes
    for case, outcome in zip(EXPECTED["cases"], outcomes, strict=True):
        expected = case["expected"]
        assert outcome.export_state == expected["export_state"], case["name"]
        assert outcome.review_reasons == tuple(expected["review_reasons"]), case["name"]
        assert (outcome.resource["resourceType"] if outcome.resource else None) == expected[
            "resource_type"
        ]
        assert (outcome.ledger is not None) == expected["in_ledger"]
    items = note_items(outcomes, transcript, validation)
    summary = export_decision(intake, outcomes, validation).summary
    note = render_note(items, metadata(), export_summary=summary)
    assert note == (FIXTURES / "note.md").read_text()
    assert note == render_note(items, metadata(), export_summary=summary)
    assert "validated" not in note.replace("not validated", "")
    for line in note.splitlines():
        if line.startswith("- "):
            assert re.search(r"\[\d+:\d{2} · (patient|clinician)\]", line)
            assert "transcript-supported · " in line and "coding not checked" in line
    review = note.split("## Needs review — not validated\n", 1)[1].split("## Discussed", 1)[0]
    ledger = note.split("## Discussed, not captured as records\n", 1)[1]
    for item in items:
        if item.export_state != "final_exportable":
            assert item.line(reasons=True) in review
            assert item.reasons
        assert (item.line() in ledger) == item.in_ledger
    assert "drug family, kept as text · needs coding review" in note
    assert "Family history (not the patient's condition)" in note
    assert "Blood pressure 126/78" in note and "Heart rate 84" in note
    assert "name not captured" not in note


def test_empty_sections_are_explicit():
    note = render_note((), metadata(), export_summary="No extracted records exported.")
    assert note.count("\nnot captured\n") == 6
    assert "FICTIONAL DEMO" in note and "clinician review mandatory" in note


def test_word_time_and_actual_speaker_are_used(terminology, units):
    from extract_helpers import case_input

    from auris.transcript import Transcript

    transcript, assertion = case_input(
        "vital_sign",
        quote="Heart rate 84.",
        changes={
            "concept_text": "heart rate",
            "value_text": "84",
        },
    )
    data = transcript.model_dump(mode="json")
    data["segments"][0].update(
        start_seconds=60,
        end_seconds=80,
        words=[
            {"word": word, "start_seconds": start, "end_seconds": start + 1}
            for word, start in (("Heart", 65), ("rate", 66), ("84.", 67))
        ],
    )
    transcript = Transcript.model_validate(data)
    intake = demo_input()[1]
    outcomes, validation = validate_mappings(
        FakeValidator(),
        intake,
        Mapper(intake, transcript, terminology, units).map_all((assertion,)),
    )
    assert note_items(outcomes, transcript, validation)[0].receipts == "[01:05 · clinician]"


def test_failed_evidence_is_never_presented_as_supported(demo_outcomes):
    transcript, _, outcomes, validation = demo_outcomes
    original = outcomes[7]
    failed = original.model_copy(
        update={
            "lanes": original.lanes.model_copy(update={"evidence": LaneState.FAIL}),
            "review_reasons": ("consistency_failed",),
        }
    )
    item = note_items((failed,), transcript, validation)[0]
    assert item.export_state == "validation_failed"
    assert item.fact.startswith("Unconfirmed extraction")
    assert "transcript-supported" not in item.label
    assert "checks failed" in item.label


def test_affirmative_validated_requires_every_lane_and_export_gate(demo_outcomes):
    transcript, _, outcomes, validation = demo_outcomes
    outcome = outcomes[7]
    results = tuple(
        result.model_copy(
            update={"terminology": result.terminology.model_copy(update={"state": LaneState.PASS})}
        )
        for result in validation.resources
    )
    tier_one = validation.model_copy(update={"resources": results})
    checked = apply_validation(outcome, tier_one)
    assert " · validated" in note_items((checked,), transcript, tier_one)[0].label
    material = checked.model_copy(update={"is_material": True})
    assert " · validated" not in note_items((material,), transcript, tier_one)[0].label
    failed = checked.model_copy(
        update={"lanes": checked.lanes.model_copy(update={"evidence": LaneState.FAIL})}
    )
    assert " · validated" not in note_items((failed,), transcript, tier_one)[0].label


def test_markdown_and_newlines_in_quotes_are_inert(demo_outcomes):
    transcript, _, outcomes, validation = demo_outcomes
    outcome = outcomes[0]
    evidence = outcome.evidence.model_copy(
        update={
            "items": (
                outcome.evidence.items[0].model_copy(
                    update={"aligned": None, "quote": "\n# forged\n<script>x</script> [link](bad)"}
                ),
            )
        }
    )
    item = note_items((outcome.model_copy(update={"evidence": evidence}),), transcript, validation)[
        0
    ]
    assert "\n" not in item.fact and "<script>" not in item.fact
    assert r"\# forged" in item.fact and r"\[link\]" in item.fact


def test_structural_failure_reason_is_visible(demo_outcomes):
    from auris.validation.models import LaneResult, ValidatorIssue

    transcript, _, outcomes, validation = demo_outcomes
    target = outcomes[7]
    failure = validation.for_resource(target.resource).model_copy(
        update={
            "base_r4": LaneResult(
                state=LaneState.FAIL,
                issues=(
                    ValidatorIssue(
                        severity="error",
                        code="required",
                        details={"text": "A required field is absent"},
                    ),
                ),
            )
        }
    )
    failed_run = validation.model_copy(update={"resources": (failure,)})
    failed = apply_validation(target, failed_run)
    item = note_items((failed,), transcript, failed_run)[0]
    assert item.export_state == "validation_failed"
    assert "A required field is absent" in item.reasons
    assert failed.validation_digest == resource_digest(target.resource)
