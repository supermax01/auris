"""Private leakage diagnostics; all sentence pairs here are authored fiction."""

import hashlib
import json
from importlib.resources import files

import pytest
from eval_helpers import DATASET, MANIFEST, TRANSCRIPTS

from auris.cli import main
from auris.eval.leakage_report import leakage_report
from auris.extract.prompt import PROMPT_VERSION, prompt_text


@pytest.fixture
def fictional_corpus(monkeypatch, tmp_path):
    original = TRANSCRIPTS[DATASET.encounters[0].id]
    transcript = original.model_copy(
        update={
            "segments": (
                original.segments[0].model_copy(update={"text": "I live with migraine too."}),
            )
        }
    )
    corpus = {"fictional-encounter": transcript}
    monkeypatch.setattr("auris.cli._repo_root", lambda: tmp_path)
    monkeypatch.setattr("auris.eval.leakage_report.load_dataset", lambda _: (DATASET, corpus))
    return corpus


def test_only_prompt_sentence_reaches_stdout_and_report_is_private(
    fictional_corpus, tmp_path, capsys
):
    assert (
        main(
            [
                "eval",
                "leakage-report",
                "--dataset",
                str(MANIFEST),
                "--prompt-version",
                "extraction_v2",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.out == "I live with migraine\n"
    assert captured.err == ""
    paths = list((tmp_path / "cache").rglob("*.json"))
    assert len(paths) == 1
    assert paths[0].stat().st_mode & 0o777 == 0o600
    assert all(p.stat().st_mode & 0o777 == 0o700 for p in (paths[0].parent, paths[0].parent.parent))
    report = json.loads(paths[0].read_text())
    assert report["hits"] == [
        {
            "kind": "near_duplicate",
            "encounter_id": "fictional-encounter",
            "prompt_sentence": "I live with migraine",
            "corpus_sentence": "I live with migraine too",
            "jaccard": 0.8,
        }
    ]
    assert report["leakage_check"] == "fail"
    path, sentences = leakage_report(str(MANIFEST))
    assert not sentences
    assert json.loads(path.read_text())["leakage_check"] == "pass"


@pytest.mark.parametrize("relative", ["cache", "cache/eval", "cache/eval/leakage-reports"])
def test_symlink_escape_refused(fictional_corpus, tmp_path, relative):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="real repository cache"):
        leakage_report(str(MANIFEST))
    assert not list(outside.iterdir())


def test_public_dataset_audits_verified_full_corpus(fictional_corpus, tmp_path, monkeypatch):
    called = []

    def verified(root, pin, manifest):
        called.append((root, pin.id, manifest))
        return fictional_corpus

    monkeypatch.setattr("auris.setup.datasets.verified_corpus", verified)
    monkeypatch.setattr("auris.setup.datasets.default_cache_dir", lambda: tmp_path / "sources")
    path, sentences = leakage_report("primock57", prompt_version="extraction_v2")
    assert called[0] == (
        tmp_path / "sources/primock57",
        "primock57",
        tmp_path / "eval/datasets/primock57-sources.json",
    )
    assert sentences == ("I live with migraine",)
    assert path.is_relative_to(tmp_path / "cache")
    assert json.loads(path.read_text())["leakage_check_details"]["clinical_policy"] == "warn"


def test_v3_only_rewords_one_example_and_preserves_v2():
    old = files("auris.extract").joinpath("prompts/extraction_v2.md").read_bytes()
    assert (
        hashlib.sha256(old).hexdigest()
        == "a66c105f84af129589e5047c1eb3b4457a13b527cebee495030cb7f05cffa93e"
    )
    assert PROMPT_VERSION == "extraction_v3"
    assert prompt_text() == old.decode().replace(
        "I live with migraine.", "Migraine affects me personally."
    )
