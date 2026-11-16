"""Public terminology extracts and fictional visit helpers for offline tests."""

import hashlib
import io
import json
import zipfile
from pathlib import Path

from auris.config import TerminologyConfig, load_frozen_config
from auris.fhir.contracts import UNIT_SET_IDS, unit_sets_from_valuesets
from auris.intake import Intake
from auris.terminology.index import ICD_ORDER, RX_CONSO, RX_REL

FIXTURES = Path(__file__).parent / "fixtures" / "terminology"


def fixture_archives() -> tuple[bytes, bytes]:
    payloads = []
    for members in (
        {RX_CONSO: "RXNCONSO.RRF", RX_REL: "RXNREL.RRF"},
        {ICD_ORDER: "icd10cm_order_2027.txt"},
    ):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, source in members.items():
                info = zipfile.ZipInfo(name, date_time=(2026, 11, 2, 0, 0, 0))
                archive.writestr(info, (FIXTURES / source).read_bytes())
        payloads.append(buffer.getvalue())
    return tuple(payloads)


def fixture_config() -> TerminologyConfig:
    config = load_frozen_config().terminology.model_dump(mode="json")
    for name, data in zip(("rxnorm", "icd10cm"), fixture_archives(), strict=True):
        config[name]["sha256"] = hashlib.sha256(data).hexdigest()
        config[name]["md5"] = hashlib.md5(data, usedforsecurity=False).hexdigest()
    return TerminologyConfig.model_validate(config)


def write_archives(cache: Path) -> TerminologyConfig:
    cache.mkdir(parents=True, exist_ok=True)
    config = fixture_config()
    for pin, data in zip((config.rxnorm, config.icd10cm), fixture_archives(), strict=True):
        (cache / pin.filename).write_bytes(data)
    return config


def fixture_unit_sets():
    return unit_sets_from_valuesets(
        [json.loads((FIXTURES / f"ValueSet-{name}.json").read_text()) for name in UNIT_SET_IDS]
    )


def fictional_intake() -> Intake:
    # The repository's fictional example, placed inside the pinned ICD fiscal year.
    data = json.loads((Path(__file__).parents[1] / "examples" / "intake.json").read_text())
    data["encounter"]["period"] = {"start": "2026-11-12T09:00:00-05:00"}
    return Intake.model_validate(data)
