"""Offline transport and subprocess ownership contracts; no real sockets."""

import hashlib
import io
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest
from extract_helpers import completion

from auris.assertions import extraction_json_schema
from auris.config import load_frozen_config
from auris.extract import backend as module
from auris.extract.backend import (
    TESTED_RUNTIME,
    ExtractionError,
    FakeBackend,
    HttpBackend,
    ManagedLlamaServer,
    _NoRedirect,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8000",
        "http://example.org:8000",
        "http://localhost:8000",
        "http://127.0.0.1:8000/path",
        "http://user@127.0.0.1:8000",
        "http://127.0.0.1:8000?x=1",
        "http://127.0.0.1",
        "http://[::1]:8000",
    ],
)
def test_remote_or_ambiguous_hosts_are_refused(url):
    with pytest.raises(ExtractionError, match="127.0.0.1"):
        HttpBackend(url)


def test_http_request_preserves_schema_and_decoding(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://example.org:8888")
    client = HttpBackend("http://127.0.0.1:8000")
    seen = []

    def open_(request, timeout):
        seen.append(json.loads(request.data))
        return io.BytesIO(json.dumps(completion()).encode())

    client.opener.open = open_
    schema = extraction_json_schema()
    result = client.complete(
        [{"role": "user", "content": "fictional"}], schema, temperature=0, max_tokens=1024
    )
    assert result == completion()
    assert seen[0]["response_format"]["json_schema"]["schema"] == schema
    assert seen[0]["response_format"]["json_schema"]["strict"] is True
    assert seen[0]["temperature"] == 0 and seen[0]["max_tokens"] == 1024
    assert seen[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert not any(type(handler).__name__ == "ProxyHandler" for handler in client.opener.handlers)
    with pytest.raises(ExtractionError, match="redirect"):
        _NoRedirect().redirect_request(None, None, 302, "", {}, "http://example.org")


def test_live_tokenizer_failure_never_falls_back_to_estimate():
    client = HttpBackend("http://127.0.0.1:8000")
    client.request = Mock(return_value={"tokens": [1, 2, 3]})
    assert client.tokenize("fictional") == 3
    client.request.assert_called_once_with(
        "/tokenize", {"content": "fictional", "add_special": True, "parse_special": True}
    )
    client.request.side_effect = ExtractionError("unsupported")
    with pytest.raises(ExtractionError, match="unsupported"):
        client.tokenize("fictional")
    assert client.request.call_count == 2


def test_rendered_input_uses_same_chat_settings_as_completion():
    client = HttpBackend("http://127.0.0.1:8000")
    rendered = "<fictional-template>fictional input<assistant>"
    client.request = Mock(
        side_effect=[{"prompt": rendered}, {"tokens": [10, 11, 12]}, completion()]
    )
    messages = [{"role": "user", "content": "fictional input"}]
    schema = extraction_json_schema()
    assert client.count_input_tokens(messages, schema) == 3
    client.complete(messages, schema, temperature=0, max_tokens=4096)
    template, tokenize, complete = client.request.call_args_list
    assert template.args[0] == "/apply-template"
    assert tokenize.args == (
        "/tokenize",
        {"content": rendered, "add_special": True, "parse_special": True},
    )
    assert template.args[1] == {
        key: value
        for key, value in complete.args[1].items()
        if key not in ("temperature", "max_tokens", "stream")
    }


@pytest.mark.parametrize("rendered", [{}, {"prompt": None}, {"prompt": []}, {"prompt": ""}])
def test_invalid_template_response_fails_without_inference(rendered):
    client = HttpBackend("http://127.0.0.1:8000")
    client.request = Mock(return_value=rendered)
    with pytest.raises(ExtractionError, match="chat-template"):
        client.count_input_tokens([], {})
    assert client.request.call_count == 1


@pytest.mark.parametrize("tokens", [None, {}, [True], ["1"]])
def test_invalid_tokenizer_response_fails(tokens):
    client = HttpBackend("http://127.0.0.1:8000")
    client.request = Mock(return_value={"tokens": tokens})
    with pytest.raises(ExtractionError, match="tokenizer"):
        client.tokenize("fictional")


@pytest.mark.parametrize(
    "failure",
    [
        OSError("secret clinical body"),
        HTTPError("http://127.0.0.1:8000", 500, "secret clinical body", {}, None),
    ],
)
def test_transport_errors_do_not_log_clinical_text(failure):
    client = HttpBackend("http://127.0.0.1:8000")
    client.opener.open = Mock(side_effect=failure)
    with pytest.raises(ExtractionError) as exc:
        client.complete([], {}, temperature=0, max_tokens=10)
    assert "secret" not in str(exc.value)


@pytest.fixture
def managed(tmp_path, monkeypatch):
    path = tmp_path / "fictional.gguf"
    path.write_bytes(b"fictional model bytes; not a real model")
    candidate = (
        load_frozen_config()
        .extractor.candidate()
        .model_copy(update={"sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    )
    process = Mock(pid=123, returncode=None)
    process.poll.return_value = None
    monkeypatch.setattr(module, "find_llama_server", lambda: "fictional-llama-server")
    monkeypatch.setattr(module, "free_port", lambda: 12345)
    monkeypatch.setattr(module, "process_rss", lambda pid: 123456)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        Mock(return_value=SimpleNamespace(stdout=TESTED_RUNTIME, stderr="", returncode=0)),
    )
    monkeypatch.setattr(module.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(HttpBackend, "request", Mock(return_value={"chat_template": "fictional"}))
    return ManagedLlamaServer(candidate, path, startup_timeout=0.01), process


@pytest.mark.parametrize("crash", [False, True])
def test_managed_process_terminated_on_success_and_body_crash(managed, crash):
    manager, process = managed
    try:
        with manager as client:
            assert client.runtime.version == TESTED_RUNTIME
            assert client.runtime.chat_template_sha256 == hashlib.sha256(b"fictional").hexdigest()
            if crash:
                raise RuntimeError("simulated completion crash")
    except RuntimeError:
        assert crash
    process.terminate.assert_called_once()
    process.wait.assert_called_once()
    args = module.subprocess.Popen.call_args.args[0]
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[-2:] == ["--reasoning", "off"]
    assert manager.runtime.peak_rss_bytes == 123456


@pytest.mark.parametrize(
    "model_id", ["gemma-4-e4b-it-q4_0", "bonsai-2-9b-q2_0", "bonsai-2-27b-q2_0"]
)
def test_managed_comparators_pass_zero_reasoning_budget(managed, model_id):
    manager, _ = managed
    candidate = load_frozen_config().extractor.candidate(model_id)
    # Only bytes/hash are fictional; use the actual per-model frozen flags.
    manager.candidate = candidate.model_copy(
        update={"sha256": manager.candidate.sha256, "bytes": manager.model_path.stat().st_size}
    )
    with manager:
        pass
    args = module.subprocess.Popen.call_args.args[0]
    assert args[-4:] == ["--reasoning", "off", "--reasoning-budget", "0"]


def test_startup_health_failure_still_terminates(managed, monkeypatch):
    manager, process = managed
    monkeypatch.setattr(HttpBackend, "request", Mock(side_effect=ExtractionError("not ready")))
    with pytest.raises(ExtractionError, match="health wait"):
        with manager:
            pytest.fail("must not enter body")
    process.terminate.assert_called_once()


def test_early_server_crash_is_reaped(managed):
    manager, process = managed
    process.poll.return_value = 1
    with pytest.raises(ExtractionError, match="exited"):
        with manager:
            pytest.fail("must not enter body")
    process.wait.assert_called_once()


def test_stubborn_server_is_killed(managed):
    manager, process = managed
    process.wait.side_effect = [subprocess.TimeoutExpired("fictional", 5), 0]
    with manager:
        pass
    process.kill.assert_called_once()
    assert process.wait.call_count == 2


def test_runtime_mismatch_warns_and_records(managed):
    manager, _ = managed
    module.subprocess.run.return_value.stdout = "version: fictional future build"
    with pytest.warns(RuntimeWarning, match="differs"):
        with manager as client:
            assert client.runtime.warnings


def test_corrupt_model_never_starts_a_server(managed):
    manager, _ = managed
    manager.model_path.write_bytes(b"corrupt")
    with pytest.raises(ExtractionError, match="SHA-256"):
        with manager:
            pass
    module.subprocess.Popen.assert_not_called()


def test_fake_backend_passes_through_exact_schema():
    fake = FakeBackend([completion()])
    schema = extraction_json_schema()
    fake.complete([], schema, temperature=0, max_tokens=100)
    assert fake.calls[0]["schema"] is schema


def test_exit_during_terminate_is_still_reaped(managed):
    manager, process = managed
    process.terminate.side_effect = ProcessLookupError()
    with manager:
        pass
    process.wait.assert_called_once()


def test_version_probe_timeout_is_clear_and_has_no_server(managed):
    manager, _ = managed
    module.subprocess.run.side_effect = subprocess.TimeoutExpired("fictional", 10)
    with pytest.raises(ExtractionError, match="version probe timed out"):
        with manager:
            pass
    module.subprocess.Popen.assert_not_called()
