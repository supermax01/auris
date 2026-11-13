"""Reproduce the model-free coverage audit and frozen split manifests offline.

Writes absent aggregate manifests; refuses to overwrite differing frozen bytes.
No inference backend is invoked. Source content never enters committed files.
"""

import hashlib
from collections import Counter
from pathlib import Path

from auris.config import load_frozen_config
from auris.datasets.coverage import CATEGORIES, DrugLexicon, aggregate
from auris.datasets.splits import build_splits
from auris.eval.gold import canonical_json
from auris.extract.leakage import check_leakage
from auris.extract.prompt import prompt_text
from auris.setup.datasets import default_cache_dir, verified_corpus
from auris.setup.terminology import default_cache_dir as terminology_cache
from auris.terminology.index import TerminologyIndex

LIMITS = """Each cell is candidate occurrences / encounters containing at least one.
These are lexical estimates, **not gold facts, clinical accuracy, or FHIR eligibility**.
Counts below 25 are sparse candidate evidence; even larger counts cannot establish
25 adjudicated examples. Public mock data may have appeared in model pretraining.

Counting unit: one sentence within one source turn per class, except vitals count
one per named vital type per sentence and drug mentions count longest nonoverlapping
exact token matches. Repetitions across turns count again. Questions, hypothetical
statements, negation scope, false friends and unrelated nearby numbers can inflate
counts. Cross-turn facts and unlisted wording can be missed. Source roles are used
only as lexical filters, never as adjudicated role gold. Symptoms, assessed problems
and patient history use deliberately shallow cue lists; they are not diagnoses.

Vitals require a named BP, pulse/heart rate, respiratory rate, temperature, oxygen
saturation, weight or height within eight whitespace tokens of a digit or number
word (zero through nineteen, tens, hundred). No value/unit correctness, measurement
versus history, or actual spoken dose is established. Allergy words are divided by
the existing sentence negation-cue detector; this does not prove affirmed allergy
or denial. Ruled-out and family-history phrase patterns are separate cues.
Medication names use every RxNorm term in the pinned local index plus the existing
audited UK-to-US synonyms whose target occurs in that index. Matching uses exact
normalized token sequences; no codes are assigned. New-order candidates also need
a clinician role and an order verb, with no sentence negation/family cue; reported
medications need a taking/using/on/continuing cue. Dosed-order candidates additionally
need a number followed by a dose/unit word in that sentence. This can overcount
recommendations and miss prescriptions distributed over several turns.

The primary human/scripted table excludes ACI ASR-corrected transcripts. The second
table includes all eligible canonical text to expose that distinction. ACI has 195
eligible encounters (83 human/scripted, 112 ASR-corrected); 12 training virtscribe
encounters are excluded because no named human variant is supplied. This still
provides all 20 valid encounters and 40 in each upstream test cohort. No raw ASR,
reference notes, checklists, model outputs or clinical gold are used for counts or
split selection. Coverage is computed on the full eligible pools before selecting
length strata; coverage values never drive selection.

PriMock57's UK telemedicine setting remains a limitation. The private recording
set still needs the PLAN §10 targets: at least 25 adjudicated examples each of
spoken vitals, dosed US-drug orders and affirmed allergies, with the specified
single-microphone and companion conditions. This lexical audit cannot certify any
of those targets. Human annotation and the independent FHIR subset remain pending.
"""


def write_frozen(path: Path, text: str) -> None:
    if path.exists():
        if path.read_bytes() != text.encode("utf-8"):
            raise ValueError(f"frozen audit differs: {path.name}; review required, not overwritten")
    else:
        path.write_text(text, encoding="utf-8")


def audit(directory: Path, cache: Path, index_path: Path) -> dict:
    config = load_frozen_config()
    corpora = {
        pin.id: verified_corpus(cache / pin.id, pin, directory / f"{pin.id}-sources.json")
        for pin in config.datasets
    }
    with TerminologyIndex(index_path) as index:
        if index.metadata.get("rxnorm") != config.terminology.rxnorm.model_dump(mode="json"):
            raise ValueError("coverage requires the pinned RxNorm index; rerun terminology setup")
        drugs = DrugLexicon.from_index(index)
        terminology_metadata = index.metadata
    # Full-pool counts precede the split selection; never use coverage to stratify.
    full_counts = {name: aggregate(corpus, drugs) for name, corpus in corpora.items()}
    full_human = {
        name: aggregate(
            {k: t for k, t in corpus.items() if t.source and t.source.human_transcription}, drugs
        )
        for name, corpus in corpora.items()
    }
    manifests = {
        pin.id: build_splits(pin.id, corpora[pin.id], pin.manifest_sha256)
        for pin in config.datasets
    }
    result = {
        "schema_version": "1",
        "method": "lexical_candidates_v1",
        "drug_lexicon_sha256": hashlib.sha256(canonical_json(drugs.names).encode()).hexdigest(),
        "drug_lexicon_names": len(drugs.names),
        "terminology": terminology_metadata,
        "sources": {pin.id: pin.manifest_sha256 for pin in config.datasets},
        "datasets": {},
    }
    for name, corpus in corpora.items():
        groups = {"all": corpus}
        groups.update(
            {
                split.split: {e.id: corpus[e.id] for e in split.encounters}
                for split in manifests[name]
            }
        )
        result["datasets"][name] = {}
        for label, group in groups.items():
            human = {k: t for k, t in group.items() if t.source and t.source.human_transcription}
            result["datasets"][name][label] = {
                "encounters": len(group),
                "human_encounters": len(human),
                "all_canonical": full_counts[name] if label == "all" else aggregate(group, drugs),
                "human_only": full_human[name] if label == "all" else aggregate(human, drugs),
            }
    lines = ["# Public mock-corpus candidate coverage", "", LIMITS, ""]
    columns = [(name, split) for name in corpora for split in ("all", "dev", "test")]
    for mode, title, size in (
        ("human_only", "Human/scripted references", "human_encounters"),
        ("all_canonical", "All canonical text (includes corrected ASR)", "encounters"),
    ):
        lines.extend(
            [
                f"## {title}",
                "",
                "| Candidate type | "
                + " | ".join(
                    f"{name} {split} (n={result['datasets'][name][split][size]})"
                    for name, split in columns
                )
                + " |",
                "|---|" + "---:|" * len(columns),
            ]
        )
        for category in CATEGORIES:
            cells = [result["datasets"][name][split][mode][category] for name, split in columns]
            lines.append(
                "| "
                + category
                + " | "
                + " | ".join(f"{cell['candidates']} / {cell['encounters']}" for cell in cells)
                + " |"
            )
        lines.append("")
    lines.extend(
        [
            "## Provenance and reproduction",
            "",
            f"Method: `{result['method']}`; {len(drugs.names)} normalized drug names.",
            f"Lexicon SHA-256: `{result['drug_lexicon_sha256']}`.",
            "",
            "Run `.venv/bin/python -m auris.datasets.audit` with the pinned local datasets",
            "and terminology index. Existing manifests are verified byte for byte; differing",
            "files are refused. Split seed 1729; see the engineering step-6c note for selection",
            "and exclusions.",
            "",
        ]
    )
    # Write the coverage artifact before freezing membership files.
    write_frozen(directory / "coverage-counts.json", canonical_json(result))
    write_frozen(directory / "coverage-counts.md", "\n".join(lines))
    for name, splits in manifests.items():
        for split in splits:
            write_frozen(
                directory / f"{name}-{split.split}.json",
                canonical_json(split.model_dump(mode="json")),
            )
        corpus = corpora[name]
        leakage = check_leakage(
            prompt_text(),
            ["\n".join(s.text for s in t.segments) for t in corpus.values()],
            clinical_policy="warn",
        )
        write_frozen(directory / f"{name}-leakage.json", canonical_json(leakage.metadata()))
        removed: Counter[str] = Counter()
        for t in corpus.values():
            assert t.source
            removed.update(t.source.markup_removed)
        stats = {
            "encounters": len(corpus),
            "markup_removed": dict(sorted(removed.items())),
            "markup_characters_removed": sum(
                t.source.markup_characters_removed for t in corpus.values() if t.source
            ),
            "silence_intervals_dropped": sum(
                t.source.silence_intervals_dropped for t in corpus.values() if t.source
            ),
            "empty_extraction_segments": sum(
                not s.text.strip() for t in corpus.values() for s in t.segments
            ),
            "unknown_role_segments": sum(
                s.role == "unknown" for t in corpus.values() for s in t.segments
            ),
        }
        write_frozen(directory / f"{name}-adaptation.json", canonical_json(stats))
    return result


def main() -> None:
    from auris.cli import _repo_root

    result = audit(
        _repo_root() / "eval/datasets", default_cache_dir(), terminology_cache() / "index.sqlite"
    )
    for dataset, groups in result["datasets"].items():
        print(
            f"{dataset}: "
            + ", ".join(f"{split}={row['encounters']}" for split, row in groups.items())
        )
    print("coverage and splits frozen/verified; no extraction or scoring performed")


if __name__ == "__main__":
    main()
