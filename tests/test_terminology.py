"""Offline exact-coding, candidate retrieval, and atomic setup tests."""

import io
import sqlite3
import zipfile

import pytest
from pydantic import ValidationError
from terminology_helpers import FIXTURES, fixture_archives, fixture_config, write_archives

from auris.cli import main
from auris.config import TerminologyPin, load_frozen_config
from auris.setup.terminology import ensure_archive, setup_terminology, verify_archive
from auris.terminology import ICD10CM, RXNORM, TerminologyIndex
from auris.terminology.index import build_index, enable_fts, icd10cm_terms, rxnorm_terms
from auris.terminology.text import DRUG_FAMILIES, UK_US_NAMES, normalise


@pytest.fixture
def index_path(tmp_path):
    config = write_archives(tmp_path)
    return setup_terminology(config, tmp_path)


@pytest.fixture
def index(index_path):
    with TerminologyIndex(index_path) as value:
        yield value


@pytest.mark.parametrize(
    "text,code",
    [
        ("amoxicillin", "723"),
        (" AMOXICILLIN! ", "723"),
        ("penicillin G", "7980"),
        ("acetaminophen", "161"),
        ("amoxicillin sodium", "723"),
        ("Tylenol", "161"),
        ("amoxicillin 500 MG Oral Capsule", "308191"),
        ("acetaminophen 325 MG Oral Tablet [Tylenol]", "209387"),
    ],
)
def test_exact_rxnorm(index, text, code):
    assert index.exact(text, RXNORM).code == code


@pytest.mark.parametrize(
    "text",
    [
        "amoxycillin",
        "amoxicillin 500",
        "amoxicillin capsules",
        "amoxicillin 250 MG Oral Capsule",
        "amoxicillin (as amoxicillin trihydrate) 500 MG Oral Capsule",  # SY is not SCD/SBD
        "amoxicillin / clavulanate",
        "co-amoxiclav",
        "",
        "unknown fictional medication",
    ],
)
def test_near_miss_or_disallowed_type_never_codes(index, text):
    assert index.exact(text, RXNORM) is None


@pytest.mark.parametrize("family", sorted(DRUG_FAMILIES))
def test_drug_families_never_code(index, family):
    assert index.exact(family, RXNORM) is None


@pytest.mark.parametrize("uk,us", [(k, v) for k, v in UK_US_NAMES.items() if k != "co amoxiclav"])
def test_uk_synonyms(index, uk, us):
    assert index.exact(uk, RXNORM) == index.exact(us, RXNORM)
    assert index.exact(uk, RXNORM) is not None


@pytest.mark.parametrize(
    "text,code",
    [
        ("Acute bronchitis, unspecified", "J20.9"),
        ("TYPE 2 DIABETES MELLITUS WITHOUT COMPLICATIONS", "E11.9"),
        ("Cholera due to Vibrio cholerae 01, biovar cholerae", "A00.0"),
        ("Unspecified asthma, uncomplicated", "J45.909"),
    ],
)
def test_icd_billable_dotted_codes(index, text, code):
    assert index.exact(text, ICD10CM).code == code


@pytest.mark.parametrize("text", ["Cholera", "acute bronchitis", "asthma", "diabetes", "J20.9"])
def test_icd_header_or_near_miss_never_codes(index, text):
    assert index.exact(text, ICD10CM) is None


def test_icd_short_description_and_three_character_billable_code():
    # Synthetic parser shape only, derived from a real row, not used as clinical coding.
    row = (FIXTURES / "icd10cm_order_2027.txt").read_text().splitlines()[1]
    short = "Parser fixture short description".ljust(60)
    rows = list(icd10cm_terms([row[:6] + "A00    " + row[13:16] + short + row[76:]]))
    assert {r[4] for r in rows} == {normalise(short), normalise(row[77:])}
    assert {r[1] for r in rows} == {"A00"}


def test_numeric_punctuation_is_significant():
    assert normalise("drug 1.5 mg") != normalise("drug 1 5 mg")
    assert normalise("drug 1.5 mg") != normalise("drug 15 mg")
    assert normalise("drug 1/2 mg") != normalise("drug 1 2 mg")


def test_candidates_are_ranked_deduplicated_and_never_used_as_exact(index):
    assert index.exact("amoxicillin capsule", RXNORM) is None
    candidates = index.candidates("amoxicillin capsule", RXNORM, 3)
    assert candidates and candidates == index.candidates("amoxicillin capsule", RXNORM, 3)
    assert len({c.code for c in candidates}) == len(candidates)
    assert [c.score for c in candidates] == sorted([c.score for c in candidates], reverse=True)
    assert index.candidates("Acute bronchitis", ICD10CM, 1)[0].code == "J20.9"
    assert index.candidates("", RXNORM, 5) == []
    assert index.candidates("amoxicillin", RXNORM, 0) == []


def test_fts_and_scan_have_identical_ranking(index):
    fast = index.candidates("amoxicillin 500 MG Oral Capsule", RXNORM, 20)
    index.metadata["fts5"] = False
    assert index.candidates("amoxicillin 500 MG Oral Capsule", RXNORM, 20) == fast


def test_runtime_without_fts_and_copied_index_fallback(tmp_path, monkeypatch):
    import auris.terminology.index as module

    monkeypatch.setattr(module, "enable_fts", lambda connection: False)
    path = setup_terminology(write_archives(tmp_path), tmp_path)
    with TerminologyIndex(path) as value:
        assert not value.metadata["fts5"]
        assert value.candidates("amoxicillin", RXNORM, 1)[0].code == "723"
        value.metadata["fts5"] = True  # simulate an FTS table unavailable on this runtime
        assert value.candidates("amoxicillin", RXNORM, 1)[0].code == "723"

    class NoFts:
        def execute(self, *args):
            raise sqlite3.OperationalError("no such module: fts5")

    assert enable_fts(NoFts()) is False


def test_setup_is_rebuildable_and_byte_deterministic(index_path):
    before = index_path.read_bytes()
    assert setup_terminology(fixture_config(), index_path.parent).read_bytes() == before
    with TerminologyIndex(index_path) as value:
        assert value.metadata["rxnorm"]["release"] == "2026-11-02"
        with pytest.raises(sqlite3.OperationalError):
            value.connection.execute("DELETE FROM terms")


def test_missing_archives_list_sources_and_fetch_command(tmp_path):
    config = fixture_config()
    with pytest.raises(ValueError) as error:
        setup_terminology(config, tmp_path)
    message = str(error.value)
    assert config.rxnorm.url in message and config.icd10cm.url in message
    assert "auris setup terminology --fetch" in message
    assert not list(tmp_path.iterdir())


def test_fake_fetcher_is_verified_then_builds(tmp_path):
    config = fixture_config()
    payload = dict(zip((config.rxnorm.url, config.icd10cm.url), fixture_archives(), strict=True))
    fetched = []

    def fetch(url, path):
        fetched.append(url)
        path.write_bytes(payload[url])

    path = setup_terminology(config, tmp_path, fetch=True, fetcher=fetch)
    assert path.is_file() and fetched == list(payload)
    setup_terminology(config, tmp_path, fetch=True, fetcher=lambda *_: pytest.fail("cached fetch"))


@pytest.mark.parametrize("cached", [False, True])
def test_checksum_mismatch_never_promotes_or_builds(tmp_path, cached):
    pin = fixture_config().rxnorm
    if cached:
        (tmp_path / pin.filename).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        ensure_archive(
            pin, tmp_path, fetch=True, fetcher=lambda url, path: path.write_bytes(b"corrupt")
        )
    assert not (tmp_path / "index.sqlite").exists()
    assert (tmp_path / pin.filename).exists() == cached
    assert not list(tmp_path.glob("*.download"))


def test_published_md5_is_checked(tmp_path):
    config = write_archives(tmp_path)
    with pytest.raises(ValueError, match="MD5 mismatch"):
        verify_archive(
            tmp_path / config.rxnorm.filename, config.rxnorm.model_copy(update={"md5": "0" * 32})
        )


def test_transport_failure_cleans_temporary_download(tmp_path):
    def fail(url, path):
        path.write_bytes(b"partial")
        raise OSError("fictional transport failure")

    with pytest.raises(OSError):
        ensure_archive(fixture_config().rxnorm, tmp_path, fetch=True, fetcher=fail)
    assert not list(tmp_path.iterdir())


def test_bad_zip_preserves_previous_index(index_path):
    before = index_path.read_bytes()
    bad = index_path.parent / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(zipfile.BadZipFile):
        build_index(bad, bad, index_path)
    assert index_path.read_bytes() == before
    assert not list(index_path.parent.glob("*.sqlite.tmp"))


def test_cli_missing_archive_is_offline(tmp_path, capsys):
    assert main(["setup", "terminology", "--cache", str(tmp_path)]) == 1
    assert "--fetch" in capsys.readouterr().err


def test_cli_success_with_fake_setup(tmp_path, monkeypatch, capsys):
    import auris.setup.terminology as module

    def setup(config, cache, *, fetch):
        assert cache == tmp_path and fetch is False
        return cache / "index.sqlite"

    monkeypatch.setattr(module, "setup_terminology", setup)
    assert main(["setup", "terminology", "--cache", str(tmp_path)]) == 0
    assert "checksums OK; index rebuilt" in capsys.readouterr().out


@pytest.mark.parametrize(
    "field,value",
    [
        ("sha256", "bad"),
        ("md5", "bad"),
        ("filename", "../outside.zip"),
        ("url", "http://example.org"),
    ],
)
def test_pin_validation(field, value):
    pin = load_frozen_config().terminology.rxnorm.model_dump()
    with pytest.raises(ValidationError):
        TerminologyPin.model_validate({**pin, field: value})


def test_icd_effective_period(index):
    assert index.icd_valid_on("2026-10-01")
    assert index.icd_valid_on("2027-09-30T10:00:00Z")
    assert not index.icd_valid_on("2026-09-30")
    assert not index.icd_valid_on("2027-10-01")
    assert not index.icd_valid_on("2026")


def test_rxnorm_source_suppression_and_ambiguous_relationships():
    conso = (FIXTURES / "RXNCONSO.RRF").read_text()
    relations = (FIXTURES / "RXNREL.RRF").read_text()
    # Mutation of real extract rows tests rejection; no synthetic clinical codes.
    altered = []
    for line in conso.splitlines():
        fields = line.split("|")
        if fields[0] == "723":
            fields[16] = "Y"
        if fields[0] == "161":
            fields[11] = "OTHER"
        altered.append("|".join(fields))
    rows = list(rxnorm_terms(io.StringIO("\n".join(altered)), io.StringIO(relations)))
    assert not any(row[1] in ("723", "161") for row in rows)
    assert all(row[5] is None for row in rows if row[1] in ("267360", "202433"))
    rel = relations.splitlines()[0].split("|")
    rel[0], rel[4], rel[7] = "723", "202433", "tradename_of"
    rows = list(rxnorm_terms(io.StringIO(conso), io.StringIO(relations + "|".join(rel) + "\n")))
    assert next(row for row in rows if row[1] == "202433")[5] is None


@pytest.mark.parametrize(
    "parser", [icd10cm_terms, lambda rows: rxnorm_terms(rows, io.StringIO(""))]
)
def test_malformed_extract_is_refused(parser):
    with pytest.raises(ValueError, match="malformed"):
        list(parser(io.StringIO("bad row")))


def test_family_guard_wins_even_if_an_index_row_claims_an_ingredient(index_path):
    # A corrupt/mislabelled terminology row must not defeat the explicit family guard.
    with sqlite3.connect(index_path) as connection:
        connection.execute("UPDATE terms SET normalised='penicillin' WHERE code='7980'")
    with TerminologyIndex(index_path) as value:
        assert value.exact("PENICILLIN", RXNORM) is None


def test_exact_name_collision_abstains(index_path):
    with sqlite3.connect(index_path) as connection:
        connection.execute("UPDATE terms SET normalised='amoxicillin' WHERE code='161'")
    with TerminologyIndex(index_path) as value:
        assert value.exact("amoxicillin", RXNORM) is None


def test_wrong_index_schema_is_refused(index_path):
    with sqlite3.connect(index_path) as connection:
        connection.execute("UPDATE metadata SET value='{\"schema_version\": 2}'")
    with pytest.raises(ValueError, match="unsupported terminology index"):
        TerminologyIndex(index_path)


def test_source_pins_match_owner_supplied_artifacts():
    config = load_frozen_config().terminology
    assert config.rxnorm.release == "2026-11-02"
    assert (
        config.rxnorm.sha256 == "c742a6c4b2995194aa3479400e08a7c1ad707178bfc80fe9c8cd7afa693ae987"
    )
    assert config.rxnorm.md5 == "cd240fc9655dcd2cb54b1e185f3d8702"
    assert config.icd10cm.release == "FY2027"
    assert (
        config.icd10cm.sha256 == "91c6c9d1117764ce72375a0f3a5493b1725dafbc1ab283b55799076c9e194965"
    )
