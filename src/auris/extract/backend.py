"""Loopback-only constrained chat transport and an owned llama-server process.

No proxies, redirects, downloads, or transcript-bearing application logs.
The response schema is passed through unchanged. Tests can replace the entire
transport with FakeBackend without opening a socket or starting a process.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from auris.config import ExtractorCandidate
from auris.setup.fhir_packages import sha256_file

TESTED_RUNTIME = "version: 0.9.0 (build 12110, commit cd959bae9)"
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class ExtractionError(ValueError):
    """A safe-to-log failure reason; never includes clinical response text."""


@dataclass
class RuntimeMetadata:
    version: str | None = None
    startup_seconds: float | None = None
    warnings: list[str] = field(default_factory=list)
    chat_template_sha256: str | None = None
    peak_rss_bytes: int | None = None
    rss_method: str = "sampled process RSS; excludes unreported GPU allocations"


class Backend(Protocol):
    runtime: RuntimeMetadata

    def complete(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        *,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]: ...

    def tokenize(self, text: str) -> int | None: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise ExtractionError("local server redirect refused")


class HttpBackend:
    def __init__(
        self, base_url: str, *, timeout: float = 180, runtime: RuntimeMetadata | None = None
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ExtractionError("extractor URL must be http://127.0.0.1:<port>")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.runtime = runtime or RuntimeMetadata()
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())
        self._tokenizer_available: bool | None = None

    def request(
        self, path: str, payload: dict[str, Any] | None = None, *, timeout: float | None = None
    ) -> dict[str, Any]:
        if path not in ("/health", "/props", "/tokenize", "/v1/chat/completions"):
            raise ExtractionError("unsupported local extractor endpoint")
        data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
        request = Request(
            self.base_url + path, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with self.opener.open(request, timeout=timeout or self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ExtractionError("extractor response exceeds size limit")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ExtractionError("extractor response is not an object")
            return result
        except HTTPError as exc:
            # Do not include server diagnostics: they may echo the transcript.
            raise ExtractionError(f"local extractor HTTP {exc.code}") from None
        except (OSError, URLError, ValueError) as exc:
            if isinstance(exc, ExtractionError):
                raise
            raise ExtractionError("local extractor connection or JSON response failed") from None

    def complete(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        *,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        return self.request(
            "/v1/chat/completions",
            {
                "messages": messages,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "ExtractionResult", "strict": True, "schema": schema},
                },
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )

    def tokenize(self, text: str) -> int | None:
        if self._tokenizer_available is False:
            return None
        try:
            result = self.request("/tokenize", {"content": text, "add_special": True})
            tokens = result["tokens"]
            if not isinstance(tokens, list) or any(type(t) is not int for t in tokens):
                raise ValueError("unexpected tokenizer response")
        except (ExtractionError, KeyError, ValueError):
            self._tokenizer_available = False
            return None
        self._tokenizer_available = True
        return len(tokens)


def find_llama_server() -> str:
    resolved = shutil.which(os.environ.get("AURIS_LLAMA_SERVER", "llama-server"))
    if resolved is None:
        raise ExtractionError(
            "llama-server unavailable; set AURIS_LLAMA_SERVER or PATH, then run auris setup model"
        )
    return resolved


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def process_rss(pid: int) -> int | None:
    """Portable POSIX RSS sampling, in bytes. Not whole-pipeline peak memory."""
    try:
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        return int(result.stdout.strip()) * 1024 if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


class ManagedLlamaServer:
    """Own one process; release it on startup errors, body errors and success.

    The OS-selected port has a bind/start race; a collision fails health/startup
    rather than retrying model inference. All flags except the executable and
    local model file are fixed or validated frozen config values.
    """

    def __init__(
        self,
        candidate: ExtractorCandidate,
        model_path: Path,
        *,
        n_ctx: int = 8192,
        startup_timeout: float = 60,
        request_timeout: float = 180,
    ) -> None:
        self.candidate = candidate
        self.model_path = model_path
        self.n_ctx = n_ctx
        self.startup_timeout = startup_timeout
        self.request_timeout = request_timeout
        self.process: subprocess.Popen | None = None
        self.runtime = RuntimeMetadata()
        self._stop_sampling = threading.Event()
        self._sampler: threading.Thread | None = None

    def __enter__(self) -> HttpBackend:
        started = time.monotonic()
        try:
            if not self.model_path.is_file():
                raise ExtractionError(
                    "pinned extractor model missing; run auris setup model "
                    f"--model {self.candidate.id} --fetch"
                )
            if self.candidate.bytes and self.model_path.stat().st_size != self.candidate.bytes:
                raise ExtractionError("extractor model size mismatch")
            if sha256_file(self.model_path) != self.candidate.sha256:
                raise ExtractionError("extractor model SHA-256 mismatch")
            binary = find_llama_server()
            version = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, timeout=10, check=False
            )
            self.runtime.version = (version.stdout + version.stderr).strip()
            if TESTED_RUNTIME not in self.runtime.version or version.returncode:
                message = "llama-server runtime differs from tested 0.9.0 build 12110 cd959bae9"
                self.runtime.warnings.append(message)
                warnings.warn(message, RuntimeWarning, stacklevel=2)
            port = free_port()
            self.process = subprocess.Popen(
                [
                    binary,
                    "-m",
                    str(self.model_path),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "-c",
                    str(self.n_ctx),
                    "-ngl",
                    "99",
                    "--no-webui",
                    *self.candidate.runtime_flags,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._sampler = threading.Thread(target=self._sample, daemon=True)
            self._sampler.start()
            client = HttpBackend(
                f"http://127.0.0.1:{port}", timeout=self.request_timeout, runtime=self.runtime
            )
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise ExtractionError("llama-server exited before becoming healthy")
                try:
                    client.request(
                        "/health", timeout=min(1, max(0.01, deadline - time.monotonic()))
                    )
                    break
                except ExtractionError:
                    time.sleep(0.05)
            else:
                raise ExtractionError("llama-server health wait timed out")
            try:
                template = client.request("/props", timeout=2).get("chat_template")
                if isinstance(template, str):
                    self.runtime.chat_template_sha256 = hashlib.sha256(
                        template.encode()
                    ).hexdigest()
            except ExtractionError:
                pass
            if self.runtime.chat_template_sha256 is None:
                self.runtime.warnings.append("server chat-template hash unavailable from /props")
            self.runtime.startup_seconds = time.monotonic() - started
            return client
        except subprocess.TimeoutExpired:
            self.close()
            raise ExtractionError("llama-server version probe timed out") from None
        except BaseException:
            self.close()
            raise

    def _sample(self) -> None:
        assert self.process is not None
        while True:
            rss = process_rss(self.process.pid)
            if rss is not None:
                self.runtime.peak_rss_bytes = max(self.runtime.peak_rss_bytes or 0, rss)
            if self._stop_sampling.wait(0.1):
                break

    def close(self) -> None:
        self._stop_sampling.set()
        try:
            if self.process is not None:
                if self.process.poll() is None:
                    try:
                        self.process.terminate()
                    except ProcessLookupError:
                        pass  # Exited between poll and terminate; still reap it.
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            self.process.kill()
                        except ProcessLookupError:
                            pass
                        self.process.wait(timeout=5)
                else:
                    self.process.wait()
        finally:
            if self._sampler is not None:
                self._sampler.join(timeout=2)

    def __exit__(self, *_: object) -> None:
        self.close()


class FakeBackend:
    def __init__(
        self, responses: Sequence[dict[str, Any]], *, tokenizer: Callable[[str], int] | None = None
    ) -> None:
        self.responses = iter(responses)
        self.tokenizer = tokenizer
        self.calls: list[dict[str, Any]] = []
        self.runtime = RuntimeMetadata(version="fake (offline test)")

    def complete(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        *,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        self.calls.append(
            dict(messages=messages, schema=schema, temperature=temperature, max_tokens=max_tokens)
        )
        try:
            return next(self.responses)
        except StopIteration:
            raise ExtractionError("fake backend has no response") from None

    def tokenize(self, text: str) -> int | None:
        return self.tokenizer(text) if self.tokenizer else None
