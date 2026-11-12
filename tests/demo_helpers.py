"""Fictional demo replay; structural PASS here is a fake, not validator evidence."""

import json
from contextlib import nullcontext
from pathlib import Path

from extract_helpers import completion

from auris.assertions import extraction_json_schema, parse_assertion
from auris.extract.backend import FakeBackend
from auris.extract.chunking import plan_chunks
from auris.extract.leakage import check_leakage
from auris.extract.pipeline import extract
from auris.extract.prompt import prompt_text
from auris.intake import load_intake
from auris.transcript import Transcript
from auris.validation.models import ValidationRun
from auris.validation.outcomes import classify

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/demo"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text())


def demo_input():
    return (
        Transcript.load(ROOT / "examples/transcript.json"),
        load_intake(ROOT / "examples/intake.json"),
        tuple(parse_assertion(case["assertion"]) for case in EXPECTED["cases"]),
    )


def demo_leakage():
    transcript, _, _ = demo_input()
    return check_leakage(
        prompt_text(),
        tuple(s.text for s in transcript.segments),
        clinical_terms=EXPECTED["clinical_terms"],
    )


def replay(transcript, config, candidate, assertions):
    # Exercise real window planning and merging with the conservative tokenizer.
    chunks = plan_chunks(
        transcript,
        extraction_json_schema(),
        FakeBackend([]),
        n_ctx=config.n_ctx,
        max_tokens=config.max_tokens,
    )
    responses = []
    for chunk in chunks:
        ids = {s.id for s in chunk.segments}
        responses.append(
            completion([a for a in assertions if all(e.segment_id in ids for e in a.evidence)])
        )
    run = extract(FakeBackend(responses), transcript, config, candidate)
    run.started_at = EXPECTED["run_timestamp"]
    return run


class FakeValidator:
    def __init__(self, *, failure=None):
        self.failure = failure

    def validate(self, resources):
        return ValidationRun(
            resources=tuple(classify(r, (), (), failure=self.failure) for r in resources),
            wall_seconds=0,
            failure=self.failure,
        )


def patch_cli(monkeypatch, terminology, units, assertions=None, *, validator=None):
    from auris import terminology as terminology_module
    from auris.extract import pipeline
    from auris.fhir import contracts
    from auris.validation import runner

    if assertions is None:
        assertions = demo_input()[2]
    monkeypatch.setattr(
        pipeline,
        "extract_local",
        lambda transcript, config, candidate, path: replay(
            transcript, config, candidate, assertions
        ),
    )
    monkeypatch.setattr(contracts, "load_unit_sets", lambda *args: units)
    monkeypatch.setattr(
        terminology_module, "TerminologyIndex", lambda path: nullcontext(terminology)
    )
    monkeypatch.setattr(
        runner, "ValidatorRunner", lambda *args, **kwargs: validator or FakeValidator()
    )
