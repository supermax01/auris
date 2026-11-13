"""Blind, symmetric alignment of independent human annotations."""

from collections.abc import Sequence
from pathlib import Path

from auris.annotation.models import AnnotationDocument, AnnotationFact, Assignment
from auris.eval.matching import RESOURCE_CLASSES, hungarian, overlap
from auris.eval.normalization import normalize


def load_pair(
    a: Path, b: Path, assignment: Assignment | None = None
) -> tuple[dict[str, AnnotationDocument], dict[str, AnnotationDocument]]:
    """Read only annotation filenames, never predictions or evaluation artifacts."""
    sides = []
    for folder in (a, b):
        documents = {}
        for path in sorted(folder.rglob("*.annotation.json")):
            if path.is_symlink():
                raise ValueError("annotation input cannot be a symlink")
            doc = AnnotationDocument.model_validate_json(path.read_bytes())
            if doc.encounter_id in documents:
                raise ValueError("duplicate encounter in annotator directory")
            if doc.status != "done":
                raise ValueError("agreement requires completed independent annotations")
            documents[doc.encounter_id] = doc
        if not documents or len({d.annotator_id for d in documents.values()}) != 1:
            raise ValueError("each directory must contain exactly one annotator's work")
        sides.append(documents)
    first, second = sides
    validate_pair(first, second, assignment)
    return first, second


def validate_pair(
    a: dict[str, AnnotationDocument],
    b: dict[str, AnnotationDocument],
    assignment: Assignment | None = None,
) -> None:
    if not a or set(a) != set(b):
        raise ValueError("independent encounter sets differ")
    if any(key != doc.encounter_id for side in (a, b) for key, doc in side.items()):
        raise ValueError("annotation encounter identity differs from its assignment key")
    if (
        len({d.annotator_id for d in a.values()}) != 1
        or len({d.annotator_id for d in b.values()}) != 1
    ):
        raise ValueError("each side requires one annotator")
    if next(iter(a.values())).annotator_id == next(iter(b.values())).annotator_id:
        raise ValueError("two independent annotator ids are required")
    hashes = {d.guidelines_sha256 for d in (*a.values(), *b.values())}
    if len(hashes) != 1:
        raise ValueError("guideline hashes differ; issue matching assignments")
    if any(a[key].transcript_sha256 != b[key].transcript_sha256 for key in a):
        raise ValueError("transcript hashes differ between annotators")
    if any(d.status != "done" for d in (*a.values(), *b.values())):
        raise ValueError("agreement requires completed independent annotations")
    if assignment is not None:
        if set(a) != set(assignment.encounter_ids):
            raise ValueError("encounter sets differ from assignment")
        if hashes != {assignment.guidelines_sha256}:
            raise ValueError("guideline hashes differ from assignment")
        entries = {e.id: e for e in assignment.dataset.encounters}
        if any(a[key].transcript_sha256 != entries[key].transcript_sha256 for key in a):
            raise ValueError("transcript hashes differ from frozen assignment")


def concept_equal(a: AnnotationFact, b: AnnotationFact) -> bool:
    return a.gold_concept_id == b.gold_concept_id or bool(
        {normalize(t) for t in a.acceptable_texts if normalize(t)}
        & {normalize(t) for t in b.acceptable_texts if normalize(t)}
    )


def align(
    a: Sequence[AnnotationFact], b: Sequence[AnnotationFact], *, same_kind: bool = False
) -> tuple[tuple[int, int], ...]:
    weights = [
        [
            int(
                (RESOURCE_CLASSES[x.kind], x.experiencer, x.polarity)
                == (RESOURCE_CLASSES[y.kind], y.experiencer, y.polarity)
                and concept_equal(x, y)
                and overlap(x.evidence, y.evidence)
                and (not same_kind or x.kind == y.kind)
            )
            for y in b
        ]
        for x in a
    ]
    return tuple((i, j) for i, j in hungarian(weights) if weights[i][j])


def differences(a: AnnotationFact, b: AnnotationFact) -> tuple[str, ...]:
    """All semantic, evidence and note differences; local fact ids have no meaning."""
    left, right = a.model_dump(mode="json"), b.model_dump(mode="json")
    changed = []
    for key in left:
        if key == "critical":
            changed.extend(
                f"critical.{name}" for name in left[key] if left[key][name] != right[key][name]
            )
        elif key != "id" and left[key] != right[key]:
            changed.append(key)
    return tuple(changed)
