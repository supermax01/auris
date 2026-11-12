"""Rebuildable SQLite terminology index. All input is local, public terminology."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import zipfile
from collections.abc import Iterable
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any, TextIO

from pydantic import BaseModel, ConfigDict

from auris.terminology.text import DRUG_FAMILIES, ICD10CM, RXNORM, drug_name, normalise

RX_CONSO = "rrf/RXNCONSO.RRF"
RX_REL = "rrf/RXNREL.RRF"
ICD_ORDER = "Code Descriptions/icd10cm_order_2027.txt"
# system, code, display, tty, normalised name, assignable code/display
Term = tuple[str, str, str, str, str, str | None, str | None]


class Candidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    system: str
    code: str
    display: str
    term_type: str
    score: float = 1.0

    def coding(self) -> dict[str, str]:
        return {"system": self.system, "code": self.code, "display": self.display}


def _rrf(lines: Iterable[str], columns: int) -> Iterable[list[str]]:
    for line in lines:
        if not line.strip():
            continue
        fields = line.rstrip("\r\n").split("|")
        if len(fields) != columns + 1 or fields[-1] != "":
            raise ValueError("malformed terminology RRF row")
        yield fields[:-1]


def rxnorm_terms(conso: TextIO, relations: TextIO) -> Iterable[Term]:
    """Use RXNORM/N terms only; PIN and BN need one explicit IN relationship.

    RXNREL describes RXCUI2's relation to RXCUI1. Read both inverse forms,
    and count MIN targets too so a combination brand cannot become one IN.
    Blank RXNREL SUPPRESS (used by CPC) and N both mean unsuppressed.
    """
    terms: dict[str, tuple[str, str]] = {}
    for row in _rrf(conso, 18):
        if (
            row[11] == "RXNORM"
            and row[16] == "N"
            and row[12] in {"IN", "PIN", "BN", "MIN", "SCD", "SBD"}
        ):
            value = (row[12], row[14])
            if row[0] in terms and terms[row[0]] != value:
                raise ValueError("conflicting preferred RxNorm terms")
            terms[row[0]] = value
    targets: dict[str, set[str]] = {}
    for row in _rrf(relations, 16):
        if row[10] != "RXNORM" or row[14] not in ("", "N"):
            continue
        if row[7] in ("form_of", "tradename_of"):
            alias, ingredient = row[4], row[0]
        elif row[7] in ("has_form", "has_tradename"):
            alias, ingredient = row[0], row[4]
        else:
            continue
        if terms.get(alias, (None,))[0] in ("PIN", "BN"):
            targets.setdefault(alias, set()).add(ingredient)
    for code, (tty, display) in sorted(terms.items()):
        assigned = code if tty in ("IN", "SCD", "SBD") else None
        possible = targets.get(code, set())
        if tty in ("PIN", "BN") and len(possible) == 1:
            target = next(iter(possible))
            if terms.get(target, (None,))[0] == "IN":
                assigned = target
        yield (
            RXNORM,
            code,
            display,
            tty,
            normalise(display),
            assigned,
            terms[assigned][1] if assigned else None,
        )


def icd10cm_terms(lines: Iterable[str]) -> Iterable[Term]:
    for line in lines:
        if not line.strip():
            continue
        if (
            len(line.rstrip("\r\n")) < 78
            or not line[:5].isdigit()
            or any(line[pos] != " " for pos in (5, 13, 15, 76))
            or line[14] not in "01"
        ):
            raise ValueError("malformed ICD-10-CM order row")
        if line[14] != "1":
            continue
        raw_code = line[6:13].strip()
        code = raw_code[:3] + ("." + raw_code[3:] if len(raw_code) > 3 else "")
        short, long = line[16:76].strip(), line[77:].strip()
        if not raw_code.isalnum() or not short or not long:
            raise ValueError("incomplete ICD-10-CM order row")
        for name in sorted({short, long}):
            yield ICD10CM, code, long, "billable", normalise(name), code, long


def enable_fts(connection: sqlite3.Connection) -> bool:
    """Probe this SQLite runtime, including builds without the FTS5 extension."""
    try:
        connection.execute("CREATE VIRTUAL TABLE terms_fts USING fts5(normalised)")
    except sqlite3.OperationalError:
        return False
    connection.execute(
        "INSERT INTO terms_fts(rowid, normalised) SELECT rowid, normalised FROM terms"
    )
    return True


def build_index(
    rxnorm_zip: Path,
    icd10cm_zip: Path,
    destination: Path,
    *,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Build atomically, with stable ordering and no timestamps or local paths.

    Setup verifies archive pins before calling this function. Tests use tiny
    extract zips through this same reader. An error preserves the old index.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=destination.parent, suffix=".sqlite.tmp")
    os.close(fd)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(temporary)) as connection, connection:
            connection.execute(
                "CREATE TABLE terms(system TEXT, code TEXT, display TEXT, tty TEXT, "
                "normalised TEXT, assigned_code TEXT, assigned_display TEXT)"
            )
            with zipfile.ZipFile(rxnorm_zip) as rx, zipfile.ZipFile(icd10cm_zip) as icd:
                with (
                    rx.open(RX_CONSO) as conso,
                    rx.open(RX_REL) as relations,
                    icd.open(ICD_ORDER) as order,
                ):
                    rows = sorted(
                        set(
                            [
                                *rxnorm_terms(io.TextIOWrapper(conso), io.TextIOWrapper(relations)),
                                *icd10cm_terms(io.TextIOWrapper(order)),
                            ]
                        )
                    )
            connection.executemany("INSERT INTO terms VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            connection.execute("CREATE INDEX exact_name ON terms(system, normalised)")
            info = {**(metadata or {}), "schema_version": 1, "fts5": enable_fts(connection)}
            connection.execute("CREATE TABLE metadata(value TEXT NOT NULL)")
            connection.execute(
                "INSERT INTO metadata VALUES (?)", (json.dumps(info, sort_keys=True),)
            )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


class TerminologyIndex:
    def __init__(self, path: str | Path) -> None:
        self.connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        self.connection.row_factory = sqlite3.Row
        self.metadata = json.loads(
            self.connection.execute("SELECT value FROM metadata").fetchone()[0]
        )
        if self.metadata.get("schema_version") != 1:
            self.close()
            raise ValueError("unsupported terminology index; run auris setup terminology")

    def __enter__(self) -> TerminologyIndex:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def exact(self, text: str, system: str) -> Candidate | None:
        if system == RXNORM and normalise(text) in DRUG_FAMILIES:
            return None
        name = drug_name(text) if system == RXNORM else normalise(text)
        rows = self.connection.execute(
            "SELECT * FROM terms WHERE system=? AND normalised=?", (system, name)
        ).fetchall()
        # Even one unresolved exact-name homonym prevents an automatic choice.
        if not rows or any(row["assigned_code"] is None for row in rows):
            return None
        codes = {row["assigned_code"] for row in rows}
        if len(codes) != 1:
            return None
        row = rows[0]
        return Candidate(
            system=system,
            code=row["assigned_code"],
            display=row["assigned_display"],
            term_type="IN" if row["tty"] in ("PIN", "BN") else row["tty"],
        )

    def icd_valid_on(self, day: str) -> bool:
        try:
            value = date.fromisoformat(day[:10])
            return (
                date.fromisoformat(self.metadata["icd10cm_valid_from"])
                <= value
                <= date.fromisoformat(self.metadata["icd10cm_valid_to"])
            )
        except (KeyError, ValueError):
            return False

    def candidates(self, text: str, system: str, k: int = 5) -> list[Candidate]:
        """Token-set Jaccard ranking, deterministic ties; never used to auto-code."""
        if k <= 0:
            return []
        name = drug_name(text) if system == RXNORM else normalise(text)
        tokens = set(name.split())
        if not tokens:
            return []
        query = "SELECT * FROM terms WHERE system=? AND assigned_code IS NOT NULL"
        parameters: tuple[str, ...] = (system,)
        if self.metadata.get("fts5"):
            query += " AND rowid IN (SELECT rowid FROM terms_fts WHERE terms_fts MATCH ?)"
            parameters += (
                " OR ".join('"' + token.replace('"', '""') + '"' for token in sorted(tokens)),
            )
        try:
            rows = self.connection.execute(query, parameters).fetchall()
        except sqlite3.OperationalError:
            # An index copied from a runtime with FTS5 still works without FTS5.
            rows = self.connection.execute(
                "SELECT * FROM terms WHERE system=? AND assigned_code IS NOT NULL", (system,)
            ).fetchall()
        ranked: dict[str, Candidate] = {}
        for row in rows:
            other = set(row["normalised"].split())
            overlap = tokens & other
            if not overlap:
                continue
            candidate = Candidate(
                system=system,
                code=row["assigned_code"],
                display=row["assigned_display"],
                term_type="IN" if row["tty"] in ("PIN", "BN") else row["tty"],
                score=len(overlap) / len(tokens | other),
            )
            previous = ranked.get(candidate.code)
            if previous is None or candidate.score > previous.score:
                ranked[candidate.code] = candidate
        return sorted(ranked.values(), key=lambda c: (-c.score, c.code, c.display))[:k]
