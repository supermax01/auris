"""Offline CLI regression on the fictional demo and positive export controls."""

import json

import pytest
from demo_helpers import FakeValidator, demo_input, demo_leakage, patch_cli

from auris.cli import main


def documents(directory):
    return tuple(
        json.loads((directory / name).read_text())
        for name in (
            "extraction.json",
            "validation.json",
            "bundle.json",
        )
    )


def test_demo_leakage_and_a3_coverage():
    from auris.assertions import AssertionKind

    transcript, _, assertions = demo_input()
    demo_leakage().raise_if_failed()
    assert {a.kind for a in assertions} == set(AssertionKind)
    assert 180 <= transcript.segments[-1].end_seconds <= 300
    assert "fictional" in transcript.segments[0].text


def test_cli_demo_writes_all_four_files_and_shared_states(
    monkeypatch, terminology, units, tmp_path, capsys
):
    patch_cli(monkeypatch, terminology, units)
    monkeypatch.chdir(tmp_path)
    assert main(["demo"]) == 0
    directory = tmp_path / "out/demo"
    assert {p.name for p in directory.iterdir()} == {
        "extraction.json",
        "validation.json",
        "bundle.json",
        "note.md",
    }
    assert directory.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in directory.iterdir())
    extraction, report, bundle = documents(directory)
    note = (directory / "note.md").read_text()
    assert len(extraction["mappings"]) == 13
    assert report["export"]["blocking_ids"]
    assert report["export"]["exported_ids"] == []
    assert bundle["type"] == "collection"
    assert [e["resource"]["resourceType"] for e in bundle["entry"]] == [
        "Patient",
        "Practitioner",
        "Encounter",
    ]
    assert "intake anchors only" in note and "intake anchors only" in capsys.readouterr().out
    assert "FICTIONAL DEMO" in note
    for mapping, row in zip(extraction["mappings"], report["resources"][3:], strict=True):
        assert row["id"] == mapping["id"]
        assert row["export_state"] == mapping["export_state"] == row["note"]["export_state"]
        rendered = row["note"]
        assert f"{rendered['fact']} {rendered['receipts']} — {rendered['label']}" in note
    assert "validated" not in note.replace("not validated", "")


@pytest.mark.parametrize("mode", ["pass", "unavailable", "nonmaterial-failure", "anchor-failure"])
def test_run_export_gates_are_nonvacuous(mode, monkeypatch, terminology, units, tmp_path, capsys):
    from demo_helpers import ROOT

    from auris.fhir.models import LaneState
    from auris.validation.models import LaneResult

    assertions = tuple(a for a in demo_input()[2] if a.kind == "vital_sign")

    class Validator(FakeValidator):
        def validate(self, resources):
            run = super().validate(resources)
            target = "Observation" if mode == "nonmaterial-failure" else "Patient"
            if mode in ("nonmaterial-failure", "anchor-failure"):
                results = list(run.resources)
                index = next(i for i, r in enumerate(results) if r.resource_type == target)
                results[index] = results[index].model_copy(
                    update={"base_r4": LaneResult(state=LaneState.FAIL)}
                )
                return run.model_copy(update={"resources": tuple(results)})
            return run

    validator = Validator(
        failure="fictional checker unavailable" if mode == "unavailable" else None
    )
    patch_cli(monkeypatch, terminology, units, assertions, validator=validator)
    out = tmp_path / "nested/run"
    assert (
        main(
            [
                "run",
                "--transcript",
                str(ROOT / "examples/transcript.json"),
                "--intake",
                str(ROOT / "examples/intake.json"),
                "--out",
                str(out),
                "--model",
                "gemma-4-e4b-it-q4_0",
            ]
        )
        == 0
    )
    extraction, report, bundle = documents(out)
    assert extraction["metadata"]["model_id"] == "gemma-4-e4b-it-q4_0"
    assert extraction["metadata"]["run_timestamp"] == report["note_metadata"]["run_timestamp"]
    exported = {entry["resource"]["id"] for entry in bundle["entry"][3:]}
    assert exported == set(report["export"]["exported_ids"])
    assert len(exported) == {"pass": 2, "nonmaterial-failure": 1}.get(mode, 0)
    rows = {row["id"]: row for row in report["resources"]}
    assert all(rows[identifier]["export_state"] == "final_exportable" for identifier in exported)
    if mode == "unavailable":
        assert "NOT_CHECKED" in capsys.readouterr().out
        assert "auris setup validator --fetch" in (out / "note.md").read_text()
        assert all(row["lanes"]["base_r4"]["state"] == "NOT_CHECKED" for row in rows.values())
    full_urls = {e["fullUrl"] for e in bundle["entry"]}
    for entry in bundle["entry"]:
        for value in entry["resource"].values():
            if isinstance(value, dict) and "reference" in value:
                assert value["reference"] in full_urls


def test_failed_run_has_only_diagnostics(monkeypatch, terminology, units, tmp_path, capsys):
    from extract_helpers import completion

    from auris.extract import pipeline
    from auris.extract.backend import FakeBackend
    from auris.extract.pipeline import extract

    patch_cli(monkeypatch, terminology, units)
    monkeypatch.setattr(
        pipeline,
        "extract_local",
        lambda t, c, m, p: extract(FakeBackend([completion(finish_reason="length")]), t, c, m),
    )
    monkeypatch.chdir(tmp_path)
    assert main(["demo"]) == 1
    directory = tmp_path / "out/demo"
    assert {p.name for p in directory.iterdir()} == {"extraction.json", "validation.json"}
    assert "truncated" in capsys.readouterr().err


def test_rerun_refuses_stale_outputs_before_model(monkeypatch, terminology, units, tmp_path):
    from auris.extract import pipeline

    patch_cli(monkeypatch, terminology, units)
    monkeypatch.chdir(tmp_path)
    assert main(["demo"]) == 0
    original = (tmp_path / "out/demo/note.md").read_bytes()
    monkeypatch.setattr(
        pipeline, "extract_local", lambda *args: pytest.fail("model must not start")
    )
    assert main(["demo"]) == 1
    assert (tmp_path / "out/demo/note.md").read_bytes() == original


def test_missing_terminology_and_fhir_are_actionable(monkeypatch, tmp_path, units, capsys):
    from auris.fhir import contracts

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AURIS_CACHE", str(tmp_path / "cache"))
    assert main(["demo"]) == 1
    assert "auris setup fhir" in capsys.readouterr().err
    monkeypatch.setattr(contracts, "load_unit_sets", lambda *args: units)
    assert main(["demo"]) == 1
    assert "auris setup terminology --fetch" in capsys.readouterr().err


def test_help_lists_complete_interface(capsys):
    with pytest.raises(SystemExit) as result:
        main(["--help"])
    assert result.value.code == 0
    help_text = capsys.readouterr().out
    for name in ("setup", "extract", "run", "demo", "checklist"):
        assert name in help_text
    assert "not implemented" not in help_text
