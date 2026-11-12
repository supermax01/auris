"""One offline JVM for both passes of every resource in a run; no warm wrapper."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from auris._storage import write_private_text
from auris.config import FrozenConfig
from auris.setup.fhir_packages import FhirPackageError
from auris.setup.validator import verify_jar, verify_seed
from auris.validation.models import ValidationRun
from auris.validation.outcomes import classify, parse_outcomes


def find_java() -> str:
    """Explicit overrides are authoritative; a broken override is an error."""
    candidate = os.environ.get("AURIS_JAVA")
    if not candidate and os.environ.get("JAVA_HOME"):
        candidate = str(Path(os.environ["JAVA_HOME"]) / "bin" / "java")
    resolved = shutil.which(candidate or "java")
    if not resolved:
        raise FileNotFoundError(
            "Java unavailable; set AURIS_JAVA or JAVA_HOME, or add java to PATH"
        )
    return str(Path(resolved).absolute())


def _without_profiles(resource: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(resource)

    def remove(value: Any) -> None:
        if isinstance(value, dict):
            if "resourceType" in value and "meta" in value:
                value["meta"].pop("profile", None)
                if not value["meta"]:
                    value.pop("meta")
            for child in value.values():
                remove(child)
        elif isinstance(value, list):
            for child in value:
                remove(child)

    remove(result)
    return result


class ValidatorRunner:
    def __init__(
        self,
        config: FrozenConfig,
        cache: Path,
        *,
        timeout_seconds: float = 180,
        allow_example_urls: bool = False,
    ) -> None:
        self.config = config
        self.cache = cache.resolve()
        self.timeout_seconds = timeout_seconds
        self.allow_example_urls = allow_example_urls

    def preflight(self) -> tuple[str, Path]:
        java = find_java()
        jar = verify_jar(self.config.validator, self.cache)
        verify_seed(self.config, self.cache / "home")
        return java, jar

    def validate(self, resources: Sequence[dict[str, Any]]) -> ValidationRun:
        resources = copy.deepcopy(tuple(resources))
        keys = [(r["resourceType"], r["id"]) for r in resources]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate resource identity in validator inputs")
        if not resources:
            return ValidationRun(resources=(), wall_seconds=0)
        started = time.monotonic()
        command: tuple[str, ...] = ()
        stdout, stderr, raw = "", "", None
        returncode = None
        failure = None
        outcomes = {}
        try:
            java, jar = self.preflight()
            # TemporaryDirectory is 0700. Inputs, output and logs can contain
            # clinical data; no resource text is emitted to application logs.
            with tempfile.TemporaryDirectory(prefix="run-", dir=self.cache) as temporary:
                work = Path(temporary)
                names = []
                for index, resource in enumerate(resources):
                    for scope, value in (
                        ("base", _without_profiles(resource)),
                        ("profile", resource),
                    ):
                        name = f"{index:04d}-{scope}.json"
                        write_private_text(work / name, json.dumps(value, allow_nan=False))
                        names.append(name)
                write_private_text(work / "outcomes.json", "")
                command = (
                    java,
                    f"-Xmx{self.config.jvm.heap_gb}g",
                    f"-Duser.home={self.cache / 'home'}",
                    "-jar",
                    str(jar),
                    *names,
                    "-version",
                    self.config.fhir.version,
                    "-ig",
                    f"{self.config.fhir.us_core_package_id}#{self.config.fhir.us_core_version}",
                    "-tx",
                    "n/a",
                    "-txCache",
                    "n/a",
                    "-no-http-access",
                    "-disable-default-resource-fetcher",
                    "-locale",
                    "en-US",
                    "-output",
                    "outcomes.json",
                )
                if self.allow_example_urls:
                    command += ("-allow-example-urls", "true")
                # Do not inherit JVM flags that can redirect caches/settings.
                env = {
                    k: v
                    for k, v in os.environ.items()
                    if k not in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS")
                }
                try:
                    process = subprocess.run(
                        command,
                        cwd=work,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=self.timeout_seconds,
                        check=False,
                    )
                finally:
                    # Preserve even partial output before the private workdir is removed.
                    raw = (work / "outcomes.json").read_text()
                stdout, stderr, returncode = process.stdout, process.stderr, process.returncode
                if returncode not in (0, 1):
                    raise ValueError(f"validator exited with status {returncode}")
                outcomes = parse_outcomes(raw)
                if set(outcomes) != set(names):
                    raise ValueError("validator returned an incomplete or unexpected input set")
                issues = [i for group in outcomes.values() for i in group]
                if any(
                    i.code in ("exception", "timeout")
                    or i.message_id == "VALIDATION_TIMEOUT_EXCEEDED"
                    for i in issues
                ):
                    raise ValueError("validator reported an exception or timeout")
                if returncode == 1 and not any(i.severity in ("fatal", "error") for i in issues):
                    raise ValueError("validator exited unsuccessfully without validation errors")
        except subprocess.TimeoutExpired as exc:
            failure = f"validator timed out after {self.timeout_seconds:g} seconds"
            stdout = _text(exc.stdout)
            stderr = _text(exc.stderr)
        except (OSError, ValueError, KeyError, TypeError, FhirPackageError) as exc:
            failure = f"validator unavailable: {exc}"
        return ValidationRun(
            resources=tuple(
                classify(
                    resource,
                    outcomes.get(f"{index:04d}-base.json", ()),
                    outcomes.get(f"{index:04d}-profile.json", ()),
                    failure=failure,
                )
                for index, resource in enumerate(resources)
            ),
            wall_seconds=time.monotonic() - started,
            command=command,
            returncode=returncode,
            failure=failure,
            raw_output=raw,
            stdout=stdout,
            stderr=stderr,
        )


def _text(value: str | bytes | None) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
