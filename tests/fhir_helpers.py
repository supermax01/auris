"""Fictional mapper inputs and independently specified golden outputs."""

import copy
import json
from pathlib import Path

from auris.assertions import parse_assertion
from auris.fhir import Mapper
from auris.transcript import Transcript

GOLDENS = json.loads((Path(__file__).parent / "fixtures/fhir/a3_golden.json").read_text())["cases"]
BY_NAME = {case["name"]: case for case in GOLDENS}


def mapped_case(name, terminology, units, intake, *, changes=None, quote=None, transcript=None):
    case = copy.deepcopy(BY_NAME[name])
    case["assertion"].update(changes or {})
    if quote is not None:
        case["assertion"]["evidence"][0]["quote"] = quote
        case["transcript"]["segments"][0]["text"] = quote
    assertion = parse_assertion(case["assertion"])
    transcript = Transcript.model_validate(transcript or case["transcript"])
    mapper = Mapper(intake, transcript, terminology, units)
    return mapper, assertion, mapper.map_assertion(assertion, 0)
