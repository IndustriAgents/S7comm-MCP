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


# S7comm function codes (first parameter byte of a Job PDU).
S7_READ_VAR = 0x04
S7_WRITE_VAR = 0x05
S7_PLC_START = 0x28
S7_PLC_STOP = 0x29


class S7Proxy:
    """A TCP proxy in front of the mock PLC that counts and can sabotage S7 jobs.

    Frames are TPKT (4 bytes, length-prefixed) + COTP DT (3 bytes) + S7. For a
    Job PDU (protocol id 0x32, ROSCTR 1) the function code is at frame[17].
    ``drop_reply_to`` holds function codes whose request is forwarded to the PLC
    but whose reply is swallowed, after which the connection is closed: the
    request was applied, the client never hears back.
    """

    def __init__(self, target_port: int, target_host: str = "127.0.0.1") -> None:
        import threading

        self.target = (target_host, target_port)
        self.jobs: list[int] = []
        self.drop_reply_to: set[int] = set()
        self.drop_once = False
        self._sockets: list[socket.socket] = []
        self._lock = threading.Lock()
        self._listener = socket.socket()
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self.port = self._listener.getsockname()[1]
        self._closed = False
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def count(self, function_code: int) -> int:
        with self._lock:
            return self.jobs.count(function_code)

    def kill_connections(self) -> None:
        """Drop every open connection (a PLC reboot or a cable pull while idle)."""
        with self._lock:
            sockets, self._sockets = self._sockets, []
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def close(self) -> None:
        self._closed = True
        self._listener.close()
        self.kill_connections()

    @staticmethod
    def _read_frame(sock: socket.socket) -> bytes | None:
        header = b""
        while len(header) < 4:
            chunk = sock.recv(4 - len(header))
            if not chunk:
                return None
            header += chunk
        length = int.from_bytes(header[2:4], "big")
        body = b""
        while len(body) < length - 4:
            chunk = sock.recv(length - 4 - len(body))
            if not chunk:
                return None
            body += chunk
        return header + body

    def _accept_loop(self) -> None:
        import threading

        while not self._closed:
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            server = socket.create_connection(self.target)
            with self._lock:
                self._sockets += [client, server]
            state = {"drop_next_reply": False}
            threading.Thread(target=self._pump_requests, args=(client, server, state), daemon=True).start()
            threading.Thread(target=self._pump_replies, args=(server, client, state), daemon=True).start()

    def _pump_requests(self, client: socket.socket, server: socket.socket, state: dict[str, bool]) -> None:
        try:
            while (frame := self._read_frame(client)) is not None:
                if len(frame) > 17 and frame[7] == 0x32 and frame[8] == 0x01:
                    code = frame[17]
                    with self._lock:
                        self.jobs.append(code)
                        if code in self.drop_reply_to:
                            state["drop_next_reply"] = True
                            if self.drop_once:
                                self.drop_reply_to.discard(code)
                server.sendall(frame)
        except OSError:
            pass
        finally:
            server.close()
            client.close()

    def _pump_replies(self, server: socket.socket, client: socket.socket, state: dict[str, bool]) -> None:
        try:
            while (frame := self._read_frame(server)) is not None:
                if state["drop_next_reply"] and len(frame) > 8 and frame[7] == 0x32 and frame[8] == 0x03:
                    # The PLC applied the request; the reply is lost with the link.
                    client.shutdown(socket.SHUT_RDWR)
                    break
                client.sendall(frame)
        except OSError:
            pass
        finally:
            client.close()
            server.close()
