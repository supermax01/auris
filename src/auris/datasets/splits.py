"""Length-stratified, seeded public split manifests with no transcript text or gold."""

import hashlib
import random
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from auris.eval.gold import Identifier, Record, Sha256, canonical_json
from auris.transcript import Transcript

SEED = 1729
GOLD_STATUS = "none (awaiting clinician annotation)"
METHOD = "word-count rank terciles; largest-remainder quotas; Python Random sample; v1"


class SplitEncounter(Record):
    id: Identifier
    transcript_sha256: Sha256
    text_sha256: Sha256
    upstream_split: str
    variant: str
    human_transcription: bool
    speaker_tags: str
    word_count: int = Field(ge=0)
    length_stratum: Literal["short", "medium", "long"]


class PublicSplit(Record):
    schema_version: Literal["public-split-1"] = "public-split-1"
    id: Literal["primock57", "aci-bench"]
    split_id: Identifier
    split: Literal["dev", "test"]
    seed: int
    method: Literal[METHOD] = METHOD
    gold_status: Literal["none (awaiting clinician annotation)"] = GOLD_STATUS
    source_manifest_sha256: Sha256
    encounters: tuple[SplitEncounter, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique(self) -> "PublicSplit":
        for field in ("id", "transcript_sha256", "text_sha256"):
            if len({getattr(e, field) for e in self.encounters}) != len(self.encounters):
                raise ValueError("duplicate encounter in public split")
        return self


def word_count(transcript: Transcript) -> int:
    return sum(len(re.findall(r"\b\w+\b", s.text)) for s in transcript.segments)


def strata(corpus: Mapping[str, Transcript]) -> dict[str, str]:
    ordered = sorted(corpus, key=lambda key: (word_count(corpus[key]), key))
    return {
        key: ("short", "medium", "long")[min(2, index * 3 // len(ordered))]
        for index, key in enumerate(ordered)
    }


def stratified_sample(corpus: Mapping[str, Transcript], count: int, *, seed: int) -> set[str]:
    if not 0 <= count <= len(corpus):
        raise ValueError("split sample exceeds eligible population")
    if not corpus:
        return set()
    labels = strata(corpus)
    bins = [
        [key for key in sorted(corpus) if labels[key] == label]
        for label in ("short", "medium", "long")
    ]
    quotas = [len(group) * count // len(corpus) for group in bins]
    # Sort the actual fractional remainders descending (integer arithmetic).
    remainder_order = sorted(range(3), key=lambda i: (-(len(bins[i]) * count % len(corpus)), i))
    for i in remainder_order[: count - sum(quotas)]:
        quotas[i] += 1
    rng = random.Random(seed)
    return {
        key for group, quota in zip(bins, quotas, strict=True) for key in rng.sample(group, quota)
    }


def build_splits(
    dataset: str, corpus: Mapping[str, Transcript], source_sha256: str, *, seed: int = SEED
) -> tuple[PublicSplit, PublicSplit]:
    labels = {}
    if dataset == "primock57":
        if len(corpus) != 57:
            raise ValueError("PriMock57 must contain 57 consultations")
        labels = strata(corpus)
        dev = stratified_sample(corpus, 17, seed=seed)
        test = set(corpus) - dev
    elif dataset == "aci-bench":
        cohorts = {
            split: {
                k: t for k, t in corpus.items() if t.source and t.source.upstream_split == split
            }
            for split in ("train", "valid", "test1", "test2", "test3")
        }
        if {k: len(v) for k, v in cohorts.items()} != {
            "train": 55,
            "valid": 20,
            "test1": 40,
            "test2": 40,
            "test3": 40,
        }:
            raise ValueError("ACI canonical cohort sizes changed")
        for cohort in cohorts.values():
            labels.update(strata(cohort))
        dev = set(cohorts["valid"]) | stratified_sample(cohorts["train"], 3, seed=seed)
        test = set().union(
            *(stratified_sample(cohorts[f"test{i}"], 20, seed=seed + i) for i in (1, 2, 3))
        )
    else:
        raise ValueError("unsupported split dataset")
    manifests = []
    for split, keys in (("dev", dev), ("test", test)):
        entries = []
        for key in sorted(keys):
            t = corpus[key]
            assert t.source is not None
            entries.append(
                SplitEncounter(
                    id=key,
                    transcript_sha256=t.content_hash,
                    text_sha256=hashlib.sha256(
                        canonical_json([s.text for s in t.segments]).encode()
                    ).hexdigest(),
                    upstream_split=t.source.upstream_split,
                    variant=t.source.variant,
                    human_transcription=t.source.human_transcription,
                    speaker_tags=t.source.speaker_tags,
                    word_count=word_count(t),
                    length_stratum=labels[key],
                )
            )
        manifests.append(
            PublicSplit(
                id=dataset,
                split_id=f"{dataset}-{split}-v1",
                split=split,
                seed=seed,
                source_manifest_sha256=source_sha256,
                encounters=tuple(entries),
            )
        )
    if {e.text_sha256 for e in manifests[0].encounters} & {
        e.text_sha256 for e in manifests[1].encounters
    }:
        raise ValueError("duplicate transcript text across public dev and test")
    return manifests[0], manifests[1]


def verify_splits(directory: Path, dataset: str, corpus: Mapping[str, Transcript]) -> None:
    source_hash = hashlib.sha256((directory / f"{dataset}-sources.json").read_bytes()).hexdigest()
    expected = build_splits(dataset, corpus, source_hash)
    for split in expected:
        frozen = PublicSplit.model_validate_json(
            (directory / f"{dataset}-{split.split}.json").read_text("utf-8")
        )
        if frozen != split:
            raise ValueError("frozen public split mismatch; do not regenerate during development")
