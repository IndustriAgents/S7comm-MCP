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
    "S7_REQUEST_DEADLINE",
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


@pytest.fixture
def garbage_tcp_server() -> Iterator[int]:
    """A TCP peer that accepts connections and answers everything with garbage."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    listener.settimeout(0.1)
    stop = False
    connections: list[socket.socket] = []

    def _serve(conn: socket.socket) -> None:
        conn.settimeout(0.2)
        while not stop:
            try:
                if not conn.recv(4096):
                    return
                conn.sendall(b"\xde\xad\xbe\xef" * 8)
            except TimeoutError:
                continue
            except OSError:
                return

    def _accept() -> None:
        while not stop:
            try:
                conn, _ = listener.accept()
            except OSError:
                continue
            connections.append(conn)
            threading.Thread(target=_serve, args=(conn,), daemon=True).start()

    thread = threading.Thread(target=_accept, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1]
    finally:
        stop = True
        thread.join(timeout=2)
        listener.close()
        for conn in connections:
            conn.close()


@pytest.fixture(scope="session")
def _canonical_output_validators():
    """Read the reviewed schema once; exercise it with real MCP responses below."""
    import json
    from pathlib import Path

    from jsonschema import Draft202012Validator

    contract = json.loads((Path(__file__).resolve().parents[2] / "contract/tools.json").read_text())
    return {tool["name"]: Draft202012Validator(tool["outputSchema"]) for tool in contract["tools"]}


@pytest.fixture(autouse=True)
def _validate_canonical_wire_results(monkeypatch, _canonical_output_validators):
    """Validate success, error and partial results from real stdio integration calls.

    Hidden legacy aliases can have their own compatibility result format, so this
    enforces the output guarantee only for the advertised canonical tool names.
    """
    import json

    from mcp import ClientSession

    original = ClientSession.call_tool

    async def checked(session, name, *args, **kwargs):
        result = await original(session, name, *args, **kwargs)
        validator = _canonical_output_validators.get(name)
        if validator is not None:
            value = result.structuredContent
            assert isinstance(value, dict), f"{name}: missing structured result"
            validator.validate(value)
            assert result.isError == (value["success"] is False), name
            assert json.loads(result.content[0].text) == value, name
        return result

    monkeypatch.setattr(ClientSession, "call_tool", checked)
