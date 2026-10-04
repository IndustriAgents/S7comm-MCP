"""The mock PLC: binds the requested host, stops cleanly on SIGTERM, flushes its output."""

from __future__ import annotations

import asyncio
import importlib.util
import socket
import subprocess
import sys
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
    code, output = plc.stop()  # SIGTERM; stdout is a pipe, not a terminal
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
