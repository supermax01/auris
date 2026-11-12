"""Fictional extraction inputs shared by offline and local-model tests."""

import copy
import json
from pathlib import Path

from auris.assertions import parse_assertion
from auris.transcript import Transcript

CASES = json.loads((Path(__file__).parent / "fixtures/contrastive_cases.json").read_text())["cases"]
BY_NAME = {case["name"]: case for case in CASES}


def case_input(name, *, changes=None, quote=None, role=None):
    case = copy.deepcopy(BY_NAME[name])
    data = case["assertions"][0]
    data.update(changes or {})
    if quote is not None:
        data["evidence"] = [{"segment_id": "s1", "quote": quote}]
        case["transcript"]["segments"][0]["text"] = quote
    if role is not None:
        case["transcript"]["segments"][0]["role"] = role
    return Transcript.model_validate(case["transcript"]), parse_assertion(data)


def completion(assertions=(), *, finish_reason="stop", content=None):
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {
                    "role": "assistant",
                    "content": content
                    if content is not None
                    else json.dumps(
                        {"assertions": [a.model_dump(mode="json") for a in assertions]}
                    ),
                },
            }
        ],
        "usage": {"prompt_tokens": 111, "completion_tokens": 222, "total_tokens": 333},
    }
