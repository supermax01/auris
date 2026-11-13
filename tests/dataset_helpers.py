"""Authored fictional data for offline dataset tests; no model or network."""

from auris.config import DatasetPin
from auris.datasets.aci import adapt_aci
from auris.transcript import TranscriptSource


def pin(dataset="primock57", manifest_sha256="0" * 64):
    return DatasetPin(
        id=dataset,
        repository=f"https://github.com/fictional/{dataset}",
        commit="a" * 40,
        license="CC-BY-4.0",
        manifest_sha256=manifest_sha256,
    )


def source(dataset="primock57", key="fictional", split="all"):
    return TranscriptSource(
        dataset=dataset,
        encounter_id=key,
        repository=f"https://github.com/fictional/{dataset}",
        commit="a" * 40,
        license="CC-BY-4.0",
        variant="fictional-fixture",
        upstream_split=split,
        source_files=("fictional.txt",),
        human_transcription=True,
        speaker_tags="source roles; not adjudicated",
    )


def grid(intervals, tier="Speaker"):
    text = (
        'File type = "ooTextFile"\nObject class = "TextGrid"\n'
        "xmin = 0\nxmax = 100\ntiers? <exists>\nsize = 1\nitem []:\nitem [1]:\n"
        f'class = "IntervalTier"\nname = "{tier}"\nxmin = 0\nxmax = 100\n'
        f"intervals: size = {len(intervals)}\n"
    )
    for i, (start, end, words) in enumerate(intervals, 1):
        text += (
            f"intervals [{i}]:\nxmin = {start}\nxmax = {end}\n"
            'text = "' + words.replace('"', '""') + '"\n'
        )
    return text


def corpus(size, *, dataset="primock57", split="all"):
    return {
        f"{split}-{i:03d}": adapt_aci(
            f"[patient] Fictional {i} " + "word " * i, source(dataset, f"{split}-{i:03d}", split)
        )
        for i in range(size)
    }
