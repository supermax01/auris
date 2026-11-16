"""Integration proof: load an export into a local FHIR R4 test server.

The collection bundle becomes a ``transaction`` with client-assigned ids:
each record is ``PUT`` to its own id, and the ``urn:uuid`` references are
resolved by the server to those ids. The proof passes when the server
accepts it, every stored record and every reference resolves, and
submitting the same transaction again creates nothing new. Server
acceptance is reported separately from the validator lanes.

The server is HAPI FHIR, run from its pinned file on 127.0.0.1 only, with
an in-memory database in a private temporary folder that is removed after.
"""

from __future__ import annotations

import atexit
import copy
import http.client
import json
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from auris.config import FhirServerConfig


def to_transaction(collection: dict[str, Any]) -> dict[str, Any]:
    if collection.get("resourceType") != "Bundle" or collection.get("type") != "collection":
        raise ValueError("expected an Auris collection bundle")
    entries = collection.get("entry") or []
    if not entries:
        raise ValueError("the bundle has no records to load")
    transaction = {"resourceType": "Bundle", "type": "transaction", "entry": []}
    for entry in entries:
        resource = copy.deepcopy(entry["resource"])
        transaction["entry"].append(
            {
                "fullUrl": entry["fullUrl"],
                "resource": resource,
                "request": {"method": "PUT", "url": f"{resource['resourceType']}/{resource['id']}"},
            }
        )
    return transaction


def references(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        if isinstance(value.get("reference"), str):
            found.append(value["reference"])
        for child in value.values():
            found += references(child)
    elif isinstance(value, list):
        for child in value:
            found += references(child)
    return found


def _exit_on_sigterm(signum: int, frame: Any) -> None:
    raise SystemExit(128 + signum)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class LocalFhirServer:
    """HAPI FHIR on 127.0.0.1, started on enter and always stopped on exit."""

    def __init__(self, war: Path, pin: FhirServerConfig, *, startup_seconds: float = 300) -> None:
        self.war = war
        self.pin = pin
        self.startup_seconds = startup_seconds
        self.base = ""
        self.process: subprocess.Popen[bytes] | None = None
        self.folder: Path | None = None
        self._previous_sigterm: Any = None

    def __enter__(self) -> LocalFhirServer:
        try:
            return self._start()
        except BaseException:
            # Anything at all going wrong while starting (including Ctrl-C) stops
            # the server: a with-block that never entered never calls __exit__.
            self.__exit__(None, None, None)
            raise

    def _start(self) -> LocalFhirServer:
        from auris.validation.runner import find_java

        self.folder = Path(tempfile.mkdtemp(prefix="auris-fhir-server-"))
        (self.folder / "main.war").symlink_to(self.war.resolve())
        port = _free_port()
        self.base = f"http://127.0.0.1:{port}/fhir"
        command = [
            find_java(),
            f"-Xmx{self.pin.heap_mb}m",
            "--class-path",
            "main.war",
            "-Dloader.path=main.war!/WEB-INF/classes/,main.war!/WEB-INF/",
            "org.springframework.boot.loader.PropertiesLauncher",
            f"--server.port={port}",
            "--server.address=127.0.0.1",
            "--spring.ai.mcp.server.enabled=false",
            "--hapi.fhir.openapi_enabled=false",
            # Valid FHIR ids may be all digits (a record number); HAPI's default refuses them.
            "--hapi.fhir.client_id_strategy=ANY",
            "--hapi.fhir.server_id_strategy=UUID",
        ]
        log = (self.folder / "server.log").open("wb")
        self.process = subprocess.Popen(
            command, cwd=self.folder, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT
        )
        log.close()
        atexit.register(self.__exit__, None, None, None)  # if Python exits without unwinding
        if threading.current_thread() is threading.main_thread():
            # A plain `kill` of Python skips atexit; turn it into an exit that unwinds.
            self._previous_sigterm = signal.signal(signal.SIGTERM, _exit_on_sigterm)
        deadline = time.monotonic() + self.startup_seconds
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("the local FHIR test server stopped while starting")
            try:
                if self.request("GET", "metadata")[0] == 200:
                    return self
            except (OSError, ValueError, http.client.HTTPException):
                pass  # still starting: refused, half-written or not yet JSON
            time.sleep(1)
        raise RuntimeError("the local FHIR test server did not start in time")

    def __exit__(self, *exc: object) -> None:
        try:
            process, self.process = self.process, None
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                except BaseException:  # interrupted while waiting: stop it, then pass it on
                    process.kill()
                    process.wait()
                    raise
        finally:
            try:
                if self.folder is not None:
                    shutil.rmtree(self.folder, ignore_errors=True)
                    self.folder = None
            finally:
                atexit.unregister(self.__exit__)
                if self._previous_sigterm is not None:
                    signal.signal(signal.SIGTERM, self._previous_sigterm)
                    self._previous_sigterm = None

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{self.base}/{path}".rstrip("/"),
            data=data,
            method=method,
            # HAPI reuses cached search results for a minute; counts must be live.
            headers={
                "Accept": "application/fhir+json",
                "Content-Type": "application/fhir+json",
                "Cache-Control": "no-cache",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                return error.code, json.loads(payload or b"{}")
            except ValueError:
                return error.code, {}


def _issues(response: dict) -> list[str]:
    return [
        issue.get("diagnostics", issue.get("code", ""))
        for issue in response.get("issue", [])
        if issue.get("severity") in ("error", "fatal")
    ]


def _sent(transaction: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Each record as the server should store it: urn:uuid references become ids."""
    to_key = {e["fullUrl"]: e["request"]["url"] for e in transaction["entry"]}

    def rewrite(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: (to_key.get(v, v) if k == "reference" and isinstance(v, str) else rewrite(v))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        return value

    return {e["request"]["url"]: rewrite(e["resource"]) for e in transaction["entry"]}


def _content(resource: dict[str, Any]) -> dict[str, Any]:
    """The clinical content: the server's own version metadata and narrative removed."""
    return {k: v for k, v in resource.items() if k not in ("meta", "text")}


def _ordered_statuses(response: dict) -> list[str]:
    return [
        entry.get("response", {}).get("status", "?").split(" ")[0]
        for entry in response.get("entry", [])
    ]


def check_bundle(server: LocalFhirServer, collection: dict[str, Any]) -> dict[str, Any]:
    """Post twice, read everything back, count; the A7 integration proof.

    One server can check many bundles, and bundles may share records (every
    visit names the same clinician). So before the first post each record is
    looked up: new records must be created (201) and counted as added;
    records already there must be updated in place (200) and add nothing.
    Every record read back must hold exactly what was sent (references
    resolved to ids; server metadata aside), and every reference must
    resolve. The second post must answer 200 for every record and add nothing.
    """
    transaction = to_transaction(collection)
    keys = [entry["request"]["url"] for entry in transaction["entry"]]
    sent = _sent(transaction)
    types = sorted({key.split("/")[0] for key in keys})
    report: dict[str, Any] = {"records": len(keys), "types": types}
    errors: list[str] = []

    def counts() -> dict[str, int]:
        result = {}
        for kind in types:
            status, body = server.request("GET", f"{kind}?_summary=count")
            total = body.get("total")
            if status != 200 or not isinstance(total, int):
                errors.append(f"count of {kind} failed (HTTP {status})")
                total = -1
            result[kind] = total
        return result

    present = {key for key in keys if server.request("GET", key)[0] == 200}
    start = counts()
    status, first = server.request("POST", "", transaction)
    first_statuses = _ordered_statuses(first)
    wanted = ["200" if key in present else "201" for key in keys]
    report["first_post"] = {
        "http": status,
        "entries": dict(Counter(first_statuses)),
        "errors": _issues(first),
    }
    if status != 200:
        report["passed"] = False
        return report
    after_first = counts()

    missing, changed, unresolved, checked = [], [], [], 0
    for key in keys:
        status, stored = server.request("GET", key)
        if status != 200:
            missing.append(key)
            continue
        if _content(stored) != _content(sent[key]):
            changed.append(key)
        for reference in references(stored):
            checked += 1
            if server.request("GET", reference)[0] != 200:
                unresolved.append(f"{key} -> {reference}")
    expected_references = sum(len(references(resource)) for resource in sent.values())
    report["read_back"] = {
        "missing": missing,
        "not_as_sent": changed,
        "references_checked": checked,
        "references_sent": expected_references,
        "unresolved": unresolved,
    }

    status, second = server.request("POST", "", transaction)
    second_statuses = _ordered_statuses(second)
    after_second = counts()
    expected = Counter(key.split("/")[0] for key in keys if key not in present)
    expected_first = {kind: expected.get(kind, 0) for kind in types}
    added_first = {kind: after_first[kind] - start[kind] for kind in types}
    added_second = {kind: after_second[kind] - after_first[kind] for kind in types}
    report["second_post"] = {
        "http": status,
        "entries": dict(Counter(second_statuses)),
        "errors": _issues(second),
    }
    report["already_present"] = sorted(present)
    report["counts"] = {
        "expected": expected_first,
        "added_by_first_post": added_first,
        "added_by_second_post": added_second,
    }
    report["errors"] = errors
    report["passed"] = (
        not errors
        and not missing
        and not changed
        and not unresolved
        and checked == expected_references
        and first_statuses == wanted
        and added_first == expected_first
        and status == 200
        and second_statuses == ["200"] * len(keys)
        and not any(added_second.values())
    )
    return report


def write_report(path: Path, report: dict[str, Any]) -> None:
    from auris._storage import write_private_text

    write_private_text(path, json.dumps(report, indent=2) + "\n")
