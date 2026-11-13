"""Pinned checkout verification and canonical ACI identity joins, offline."""

import hashlib
import json

import pytest
from dataset_helpers import grid, pin

from auris.datasets.catalog import ACI_SPLITS, load_corpus, source_paths
from auris.eval.gold import canonical_json
from auris.setup.datasets import checkout_commit, verified_corpus, verify_checkout


def checkout(tmp_path):
    root = tmp_path / "checkout"
    (root / ".git").mkdir(parents=True)
    (root / ".git/HEAD").write_text("a" * 40 + "\n")
    (root / "transcripts").mkdir()
    for name in ("README.md", "LICENSE.md", "transcripts/README.md"):
        (root / name).write_text("Fictional test source")
    for role in ("doctor", "patient"):
        (root / f"transcripts/day1_consultation01_{role}.TextGrid").write_text(
            grid([(0, 1, "Fictional")])
        )
    files = {
        p: hashlib.sha256((root / p).read_bytes()).hexdigest()
        for p in source_paths(root, "primock57")
    }
    text = canonical_json({"dataset": "primock57", "commit": "a" * 40, "files": files})
    manifest = tmp_path / "sources.json"
    manifest.write_text(text)
    return root, pin(manifest_sha256=hashlib.sha256(text.encode()).hexdigest()), manifest


def test_detached_and_loose_and_packed_head(tmp_path):
    root, _, _ = checkout(tmp_path)
    assert checkout_commit(root) == "a" * 40
    (root / ".git/HEAD").write_text("ref: refs/heads/main\n")
    (root / ".git/refs/heads").mkdir(parents=True)
    ref = root / ".git/refs/heads/main"
    ref.write_text("b" * 40 + "\n")
    assert checkout_commit(root) == "b" * 40
    ref.unlink()
    (root / ".git/packed-refs").write_text("# pack-refs\n" + "c" * 40 + " refs/heads/main\n")
    assert checkout_commit(root) == "c" * 40


def test_worktree_head_and_common_ref(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    (root / ".git").write_text("gitdir: ../git/worktrees/fixture\n")
    git = tmp_path / "git/worktrees/fixture"
    git.mkdir(parents=True)
    (git / "commondir").write_text("../..\n")
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "git/packed-refs").write_text("a" * 40 + " refs/heads/main\n")
    assert checkout_commit(root) == "a" * 40


@pytest.mark.parametrize("change", ["commit", "source", "manifest", "inventory", "traversal"])
def test_pins_refuse_mismatch_before_adapter(tmp_path, change):
    root, frozen, manifest = checkout(tmp_path)
    assert len(verified_corpus(root, frozen, manifest)) == 1
    if change == "commit":
        (root / ".git/HEAD").write_text("b" * 40)
    elif change == "source":
        (root / "transcripts/day1_consultation01_doctor.TextGrid").write_text("tampered")
    elif change == "manifest":
        manifest.write_text(manifest.read_text() + " ")
    elif change == "inventory":
        (root / "transcripts/extra.TextGrid").write_text("unexpected")
    else:
        (root / ".git/HEAD").write_text("ref: refs/../../outside")
    with pytest.raises(ValueError):
        verified_corpus(root, frozen, manifest)


def test_source_symlink_outside_checkout_refuses(tmp_path):
    root, frozen, manifest = checkout(tmp_path)
    target = root / "README.md"
    target.unlink()
    outside = tmp_path / "outside"
    outside.write_text("Fictional test source")
    target.symlink_to(outside)
    with pytest.raises(ValueError, match="within"):
        verify_checkout(root, frozen, manifest)


def aci_files():
    files = {}
    for split, challenge in ACI_SPLITS.items():
        ids = {"aci": f"{split}-aci", "virtscribe": f"{split}-vs", "virtassist": f"{split}-va"}
        # Deliberately reverse upstream metadata order relative to variant order.
        files[f"data/challenge_data/{challenge}_metadata.csv"] = (
            "dataset,id,encounter_id\n"
            + "".join(f"{kind},{key},{key}\n" for kind, key in reversed(list(ids.items())))
        )
        files[f"data/challenge_data_json/{challenge}_full.json"] = json.dumps(
            {
                "data": [
                    {
                        "file": f"{key}-{kind}",
                        "src": f"[doctor] Fictional challenge {key}",
                        "tgt": "UNUSED NOTE",
                    }
                    for kind, key in ids.items()
                ]
            }
        )
        for kind, variant in (("aci", "aci_asrcorr"), ("virtscribe", "virtscribe_humantrans")):
            if split == "train" and kind == "virtscribe":
                continue
            files[f"data/src_experiment_data/{split}_{variant}_metadata.csv"] = (
                f"dataset,id\n{kind},{ids[kind]}\n"
            )
            files[f"data/src_experiment_data_json/{split}_{variant}.json"] = json.dumps(
                {
                    "data": [
                        {
                            "file": f"0-{kind}",
                            "src": f"[doctor] Fictional selected {variant} {split}",
                            "tgt": "UNUSED NOTE",
                        }
                    ]
                }
            )
    return files


def test_aci_joins_metadata_not_list_order_and_never_reads_raw_asr():
    files = aci_files()
    corpus = load_corpus(pin("aci-bench"), files.__getitem__, tuple(files))
    assert len(corpus) == 14  # train virtscribe intentionally has no eligible variant.
    corrected = corpus["valid-aci-aci"]
    assert corrected.source.variant == "aci_asrcorr"
    assert corrected.source.speaker_tags == "speaker tags unverified"
    assert not corrected.source.human_transcription
    assert corrected.segments[0].text == "Fictional selected aci_asrcorr valid"
    human = corpus["test1-vs-virtscribe"]
    assert human.source.human_transcription and human.source.variant == "virtscribe_humantrans"
    assert all("asr.json" not in path for t in corpus.values() for path in t.source.source_files)


def test_aci_broken_identity_join_refuses():
    files = aci_files()
    key = "data/src_experiment_data/valid_aci_asrcorr_metadata.csv"
    files[key] = files[key].replace("valid-aci", "wrong-identity")
    with pytest.raises(ValueError, match="identity join"):
        load_corpus(pin("aci-bench"), files.__getitem__, tuple(files))


def test_setup_materializes_private_inputs_and_audits_before_refusal(tmp_path, monkeypatch):
    from auris.setup.datasets import setup_datasets

    root, frozen, manifest = checkout(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    root.rename(cache / "primock57")
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    manifest.rename(manifests / "primock57-sources.json")
    monkeypatch.setattr("auris.datasets.splits.verify_splits", lambda *_: None)
    monkeypatch.setattr(
        "auris.setup.datasets.prompt_text", lambda: "Distinct prompt vocabulary only."
    )
    result = setup_datasets((frozen,), cache, manifests)
    prepared = cache / "prepared/primock57/day1_consultation01.json"
    assert prepared.stat().st_mode & 0o777 == 0o600
    assert prepared.parent.stat().st_mode & 0o777 == 0o700
    assert result["primock57"]["day1_consultation01"].source.commit == frozen.commit
    before = prepared.read_bytes()
    setup_datasets((frozen,), cache, manifests)
    assert prepared.read_bytes() == before
    # Exact sentence hit fails setup, but preserves the hash-verified annotation inputs.
    monkeypatch.setattr("auris.setup.datasets.prompt_text", lambda: "Fictional")
    with pytest.raises(ValueError, match="leakage"):
        setup_datasets((frozen,), cache, manifests)
    assert prepared.read_bytes() == before
    audit = json.loads((cache / "prepared/primock57-leakage.json").read_text())
    assert audit["leakage_check"] == "fail"
    assert "Fictional" not in json.dumps(audit)
