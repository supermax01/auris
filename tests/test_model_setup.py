"""Model setup never fetches implicitly; all download tests are local fakes."""

import hashlib

import pytest

from auris.cli import main
from auris.config import load_frozen_config
from auris.setup.model import setup_model


def test_setup_model_requires_opt_in_and_verifies_download(tmp_path):
    payload = b"fictional model test bytes, not weights"
    candidate = (
        load_frozen_config()
        .extractor.candidate()
        .model_copy(
            update={
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        )
    )
    calls = []

    def fetch(url, destination):
        calls.append(url)
        destination.write_bytes(payload)

    with pytest.raises(ValueError, match="auris setup model .* --fetch"):
        setup_model(candidate, tmp_path, fetcher=fetch)
    assert calls == []
    path = setup_model(candidate, tmp_path, fetch=True, fetcher=fetch)
    assert path.read_bytes() == payload and len(calls) == 1
    assert f"/{candidate.repo}/resolve/{candidate.revision}/" in calls[0]
    assert setup_model(candidate, tmp_path, fetch=True, fetcher=fetch) == path
    assert len(calls) == 1
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="size mismatch"):
        setup_model(candidate, tmp_path, fetch=True, fetcher=fetch)
    assert len(calls) == 1  # Existing corrupt artifacts are never overwritten.


def test_bad_download_is_not_promoted(tmp_path):
    candidate = load_frozen_config().extractor.candidate().model_copy(update={"bytes": None})
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        setup_model(candidate, tmp_path, fetch=True, fetcher=lambda url, p: p.write_bytes(b"bad"))
    assert list(tmp_path.iterdir()) == []


def test_model_setup_help_and_missing_model_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AURIS_CACHE", str(tmp_path))
    assert main(["setup", "model"]) == 1
    assert "auris setup model --model qwen3-8b-q4_k_m --fetch" in capsys.readouterr().err
    with pytest.raises(SystemExit) as result:
        main(["setup", "model", "--help"])
    assert result.value.code == 0
    assert "--fetch" in capsys.readouterr().out
