"""Fictional extraction inputs shared by offline and local-model tests."""

import copy
import json
from pathlib import Path

from auris.assertions import parse_assertion
from auris.transcript import Transcript

CASES = json.loads((Path(__file__).parent / "fixtures/contrastive_cases.json").read_text())["cases"]
BY_NAME = {case["name"]: case for case in CASES}


def fixture_transcript_texts(root: Path | None = None) -> tuple[str, ...]:
    """Read every transcript segment in JSON fixtures, including nested goldens.

    Discover files recursively so adding a new fixture also extends the leakage
    guard. Validate transcript objects instead of silently ignoring malformed
    segment text. No expected output, FHIR narrative or terminology is a turn.
    """
    texts: list[str] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if "segments" in value:
                transcript = Transcript.model_validate(value)
                texts.extend(segment.text for segment in transcript.segments)
            else:
                for child in value.values():
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for path in sorted((root or Path(__file__).parent / "fixtures").rglob("*.json")):
        visit(json.loads(path.read_text("utf-8")))
    if not texts:
        raise ValueError("no fixture transcript texts found")
    return tuple(texts)


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


def sse_response(response):
    """Synthetic pinned-server framing; accepts only authored fictional fixtures."""
    choice = response["choices"][0]
    events = [
        {
            "choices": [
                {"index": 0, "delta": {"role": "assistant", "content": None}, "finish_reason": None}
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": choice["message"]["content"]},
                    "finish_reason": None,
                }
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}]},
        {"choices": [], **{k: response[k] for k in ("usage", "timings") if k in response}},
    ]
    return (
        "".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n"
    ).encode()
