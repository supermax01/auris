"""Seeded dev-only pilot assignments and explicitly fictional intake anchors."""

import random
import re
import shlex
from datetime import date, timedelta
from pathlib import Path

from auris._storage import prepare_output_directory, write_private_text
from auris.annotation.models import Assignment
from auris.datasets.splits import PublicSplit
from auris.eval.gold import canonical_json, digest
from auris.intake import Intake
from auris.transcript import Transcript


def load_prepared(cache: Path, dataset: PublicSplit, encounter_id: str) -> Transcript:
    entry = next((e for e in dataset.encounters if e.id == encounter_id), None)
    if entry is None:
        raise ValueError("encounter outside assignment split")
    folder = (cache / "datasets" / "prepared" / dataset.id).resolve()
    target = folder / f"{entry.id}.json"
    if target.is_symlink() or not target.resolve().is_relative_to(folder):
        raise ValueError("prepared transcript cannot escape its dataset")
    transcript = Transcript.load(target)
    if transcript.content_hash != entry.transcript_sha256:
        raise ValueError("prepared transcript hash mismatch")
    if (
        transcript.source is None
        or transcript.source.dataset != dataset.id
        or transcript.source.encounter_id != entry.id
    ):
        raise ValueError("prepared transcript provenance mismatch")
    return transcript


def fictional_intake(dataset_id: str, encounter_id: str) -> Intake:
    """Evaluation context only, never claims to recover public patient details."""
    key = digest(f"{dataset_id}/{encounter_id}".encode())
    patient_id = f"fictional-p-{key[:16]}"
    day = date(2026, 10, 1) + timedelta(days=int(key[:8], 16) % 365)
    system = "https://example.org/auris/fictional-evaluation"
    return Intake.model_validate(
        {
            "patient": {
                "id": patient_id,
                "identifier": [{"system": system, "value": f"FICTIONAL-P-{key[:16]}"}],
                "name": [{"family": "Fictionalpatient", "given": ["Evaluation"]}],
                "gender": "unknown",
            },
            "practitioner": {
                "id": f"fictional-c-{key[:16]}",
                "identifier": [{"system": system, "value": f"FICTIONAL-C-{key[:16]}"}],
                "name": [{"family": "Fictionalclinician"}],
            },
            "encounter": {
                "id": f"fictional-e-{key[:16]}",
                "status": "finished",
                "class": {
                    "code": "AMB",
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                },
                "type": [{"text": "Fictional evaluation visit; synthetic intake anchors"}],
                "subject": {"reference": f"Patient/{patient_id}"},
                "period": {"start": f"{day.isoformat()}T12:00:00Z"},
            },
        }
    )


def pilot_ids(dataset: PublicSplit, n: int, seed: int) -> list[str]:
    if dataset.split != "dev":
        raise ValueError("pilot requires dev split only")
    if not 5 <= n <= min(10, len(dataset.encounters)):
        raise ValueError("pilot needs 5–10 encounters within the dev population")
    bins = [
        sorted(e.id for e in dataset.encounters if e.length_stratum == label)
        for label in ("short", "medium", "long")
    ]
    total = len(dataset.encounters)
    quotas = [len(group) * n // total for group in bins]
    order = sorted(range(3), key=lambda i: (-(len(bins[i]) * n % total), i))
    for i in order[: n - sum(quotas)]:
        quotas[i] += 1
    rng = random.Random(seed)
    return sorted(
        key for group, quota in zip(bins, quotas, strict=True) for key in rng.sample(group, quota)
    )


def create_pilot(
    dataset: PublicSplit, out: Path, guidelines: bytes, *, n: int, seed: int, cache: Path
) -> tuple[Assignment, Assignment]:
    selected = pilot_ids(dataset, n, seed)
    # Verify inputs before writing assignments; generate anchors for the entire
    # supplied public manifest, including encounters outside this pilot.
    for entry in dataset.encounters:
        load_prepared(cache, dataset, entry.id)
    # Anchor identity is explicitly fictional. Enumerating prepared filenames
    # lets us cover the full public corpus without opening held-out transcripts.
    prepared = cache / "datasets" / "prepared" / dataset.id
    anchor_ids = []
    for path in sorted(prepared.glob("*.json")):
        if path.name == "source-manifest.json":
            continue
        if path.is_symlink() or not re.fullmatch(r"[a-zA-Z0-9_-]+", path.stem):
            raise ValueError("invalid prepared encounter filename")
        anchor_ids.append(path.stem)
    prepare_output_directory(out)
    assignments = []
    for label in ("A", "B"):
        ids = selected.copy()
        random.Random(f"pilot-1:{seed}:{label}").shuffle(ids)
        assignment = Assignment(
            annotator_id=label,
            dataset=dataset,
            encounter_ids=tuple(ids),
            guidelines_sha256=digest(guidelines),
        )
        write_private_text(
            out / f"assignment-{label}.json", canonical_json(assignment.model_dump(mode="json"))
        )
        assignments.append(assignment)
    anchors = out / "fictional-intakes"
    anchors.mkdir(mode=0o700)
    for key in anchor_ids:
        fictional_intake(dataset.id, key).save(anchors / f"{key}.json")
    assignment_argument = (
        shlex.quote(str(out / "assignment-A.json"))
        if not out.is_absolute()
        else "PILOT_DIRECTORY/assignment-A.json"
    )
    sheet = f"""# Human-only pilot — {dataset.id} ({dataset.split_id})

Seed: {seed}. Selected {n} development encounters, stratified by frozen length
labels. A and B receive the same encounters in independently shuffled orders.
All intake anchors are FICTIONAL evaluation context, dated inside FY2027;
they are not assertions about people in the source data.

1. Coordinator: send each annotator only their assignment, guidelines and a
   separate private work directory. Use pseudonyms, never names or emails.
2. Read docs/annotation/guidelines.md and quick-reference.md before starting.
3. From the repository, start one session per annotator (replace A with B):

   .venv/bin/auris annotate serve --assignment {assignment_argument} --workdir work-A

   Replace PILOT_DIRECTORY with the actual pilot directory if shown, or use the
   actual assignment path if this sheet is moved. Open the printed
   loopback URL. Keep its session token private. No network setup is done here.
4. Select words within one turn, Add fact, fill the fields, save, then mark the
   encounter done. The page records foreground active time (60s idle cutoff)
   and open-encounter wall time. Record breaks, training and lookup difficulties
   in encounter notes; wall time excludes periods when the server is stopped.
5. Annotate independently, blind to the other annotator and all Auris output.
   Never use an AI assistant or pre-annotation. Do not exchange answers.
6. Hand back your private work directory, unchanged assignment, and notes to the
   coordinator through the agreed local/private channel. Do not commit them.
7. A third clinician adjudicates disagreements. Publish detection F1 and kappa
   per kind; freeze only at F1 >= 0.80 and kappa >= 0.70. After three rounds below
   threshold, narrow the schema and repeat; never lower the thresholds.
8. Re-price the 65h + 2.4h/test + 1.5h/dev budget using both annotators' timed
   work and adjudication/training overhead. No agreement is computed or claimed
   by this setup command.
"""
    write_private_text(out / "pilot-sheet.md", sheet)
    write_private_text(
        out / "pilot.json",
        canonical_json(
            {
                "seed": seed,
                "n": n,
                "selected": selected,
                "dataset": dataset.split_id,
                "fictional_intakes": True,
                "anchor_count": len(anchor_ids),
                "anchor_source": "prepared filenames; held-out transcripts not opened",
            }
        ),
    )
    return assignments[0], assignments[1]
