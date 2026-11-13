"""Canonical file inventory and encounter identity joins for the pinned corpora."""

import csv
import io
import json
from collections.abc import Callable
from pathlib import Path

from auris.config import DatasetPin
from auris.datasets.aci import adapt_aci
from auris.datasets.primock import adapt_primock
from auris.transcript import Transcript, TranscriptSource

ACI_SPLITS = {
    "train": "train",
    "valid": "valid",
    "test1": "clinicalnlp_taskB_test1",
    "test2": "clinicalnlp_taskC_test2",
    "test3": "clef_taskC_test3",
}


def source_paths(root: Path, dataset: str) -> tuple[str, ...]:
    if dataset == "primock57":
        paths = ["README.md", "LICENSE.md", "transcripts/README.md"]
        paths.extend(str(p.relative_to(root)) for p in root.glob("transcripts/*.TextGrid"))
    elif dataset == "aci-bench":
        paths = ["README.md"]
        for split, challenge in ACI_SPLITS.items():
            paths.extend(
                (
                    f"data/challenge_data_json/{challenge}_full.json",
                    f"data/challenge_data/{challenge}_metadata.csv",
                )
            )
            variants = ["aci_asrcorr"]
            if split != "train":
                variants.append("virtscribe_humantrans")
            for variant in variants:
                paths.extend(
                    (
                        f"data/src_experiment_data_json/{split}_{variant}.json",
                        f"data/src_experiment_data/{split}_{variant}_metadata.csv",
                    )
                )
    else:
        raise ValueError("unsupported public dataset")
    return tuple(sorted(paths))


def _source(
    pin: DatasetPin,
    encounter: str,
    split: str,
    variant: str,
    paths: tuple[str, ...],
    *,
    human: bool,
) -> TranscriptSource:
    return TranscriptSource(
        dataset=pin.id,
        encounter_id=encounter,
        repository=pin.repository,
        commit=pin.commit,
        license=pin.license,
        variant=variant,
        upstream_split=split,
        source_files=paths,
        human_transcription=human,
        speaker_tags="source roles; not adjudicated" if human else "speaker tags unverified",
    )


def _rows(read: Callable[[str], str], path: str) -> dict[str, str]:
    raw = json.loads(read(path))
    result = {}
    for row in raw["data"]:
        key, src = row["file"], row["src"]
        if not isinstance(key, str) or not isinstance(src, str) or key in result:
            raise ValueError("invalid or duplicate ACI source row")
        result[key] = src
    return result


def _metadata(read: Callable[[str], str], path: str) -> list[dict[str, str]]:
    rows = list(csv.DictReader(io.StringIO(read(path))))
    keys = [(r["dataset"], r["id"]) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate ACI metadata identity")
    return rows


def load_corpus(
    pin: DatasetPin, read: Callable[[str], str], paths: tuple[str, ...]
) -> dict[str, Transcript]:
    """The setup reader checks each byte hash before decoding it. Notes are unused."""
    if pin.id == "primock57":
        result = {}
        for doctor in paths:
            if not doctor.endswith("_doctor.TextGrid"):
                continue
            patient = doctor.replace("_doctor.TextGrid", "_patient.TextGrid")
            if patient not in paths:
                raise ValueError("unpaired PriMock57 consultation")
            key = Path(doctor).name.removesuffix("_doctor.TextGrid")
            result[key] = adapt_primock(
                read(doctor),
                read(patient),
                _source(
                    pin,
                    key,
                    "all",
                    "human_textgrid",
                    (doctor, patient),
                    human=True,
                ),
            )
        if len(result) * 2 != sum(p.endswith(".TextGrid") for p in paths):
            raise ValueError("unpaired or unexpected PriMock57 TextGrid")
        return result
    result = {}
    identities = set()
    for split, challenge in ACI_SPLITS.items():
        challenge_path = f"data/challenge_data_json/{challenge}_full.json"
        metadata_path = f"data/challenge_data/{challenge}_metadata.csv"
        challenge_rows = _rows(read, challenge_path)
        metadata = _metadata(read, metadata_path)
        by_identity = {}
        for row in metadata:
            key = f"{row['encounter_id']}-{row['dataset']}"
            identity = row["dataset"], row["id"]
            if key not in challenge_rows or identity in identities:
                raise ValueError("ACI encounter identity missing or duplicated across splits")
            identities.add(identity)
            by_identity[identity] = key
            if row["dataset"] == "virtassist":
                result[key] = adapt_aci(
                    challenge_rows[key],
                    _source(
                        pin,
                        key,
                        split,
                        "virtassist_challenge_reference",
                        (challenge_path, metadata_path),
                        human=True,
                    ),
                )
        if len(by_identity) != len(challenge_rows):
            raise ValueError("ACI challenge metadata count mismatch")
        variants = [("aci", "aci_asrcorr", False)]
        if split != "train":
            variants.append(("virtscribe", "virtscribe_humantrans", True))
        for kind, variant, human in variants:
            source_path = f"data/src_experiment_data_json/{split}_{variant}.json"
            variant_meta = f"data/src_experiment_data/{split}_{variant}_metadata.csv"
            rows = _rows(read, source_path)
            meta = _metadata(read, variant_meta)
            if set(rows) != {f"{i}-{kind}" for i in range(len(meta))}:
                raise ValueError("ACI variant row IDs do not match metadata positions")
            for index, row in enumerate(meta):
                key = by_identity.get((row["dataset"], row["id"]))
                if key is None or row["dataset"] != kind or key in result:
                    raise ValueError("ACI canonical variant identity join failed")
                result[key] = adapt_aci(
                    rows[f"{index}-{kind}"],
                    _source(
                        pin,
                        key,
                        split,
                        variant,
                        (source_path, variant_meta, metadata_path),
                        human=human,
                    ),
                )
    return dict(sorted(result.items()))
