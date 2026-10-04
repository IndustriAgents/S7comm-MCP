"""The mock PLC: binds the requested host, stops cleanly on SIGTERM, flushes its output."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from support import MOCK_SCRIPT, free_port, start_mock


def load_mock_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("s7_mock_server_under_test", MOCK_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up here
    spec.loader.exec_module(module)
    return module


def non_loopback_ipv4() -> str | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 9))  # TEST-NET-1; UDP connect sends nothing
        except OSError:
            return None
        address = sock.getsockname()[0]
    return None if address.startswith("127.") or address == "0.0.0.0" else address


def test_sigterm_stops_gracefully_and_output_is_flushed() -> None:
    plc = start_mock()
    # SIGTERM only: stdin stays open, so the stdin-EOF watch cannot be what stops it.
    plc.process.send_signal(signal.SIGTERM)
    code = plc.process.wait(timeout=10)
    output = plc.process.stdout.read() if plc.process.stdout else ""  # a pipe, not a terminal
    if plc.process.stdin:
        plc.process.stdin.close()
    assert code == 0
    assert f"[mock-s7] Listening on 127.0.0.1:{plc.port}" in output
    assert "[mock-s7] Shutting down..." in output


def test_host_option_is_applied() -> None:
    address = non_loopback_ipv4()
    if address is None:
        pytest.skip("no non-loopback IPv4 address on this machine")
    plc = start_mock("127.0.0.1")
    try:
        with socket.create_connection(("127.0.0.1", plc.port), timeout=2):
            pass
        with pytest.raises(OSError):
            with socket.create_connection((address, plc.port), timeout=2):
                pass
    finally:
        plc.stop()


def test_start_binds_the_configured_interface() -> None:
    module = load_mock_module()
    calls: list[tuple[Any, ...]] = []

    class FakeServer:
        def register_area(self, *args: Any) -> None:
            pass

        def start_to(self, ip: str, tcp_port: int) -> None:
            calls.append(("start_to", ip, tcp_port))

        def start(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - must not be used
            calls.append(("start", args, kwargs))

        def stop(self) -> None:
            calls.append(("stop",))

        def destroy(self) -> None:
            calls.append(("destroy",))

    async def scenario() -> None:
        server = module.MockS7Server(module.MockConfig(host="127.0.0.1", port=11234, update_interval=0.01))
        server.server = FakeServer()
        await server.start()
        await asyncio.sleep(0.03)
        await server.stop()

    asyncio.run(scenario())
    assert calls == [("start_to", "127.0.0.1", 11234), ("stop",), ("destroy",)]


def test_invalid_host_is_rejected() -> None:
    result = subprocess.run(
        [sys.executable, str(MOCK_SCRIPT), "--host", "localhost.example", "--port", str(free_port())],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "not an IPv4 address" in result.stderr


def test_environment_is_read_at_run_time(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_mock_module()  # imported before the variables are set
    monkeypatch.setenv("MOCK_S7_HOST", "127.0.0.1")
    monkeypatch.setenv("MOCK_S7_PORT", "1234")
    args = module.parse_args([])
    assert (args.host, args.port) == ("127.0.0.1", 1234)
    assert module.MockConfig().host == "0.0.0.0"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_mock_exits_when_the_test_process_is_killed() -> None:
    """Suite rule 11: a killed pytest must not leave a mock PLC behind."""
    tests_dir = str(Path(__file__).resolve().parent)
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; sys.path.insert(0, sys.argv[1]); from support import start_mock; "
            "plc = start_mock(); print(plc.process.pid, plc.port, flush=True); time.sleep(120)",
            tests_dir,
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert parent.stdout
    mock_pid, port = (int(x) for x in parent.stdout.readline().split())
    try:
        parent.kill()  # SIGKILL: no cleanup code in the parent runs
        parent.wait(timeout=10)
        deadline = time.monotonic() + 10
        while _alive(mock_pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _alive(mock_pid), "mock PLC outlived the killed test process"
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
    finally:
        if _alive(mock_pid):
            os.kill(mock_pid, signal.SIGKILL)


def test_exit_on_stdin_eof_stops_gracefully() -> None:
    plc = start_mock()
    assert plc.process.stdin
    plc.process.stdin.close()
    assert plc.process.wait(timeout=10) == 0
    assert "Shutting down" in (plc.process.stdout.read() if plc.process.stdout else "")
