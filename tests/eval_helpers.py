"""Fictional evaluation helpers; no accuracy or validator claims."""

from pathlib import Path

from auris.eval.gold import Fact, load_dataset
from auris.eval.predictions import Prediction
from auris.fhir.models import ValidationLanes

MANIFEST = Path(__file__).resolve().parents[1] / "eval/harness-check/manifest.json"
DATASET, TRANSCRIPTS = load_dataset(MANIFEST)


def prediction(gold, **updates):
    data = {name: getattr(gold, name) for name in Fact.model_fields}
    data.update(id=gold.id, lanes=ValidationLanes(evidence="PASS"), export_state="needs_review")
    data.update(updates)
    return Prediction.model_validate(data)


def one_dataset(*encounters):
    return DATASET.model_copy(update={"encounters": tuple(encounters)})
