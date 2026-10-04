"""Shared fixtures: a mock PLC subprocess, free ports and a clean environment.

The mock PLC is ``s7comm-mock-server/s7_mock_server.py`` from this repository,
run with the server's own interpreter (both projects lock python-snap7 2.0.2),
so the suite needs no second virtualenv. Each test module gets a fresh mock on
a free loopback port, and every process a fixture starts is stopped again.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator

import pytest
from support import MockPLC, start_mock

#: Every variable the server reads; cleared so the developer's shell cannot leak in.
SERVER_ENV_VARS = (
    "S7_HOST",
    "S7_PORT",
    "S7_RACK",
    "S7_SLOT",
    "S7_CONNECTION_TYPE",
    "S7_TIMEOUT",
    "S7_MAX_RETRIES",
    "S7_RETRY_BACKOFF_BASE",
    "S7_DEBUG",
    "S7_WRITES_ENABLED",
    "S7_SYSTEM_CMDS_ENABLED",
    "TAG_MAP_FILE",
)


@pytest.fixture(scope="module")
def mock_plc() -> Iterator[MockPLC]:
    """A fresh mock PLC per test module (fast update interval)."""
    plc = start_mock("127.0.0.1", None, "--update-interval", "0.2")
    try:
        yield plc
    finally:
        plc.stop()


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in SERVER_ENV_VARS:
        # setenv first so the original state (usually "unset") is restored at
        # teardown even if code under test writes os.environ directly.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    return monkeypatch


@pytest.fixture
def silent_tcp_server() -> Iterator[int]:
    """A TCP listener that accepts connections and never answers (a hung PLC)."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    listener.settimeout(0.1)
    accepted: list[socket.socket] = []
    stop = False

    def _accept() -> None:
        while not stop:
            try:
                conn, _ = listener.accept()
                accepted.append(conn)
            except OSError:
                continue

    thread = threading.Thread(target=_accept, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1]
    finally:
        stop = True
        thread.join(timeout=2)
        listener.close()
        for conn in accepted:
            conn.close()
