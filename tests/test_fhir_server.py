"""Integration proof: transaction bundle, read-back, no duplicates on rerun."""

import copy
import gzip
import hashlib
import io
import subprocess
import tarfile

import pytest
from fhir_helpers import GOLDENS

from auris.config import load_frozen_config
from auris.fhir import build_collection
from auris.fhir.server import LocalFhirServer, check_bundle, references, to_transaction
from auris.setup.fhir_server import default_cache_dir, setup_fhir_server, war_path

COLLECTION = {
    "resourceType": "Bundle",
    "type": "collection",
    "entry": [
        {"fullUrl": "urn:uuid:p", "resource": {"resourceType": "Patient", "id": "patient-1"}},
        {
            "fullUrl": "urn:uuid:c",
            "resource": {
                "resourceType": "Condition",
                "id": "condition-1",
                "subject": {"reference": "urn:uuid:p"},
            },
        },
    ],
}


class FakeServer:
    """A tiny in-memory FHIR server with transaction PUT semantics."""

    def __init__(self, *, duplicate_on_rerun=False, drop_patient=False):
        self.store = {}
        self.duplicate_on_rerun = duplicate_on_rerun
        self.drop_patient = drop_patient
        self.extra = 0

    def request(self, method, path, body=None):
        if method == "POST":
            urls = {e["fullUrl"]: e["request"]["url"] for e in body["entry"]}
            entries = []
            for entry in body["entry"]:
                key = entry["request"]["url"]
                resource = copy.deepcopy(entry["resource"])
                for value in [resource.get("subject", {})]:
                    if value.get("reference") in urls:
                        value["reference"] = urls[value["reference"]]
                existed = key in self.store
                if self.drop_patient and key.startswith("Patient/"):
                    continue
                if existed and self.duplicate_on_rerun:
                    self.extra += 1
                self.store[key] = resource
                entries.append({"response": {"status": "200 OK" if existed else "201 Created"}})
            return 200, {"entry": entries}
        if "?_summary=count" in path:
            kind = path.split("?")[0]
            total = sum(key.startswith(kind + "/") for key in self.store)
            return 200, {"total": total + (self.extra if kind == "Condition" else 0)}
        return (200, self.store[path]) if path in self.store else (404, {})


def test_transaction_puts_each_record_to_its_own_id_and_keeps_references():
    transaction = to_transaction(COLLECTION)
    assert transaction["type"] == "transaction"
    assert [e["request"] for e in transaction["entry"]] == [
        {"method": "PUT", "url": "Patient/patient-1"},
        {"method": "PUT", "url": "Condition/condition-1"},
    ]
    assert transaction["entry"][1]["resource"]["subject"]["reference"] == "urn:uuid:p"
    assert COLLECTION["entry"][0].get("request") is None  # the export itself is untouched
    assert references(transaction["entry"][1]["resource"]) == ["urn:uuid:p"]


@pytest.mark.parametrize(
    "bundle",
    [
        {"resourceType": "Bundle", "type": "transaction", "entry": [{}]},
        {"resourceType": "Bundle", "type": "collection", "entry": []},
        {"resourceType": "Patient"},
    ],
)
def test_transaction_refuses_anything_but_a_non_empty_collection(bundle):
    with pytest.raises(ValueError):
        to_transaction(bundle)


def test_check_passes_when_accepted_resolved_and_idempotent():
    report = check_bundle(FakeServer(), COLLECTION)
    assert report["passed"], report
    assert report["first_post"]["entries"] == {"201": 2}
    assert report["second_post"]["entries"] == {"200": 2}
    assert report["read_back"]["references_checked"] == 1


def test_one_server_checks_many_bundles_that_share_a_clinician():
    server = FakeServer()
    shared = {"fullUrl": "urn:uuid:dr", "resource": {"resourceType": "Practitioner", "id": "dr-1"}}
    for n in (1, 2, 3):
        bundle = copy.deepcopy(COLLECTION)
        for entry in bundle["entry"]:
            entry["resource"]["id"] += f"-{n}"
        bundle["entry"][1]["resource"]["subject"]["reference"] = "urn:uuid:p"
        bundle["entry"].append(copy.deepcopy(shared))
        report = check_bundle(server, bundle)
        assert report["passed"], (n, report)
        assert report["already_present"] == ([] if n == 1 else ["Practitioner/dr-1"])
        assert report["counts"]["expected"]["Practitioner"] == (1 if n == 1 else 0)


def test_check_fails_on_duplicates_or_unresolved_references():
    assert not check_bundle(FakeServer(duplicate_on_rerun=True), COLLECTION)["passed"]
    report = check_bundle(FakeServer(drop_patient=True), COLLECTION)
    assert not report["passed"]
    assert report["read_back"]["missing"] == ["Patient/patient-1"]
    assert report["read_back"]["unresolved"] == ["Condition/condition-1 -> Patient/patient-1"]


def _layer(member: str, content: bytes) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        info = tarfile.TarInfo(member)
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return gzip.compress(raw.getvalue(), mtime=0)


def test_setup_checks_the_layer_digest_and_the_server_file_pin(tmp_path):
    war = b"PK fake war"
    layer = _layer("app/main.war", war)
    pin = load_frozen_config().fhir_server.model_copy(
        update={
            "layer_digest": "sha256:" + hashlib.sha256(layer).hexdigest(),
            "war_sha256": hashlib.sha256(war).hexdigest(),
            "war_bytes": len(war),
        }
    )
    with pytest.raises(ValueError, match="missing; run auris setup fhir-server --fetch"):
        setup_fhir_server(pin, tmp_path)
    tampered = lambda p, destination: destination.write_bytes(_layer("app/main.war", b"other"))  # noqa: E731
    with pytest.raises(ValueError, match="layer does not match its digest"):
        setup_fhir_server(pin, tmp_path, fetch=True, fetcher=tampered)
    assert not war_path(pin, tmp_path).exists()
    path = setup_fhir_server(pin, tmp_path, fetch=True, fetcher=lambda p, d: d.write_bytes(layer))
    assert path.read_bytes() == war
    assert setup_fhir_server(pin, tmp_path) == path  # verified again, no download
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="does not match its pin"):
        setup_fhir_server(pin, tmp_path)


@pytest.mark.fhir_server
@pytest.mark.allow_hosts(["127.0.0.1"])
def test_golden_records_load_into_the_real_hapi_server(map_case):
    from auris.validation.runner import find_java

    pin = load_frozen_config().fhir_server
    war = war_path(pin, default_cache_dir())
    if not war.is_file():
        pytest.skip("local FHIR test server unavailable; run auris setup fhir-server --fetch")
    try:
        subprocess.run([find_java(), "-version"], capture_output=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        pytest.skip(f"Java unavailable: {error}")
    mapped = [map_case(case["name"]) for case in GOLDENS]
    intake = mapped[0][0].intake
    collection = build_collection(intake, [outcome for _, _, outcome in mapped], preview=True)
    with LocalFhirServer(setup_fhir_server(pin, default_cache_dir()), pin) as server:
        report = check_bundle(server, collection)
    assert report["passed"], report
    assert report["records"] == len(collection["entry"]) > 10
    assert report["read_back"]["references_checked"] > report["records"]


class Variant(FakeServer):
    """FakeServer that misbehaves in one chosen way."""

    def __init__(self, how):
        super().__init__()
        self.how, self.posts = how, 0

    def request(self, method, path, body=None):
        if method == "POST":
            self.posts += 1
            if self.how == "keeps-old-content":  # answers 200 for a record but keeps the old one
                kept = {
                    e["request"]["url"]: self.store.get(e["request"]["url"]) for e in body["entry"]
                }
                status, response = super().request(method, path, body)
                self.store.update({k: v for k, v in kept.items() if v is not None})
                return status, response
            status, response = super().request(method, path, body)
            if self.how == "empty-second" and self.posts == 2:
                return 200, {"entry": []}
            if self.how == "swapped" and self.posts == 1:
                response["entry"].reverse()
            if self.how == "drops-reference":
                self.store["Condition/condition-1"].pop("subject", None)
            return status, response
        if self.how == "count-fails" and "_summary=count" in path:
            return 400, {"resourceType": "OperationOutcome"}
        return super().request(method, path, body)


@pytest.mark.parametrize(
    "how", ["empty-second", "swapped", "drops-reference", "count-fails", "keeps-old-content"]
)
def test_check_fails_whenever_the_server_does_not_do_what_was_asked(how):
    server = Variant(how)
    if how == "keeps-old-content":  # the record exists with other content; the update is ignored
        server.store["Patient/patient-1"] = {"resourceType": "Patient", "id": "patient-1", "x": 1}
    if how == "swapped":  # one record present (200), one new (201): order matters
        server.store["Patient/patient-1"] = copy.deepcopy(COLLECTION["entry"][0]["resource"])
    report = check_bundle(server, COLLECTION)
    assert not report["passed"], report


def test_a_record_reported_as_created_when_it_already_existed_fails():
    server = FakeServer()
    check_bundle(server, COLLECTION)
    original = server.request

    def always_created(method, path, body=None):
        status, response = original(method, path, body)
        if method == "POST":
            for entry in response["entry"]:
                entry["response"]["status"] = "201 Created"
        return status, response

    server.request = always_created
    assert not check_bundle(server, COLLECTION)["passed"]


def test_server_always_stops_and_cleans_up_when_starting_is_interrupted(monkeypatch, tmp_path):
    import atexit
    import signal

    from auris.fhir import server as module

    class Process:
        terminated = killed = False

        def poll(self):
            return None

        def terminate(self):
            Process.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            Process.killed = True

    monkeypatch.setattr("auris.validation.runner.find_java", lambda: "java")
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(module, "_free_port", lambda: 1)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(module.LocalFhirServer, "request", interrupted)
    registered = []
    monkeypatch.setattr(atexit, "register", lambda f, *a: registered.append(f))
    monkeypatch.setattr(
        atexit, "unregister", lambda f: registered.remove(f) if f in registered else None
    )
    before = signal.getsignal(signal.SIGTERM)
    war = tmp_path / "main.war"
    war.write_bytes(b"x")
    server = module.LocalFhirServer(war, load_frozen_config().fhir_server, startup_seconds=5)
    with pytest.raises(KeyboardInterrupt):
        server.__enter__()
    assert Process.terminated and server.process is None and server.folder is None
    assert not registered and signal.getsignal(signal.SIGTERM) == before


def test_an_interruption_during_shutdown_stops_the_server_and_is_passed_on(tmp_path):
    from auris.fhir.server import LocalFhirServer

    class Process:
        killed = False

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            if timeout:
                raise KeyboardInterrupt
            return 0

        def kill(self):
            Process.killed = True

    server = LocalFhirServer(tmp_path / "main.war", load_frozen_config().fhir_server)
    server.process, server.folder = Process(), tmp_path / "server"
    server.folder.mkdir()
    with pytest.raises(KeyboardInterrupt):
        server.__exit__(None, None, None)
    assert Process.killed and server.process is None and not (tmp_path / "server").exists()
