"""Private adjudicated gold packages with frozen split and synthetic intake checks."""

import secrets
from pathlib import Path

from auris._storage import prepare_output_directory, write_private_text
from auris.annotation.adjudication import (
    AdjudicationAssignment,
    AdjudicationRecord,
    resolved,
    verify_record,
)
from auris.annotation.models import AnnotationFact
from auris.annotation.pilot import fictional_intake, load_prepared
from auris.assertions import AssertionKind
from auris.datasets.splits import PublicSplit
from auris.eval.gold import (
    Annotation,
    Dataset,
    Encounter,
    GoldAssertion,
    OutOfScopeFact,
    canonical_json,
    digest,
    load_dataset,
)
from auris.eval.ledger import register_test_use
from auris.intake import Intake
from auris.transcript import Transcript

# Same URI identities used by freeze_prediction, not a terminology lexicon.
HARNESS_SYSTEMS = {
    "http://www.nlm.nih.gov/research/umls/rxnorm": "rxnorm",
    "http://hl7.org/fhir/sid/icd-10-cm": "icd10cm",
    "http://loinc.org": "loinc",
    "http://snomed.info/sct": "snomed",
}


def scoring_assertion(fact: AnnotationFact) -> GoldAssertion:
    """Project code-system spelling into the existing harness representation.

    Human concept identity/code never changes. Full URI originals remain in the
    issued assignment and audit. Unknown namespaces and auris: slugs are literal.
    """
    data = fact.model_dump(mode="json", exclude={"notes"})
    for code in data["codes"]:
        code["system"] = HARNESS_SYSTEMS.get(code["system"], code["system"])
    for uri, label in HARNESS_SYSTEMS.items():
        if data["gold_concept_id"].startswith(f"{uri}:"):
            data["gold_concept_id"] = label + data["gold_concept_id"][len(uri) :]
            break
    return GoldAssertion.model_validate(data)


def export_gold(
    assignment: AdjudicationAssignment,
    adjudicated: Path,
    intakes: Path,
    out: Path,
    *,
    cache: Path,
    manifests: Path,
    locked_run: bool = False,
    confirmed: bool = False,
) -> Path:
    public = assignment.assignment.dataset
    frozen = PublicSplit.model_validate_json(
        (manifests / f"{public.id}-{public.split}.json").read_bytes()
    )
    if public != frozen:
        raise ValueError("assignment disagrees with frozen split")
    keys = assignment.assignment.encounter_ids
    # Refuse before transcripts/intakes or any output package is opened/written.
    register_test_use(
        cache / "eval/test-ledger.jsonl",
        frozen,
        f"gold-export-{secrets.token_hex(12)}",
        "run",
        locked_run=locked_run,
        confirmed=confirmed,
    )
    records = {}
    for path in sorted(adjudicated.glob("*.adjudication.json")):
        if path.is_symlink():
            raise ValueError("adjudication record cannot be a symlink")
        record = AdjudicationRecord.model_validate_json(path.read_bytes())
        if record.encounter_id in records:
            raise ValueError("duplicate adjudicated encounter")
        records[record.encounter_id] = record
    if set(records) != set(keys):
        raise ValueError("adjudicated encounter set differs from assignment")
    # Validate the whole package before creating output.
    entries, files = [], {}
    for key in keys:
        record = records[key]
        if record.status != "adjudicated":
            raise ValueError("export requires adjudicated encounters only")
        facts, outside, roles, pending = resolved(assignment, record)
        if pending:
            raise ValueError("unresolved adjudication tasks")
        original = load_prepared(cache, frozen, key)
        verify_record(assignment, record, original)
        raw = original.model_dump(mode="json")
        for segment in raw["segments"]:
            segment["role"] = str(roles[segment["id"]])
        transcript = Transcript.model_validate(raw)
        intake_file = intakes / f"{key}.json"
        if intake_file.is_symlink():
            raise ValueError("fictional intake cannot be a symlink")
        intake = Intake.model_validate_json(intake_file.read_bytes())
        if intake != fictional_intake(public.id, key):
            raise ValueError("intake differs from the 7a fictional anchors")
        intake_text = intake_file.read_text("utf-8")
        assert original.source is not None
        entries.append(
            Encounter(
                id=key,
                transcript_path=f"transcripts/{key}.json",
                transcript_sha256=transcript.content_hash,
                source_transcript_sha256=original.content_hash,
                intake_path=f"intakes/{key}.json",
                intake_sha256=digest(intake_text.encode()),
                split=frozen.split,
                source=public.id,
                license=original.source.license,
                fictional=True,
                annotation=Annotation(
                    annotator_ids=(assignment.a[key].annotator_id, assignment.b[key].annotator_id),
                    adjudicator_id=assignment.adjudicator_id,
                    status="adjudicated",
                    independent=True,
                    guidelines_version=f"sha256:{assignment.assignment.guidelines_sha256}",
                    guidelines_sha256=assignment.assignment.guidelines_sha256,
                ),
                assertions=tuple(scoring_assertion(f) for f in facts),
                out_of_scope=tuple(
                    OutOfScopeFact.model_validate(f.model_dump(exclude={"notes"})) for f in outside
                ),
            )
        )
        files[f"transcripts/{key}.json"] = transcript.to_json()
        files[f"original-transcripts/{key}.json"] = original.to_json()
        files[f"intakes/{key}.json"] = intake_text
        files[f"audit/{key}.adjudication.json"] = canonical_json(record.model_dump(mode="json"))
    dataset = Dataset(
        id=f"{public.id}-adjudicated",
        split_id=frozen.split_id,
        split=frozen.split,
        declared_kinds=tuple(AssertionKind),
        clinical_watchlist=("medication", "allergy"),
        encounters=tuple(entries),
    )
    files["manifest.json"] = canonical_json(dataset.model_dump(mode="json"))
    files["adjudication-assignment.json"] = canonical_json(assignment.model_dump(mode="json"))
    files["README.md"] = (
        "# Adjudicated human gold — private research data\n\n"
        "Public mock consultations; intake anchors are FICTIONAL evaluation context, "
        "not recovered demographics. Evidence uses character offsets and substring hashes. "
        "Original transcripts and independent human answers are preserved alongside "
        "role-corrected scoring transcripts and decision histories.\n\n"
        "The scoring manifest spells known code-system URIs as the existing harness "
        "short tags, including code-based concept IDs. Codes and concept identity "
        "are unchanged; full human URI IDs remain in adjudication-assignment.json.\n\n"
        "Test gold creation is a human annotation operation. It does not authorize "
        "inference or scoring. Use the evaluation CLI and shared cache/eval/test-ledger.jsonl; "
        "the one-run lock and explicit rerun confirmation still apply. load_dataset verifies "
        "package integrity, not permission to score. Never call the low-level scorer to "
        "bypass that ledger. No model output was used in adjudication.\n"
    )
    prepare_output_directory(out)
    for name, content in files.items():
        write_private_text(out / name, content)
    manifest = out / "manifest.json"
    load_dataset(manifest)  # Structural/integrity check only, never test scoring.
    return manifest
