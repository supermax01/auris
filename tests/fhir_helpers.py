"""Fictional mapper inputs and independently specified golden outputs."""

import copy
import json
from pathlib import Path

from auris.assertions import parse_assertion
from auris.fhir import Mapper
from auris.transcript import Transcript

GOLDENS = json.loads((Path(__file__).parent / "fixtures/fhir/a3_golden.json").read_text())["cases"]
BY_NAME = {case["name"]: case for case in GOLDENS}


def _plan_item_case() -> dict:
    """Schema 3 only, so not an A3 golden: a clinician's plan step, never a record."""
    case = copy.deepcopy(BY_NAME["new-order"])
    case["name"] = "plan-item"
    case["a3_row"] = "plan_item"
    order = case["assertion"]
    case["assertion"] = {
        "kind": "plan_item",
        "concept_text": "chest x-ray",
        "polarity": "affirmed",
        "experiencer": order["experiencer"],
        "temporality": "future",
        "source_speaker_role": order["source_speaker_role"],
        "evidence": [{**order["evidence"][0], "quote": "We will get a chest x-ray."}],
    }
    case["transcript"]["segments"][0]["text"] = "We will get a chest x-ray."
    case.pop("expected", None)
    return case


BY_NAME["plan-item"] = _plan_item_case()


def _social_history_case() -> dict:
    """Schema 4 only: social history, never a record."""
    case = copy.deepcopy(BY_NAME["symptom-affirmed"])
    case["name"] = "social-history"
    case["a3_row"] = "social_history"
    case["assertion"] = {
        **case["assertion"],
        "kind": "social_history",
        "concept_text": "smoke",
        "polarity": "negated",
        "evidence": [{**case["assertion"]["evidence"][0], "quote": "I do not smoke."}],
    }
    case["transcript"]["segments"][0]["text"] = "I do not smoke."
    case.pop("expected", None)
    return case


BY_NAME["social-history"] = _social_history_case()


def mapped_case(name, terminology, units, intake, *, changes=None, quote=None, transcript=None):
    case = copy.deepcopy(BY_NAME[name])
    case["assertion"].update(changes or {})
    if quote is not None:
        case["assertion"]["evidence"][0]["quote"] = quote
        case["transcript"]["segments"][0]["text"] = quote
    assertion = parse_assertion(case["assertion"], schema_version="4")
    transcript = Transcript.model_validate(transcript or case["transcript"])
    mapper = Mapper(intake, transcript, terminology, units)
    return mapper, assertion, mapper.map_assertion(assertion, 0)


def approved(outcome):
    """A clinician's approval of this exact version."""
    from auris.fhir.models import resource_digest

    return outcome.model_copy(
        update={"reviewed_resource_digest": resource_digest(outcome.resource)}
    )
