"""Opt-in sentence diagnostics, confined to the repository's ignored cache."""

import json
import re
from importlib.resources import files
from pathlib import Path

from auris._storage import write_private_text
from auris.config import load_frozen_config
from auris.eval.gold import canonical_json, load_dataset
from auris.extract.leakage import FIXTURE_CLINICAL_TERMS, check_leakage, sentence_texts
from auris.extract.profiles import PROMPT_VERSIONS
from auris.extract.prompt import PROMPT_VERSION


def leakage_report(
    dataset: str, *, prompt_version: str = PROMPT_VERSION
) -> tuple[Path, tuple[str, ...]]:
    """Audit full public corpora or a gold manifest; never reserve test inference."""
    from auris.cli import _repo_root
    from auris.datasets.splits import PublicSplit
    from auris.setup.datasets import default_cache_dir, verified_corpus

    if prompt_version not in PROMPT_VERSIONS:
        raise ValueError("unsupported prompt version")
    root = _repo_root().resolve()
    public_id = dataset if dataset in ("primock57", "aci-bench") else None
    if public_id is None:
        raw = json.loads(Path(dataset).read_text("utf-8"))
        if raw.get("schema_version") == "public-split-1":
            public_id = PublicSplit.model_validate(raw).id
    if public_id is not None:
        pin = next(p for p in load_frozen_config().datasets if p.id == public_id)
        transcripts = verified_corpus(
            default_cache_dir() / pin.id, pin, root / "eval/datasets" / f"{pin.id}-sources.json"
        )
        dataset_id, terms, policy = pin.id, FIXTURE_CLINICAL_TERMS, "warn"
    else:
        manifest, transcripts = load_dataset(Path(dataset))
        dataset_id, terms, policy = manifest.id, manifest.clinical_watchlist, "refuse"
    prompt = files("auris.extract").joinpath(f"prompts/{prompt_version}.md").read_text("utf-8")
    encounters = tuple(transcripts)
    texts = ["\n".join(s.text for s in t.segments) for t in transcripts.values()]
    report = check_leakage(prompt, texts, clinical_terms=terms, clinical_policy=policy)
    prompt_sentences = {" ".join(k): v for k, v in sentence_texts(prompt).items()}
    corpus_sentences = [{" ".join(k): v for k, v in sentence_texts(t).items()} for t in texts]
    hits = [
        {
            "kind": kind,
            "encounter_id": encounters[hit.corpus_index],
            "prompt_sentence": prompt_sentences[hit.prompt_sentence],
            "corpus_sentence": corpus_sentences[hit.corpus_index][hit.corpus_sentence],
            "jaccard": hit.jaccard,
        }
        for kind, overlaps in (
            ("exact", report.exact_sentences),
            ("near_duplicate", report.near_duplicates),
        )
        for hit in overlaps
    ]
    # No --out or AURIS_CACHE override: transcript-bearing diagnostics must stay
    # under the repository's /cache/ ignore rule, even through existing symlinks.
    directory = root / "cache" / "eval" / "leakage-reports"
    for parent in (directory, directory.parent, directory.parent.parent):
        if parent.is_symlink() or not parent.resolve().is_relative_to(root / "cache"):
            raise ValueError("leakage reports require a real repository cache directory")
    for parent in reversed((directory, directory.parent, directory.parent.parent)):
        parent.mkdir(mode=0o700, exist_ok=True)
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", dataset_id)
    path = directory / f"{slug}-{prompt_version}.json"
    write_private_text(
        path, canonical_json({**report.metadata(), "dataset_id": dataset_id, "hits": hits})
    )
    return path, tuple(dict.fromkeys(hit["prompt_sentence"] for hit in hits))
