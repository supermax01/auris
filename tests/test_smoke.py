"""Smoke tests for the Auris scaffold (Slice 1, Step 0)."""

import socket

import pytest
from pytest_socket import SocketBlockedError

import auris
from auris.cli import main


def test_package_version() -> None:
    assert auris.__version__ == "0.0.1"


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "0.0.1" in captured.out


def test_cli_demo_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    result = main(["demo"])
    assert result == 2
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "not implemented yet (Slice 1)" in combined


def test_sockets_are_blocked() -> None:
    with pytest.raises(SocketBlockedError):
        sock = socket.socket()
        try:
            sock.connect(("127.0.0.1", 80))
        finally:
            sock.close()
