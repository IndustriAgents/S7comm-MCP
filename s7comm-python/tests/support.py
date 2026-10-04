"""Helpers shared by the tests: free ports, the mock PLC process, tool calls."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
MOCK_SCRIPT = REPO_ROOT / "s7comm-mock-server" / "s7_mock_server.py"
SERVER_ROOT = REPO_ROOT / "s7comm-python"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_port(host: str, port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"nothing listening on {host}:{port} after {timeout}s")


@dataclass
class MockPLC:
    host: str
    port: int
    process: subprocess.Popen[str]

    def stop(self) -> tuple[int, str]:
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        output = self.process.stdout.read() if self.process.stdout else ""
        return self.process.returncode, output


def start_mock(host: str = "127.0.0.1", port: int | None = None, *extra: str) -> MockPLC:
    port = port or free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("MOCK_S7_")}
    process = subprocess.Popen(
        [sys.executable, str(MOCK_SCRIPT), "--host", host, "--port", str(port), *extra],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    try:
        wait_for_port(host, port)
    except TimeoutError:
        process.kill()
        output = process.communicate(timeout=5)[0]
        raise RuntimeError(f"mock PLC did not start:\n{output}") from None
    return MockPLC(host, port, process)


async def call(server: Any, tool: str, /, **arguments: Any) -> dict[str, Any]:
    """Call a tool in-process through FastMCP and return its structured envelope."""
    result = await server._mcp.call_tool(tool, arguments)
    _content, structured = result
    return structured
