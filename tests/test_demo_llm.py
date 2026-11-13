"""Real Qwen CLI smoke test on a fictional visit; not an accuracy evaluation."""

import json
import time

import pytest
from demo_helpers import EXPECTED, ROOT, demo_input, demo_leakage

from auris._storage import write_private_text
from auris.cli import main
from auris.config import load_frozen_config
from auris.extract.backend import ExtractionError, find_llama_server, free_port
from auris.extract.prompt import PROMPT_VERSION, prompt_sha256


@pytest.mark.llm
@pytest.mark.allow_hosts(["127.0.0.1"])
def test_real_qwen_demo_smoke(tmp_path, monkeypatch):
    config = load_frozen_config()
    candidate = config.extractor.candidate()
    leakage = demo_leakage()
    transcript, _, _ = demo_input()
    report_path = ROOT / "cache/llm/demo.json"
    report = {
        "fictional": True,
        "test_type": "end_to_end_demo_smoke",
        "interpretation": (
            "One scripted encounter; smoke/regression only, not accuracy. Expected states use "
            "the small offline terminology fixture and fake structural PASS; real terminology "
            "and validator outcomes may differ. Matching below uses evidence segment IDs only."
        ),
        "status": "starting",
        "model_id": candidate.id,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": prompt_sha256(),
        "transcript_sha256": transcript.content_hash,
        **leakage.metadata(),
        "wall_seconds": None,
        "peak_server_rss_bytes": None,
        "rss_scope": "sampled llama-server process RSS; not whole-pipeline/GPU memory",
        "per_assertion": [],
    }

    def save():
        report_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_private_text(report_path, json.dumps(report, indent=2) + "\n")

    def skip(reason):
        report.update(status="skipped", reason=reason)
        save()
        pytest.skip(reason)

    if not leakage.passed:
        report.update(status="failed", reason="prompt/demo leakage; inference refused")
        save()
        leakage.raise_if_failed()
    if not (ROOT / "cache/models" / candidate.gguf_filename).is_file():
        skip("pinned Qwen model missing; run auris setup model --fetch")
    if not (ROOT / "cache/terminology/index.sqlite").is_file():
        skip("terminology index missing; run auris setup terminology --fetch")
    core = config.fhir.package("hl7.fhir.r4.core")
    assert core is not None
    if not (ROOT / "cache/fhir-packages" / core.filename).is_file():
        skip("FHIR unit sets missing; run auris setup fhir")
    try:
        find_llama_server()
    except ExtractionError:
        skip("llama-server missing; configure AURIS_LLAMA_SERVER or PATH")
    try:
        free_port()
    except OSError:
        skip("sandbox blocks localhost bind; Claude must run this llm test outside the sandbox")
    monkeypatch.setenv("AURIS_CACHE", str(ROOT / "cache"))
    # Exercise ./out/demo without overwriting an operator's run.
    monkeypatch.chdir(tmp_path)
    started = time.monotonic()
    try:
        status = main(["demo"])
    except Exception:
        report.update(status="failed", reason="demo raised an unexpected error")
        raise
    finally:
        report["wall_seconds"] = time.monotonic() - started
        save()
    directory = tmp_path / "out/demo"
    extraction_path = directory / "extraction.json"
    extraction = json.loads(extraction_path.read_text()) if extraction_path.is_file() else {}
    report["extraction"] = extraction
    report["peak_server_rss_bytes"] = (
        extraction.get("metadata", {}).get("runtime", {}).get("peak_rss_bytes")
    )
    report["status"] = "complete" if status == 0 else "failed"
    report["exit_code"] = status
    report["reason"] = extraction.get("failure")
    observed = extraction.get("assertions", [])
    mappings = extraction.get("mappings", [])
    matched_positions = set()
    for case in EXPECTED["cases"]:
        expected = case["assertion"]
        segment_ids = {e["segment_id"] for e in expected["evidence"]}
        matches = []
        for position, actual in enumerate(observed):
            if segment_ids.intersection(e["segment_id"] for e in actual["evidence"]):
                matched_positions.add(position)
                matches.append(
                    {
                        "assertion": actual,
                        "mapping": mappings[position] if position < len(mappings) else None,
                        "field_matches": {
                            key: actual.get(key) == value
                            for key, value in expected.items()
                            if key != "evidence"
                        },
                    }
                )
        report["per_assertion"].append(
            {
                "name": case["name"],
                "expected_assertion": expected,
                "expected_outcome": case["expected"],
                "observed": matches,
            }
        )
    report["unmatched_assertions"] = [
        a for i, a in enumerate(observed) if i not in matched_positions
    ]
    validation_path = directory / "validation.json"
    if validation_path.is_file():
        report["validation"] = json.loads(validation_path.read_text())
    save()
    assert status == 0, "demo failed; see cache/llm/demo.json (including truncation, if reported)"
    assert all(
        (directory / name).is_file()
        for name in (
            "extraction.json",
            "validation.json",
            "bundle.json",
            "note.md",
        )
    )
