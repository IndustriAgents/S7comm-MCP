"""End to end: the real ``s7comm-mcp`` process over stdio, driven by the MCP client SDK.

Follows ``s7comm-mock-server/TEST_SCENARIOS.md`` against the mock PLC, then
checks the things only a real process shows: startup without a PLC, and that
stdout carries nothing but JSON-RPC.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import inspect
import json
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from support import S7_PLC_STOP, S7_READ_VAR, S7_WRITE_VAR, SERVER_ROOT, MockPLC, S7Proxy, free_port

SERVER_COMMAND = [sys.executable, "-m", "s7comm_mcp.cli"]


def server_env(port: int, **overrides: str) -> dict[str, str]:
    # Every setting is explicit, so neither the shell nor a stray .env can change it.
    env = {
        "S7_HOST": "127.0.0.1",
        "S7_PORT": str(port),
        "S7_RACK": "0",
        "S7_SLOT": "2",
        "S7_TIMEOUT": "2",
        "S7_MAX_RETRIES": "1",
        "S7_RETRY_BACKOFF_BASE": "0.1",
        "S7_WRITES_ENABLED": "false",
        "S7_SYSTEM_CMDS_ENABLED": "false",
        "TAG_MAP_FILE": "",
    }
    env.update(overrides)
    return env


class Session:
    def __init__(self, session: ClientSession) -> None:
        self.session = session

    async def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        result = await self.session.call_tool(tool, arguments)
        assert not result.isError, result
        envelope = result.structuredContent
        assert envelope is not None
        assert set(envelope) == {"success", "data", "error", "meta"}
        assert json.loads(result.content[0].text) == envelope  # text and structured agree
        return envelope


@asynccontextmanager
async def mcp_session(env: dict[str, str], errlog: Any = None) -> AsyncIterator[Session]:
    params = StdioServerParameters(command=SERVER_COMMAND[0], args=SERVER_COMMAND[1:], env=env, cwd=SERVER_ROOT)
    async with stdio_client(params, errlog=errlog or sys.stderr) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield Session(session)


async def test_server_info_reports_the_package_version(mock_plc: MockPLC) -> None:
    params = StdioServerParameters(
        command=SERVER_COMMAND[0], args=SERVER_COMMAND[1:], env=server_env(mock_plc.port), cwd=SERVER_ROOT
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
    assert init.serverInfo.name == "S7comm MCP Server"
    assert init.serverInfo.version == importlib.metadata.version("s7comm-mcp")
    assert init.serverInfo.version != importlib.metadata.version("mcp")


async def test_tools_list_has_a_description_for_every_tool(mock_plc: MockPLC) -> None:
    async with mcp_session(server_env(mock_plc.port)) as client:
        listed = await client.session.list_tools()
    names = {tool.name for tool in listed.tools}
    assert len(names) == 20
    for tool in listed.tools:
        assert tool.description and len(tool.description) > 20, tool.name
        assert tool.description == inspect.cleandoc(tool.description)  # dedented docstring
    for name in ("write_db", "write_output", "write_marker", "write_multiple_vars", "write_tag"):
        description = next(tool.description for tool in listed.tools if tool.name == name)
        assert "S7_WRITES_ENABLED" in description


WRITE_TOOLS = {"write_db", "write_output", "write_marker", "write_multiple_vars", "write_tag", "set_cpu_state"}


async def test_tool_annotations_never_invite_retries_of_writes(mock_plc: MockPLC) -> None:
    async with mcp_session(server_env(mock_plc.port)) as client:
        listed = await client.session.list_tools()
    for tool in listed.tools:
        hints = tool.annotations
        assert hints is not None, tool.name
        if tool.name in WRITE_TOOLS:
            assert (hints.readOnlyHint, hints.destructiveHint, hints.idempotentHint) == (False, True, False), tool.name
        else:
            assert hints.readOnlyHint is True and hints.destructiveHint is False, tool.name


async def test_scenarios_read_only(mock_plc: MockPLC, tmp_path: Path) -> None:
    tags = tmp_path / "tags.json"
    tags.write_text(
        json.dumps(
            {
                "MotorSpeed": {
                    "area": "db",
                    "db_number": 1,
                    "offset": 0,
                    "data_type": "REAL",
                    "description": "Motor speed",
                },
                "AlarmActive": {"area": "db", "db_number": 2, "offset": 0, "data_type": "BOOL", "bit": 0},
            }
        )
    )
    async with mcp_session(server_env(mock_plc.port, TAG_MAP_FILE=str(tags))) as c:
        # 1. Basic connection & system info
        info = await c.call("read_plc_info")
        assert info["data"]["module_type"] == "CPU 315-2 PN/DP" and info["data"]["version"] == "V3.2.6"
        assert (await c.call("get_connection_status"))["data"]["connected"] is True
        assert (await c.call("read_cpu_state"))["data"]["state"] == "S7CpuStatusRun"
        assert (await c.call("read_system_time"))["success"] is True
        ping = await c.call("ping")
        assert ping["success"] and ping["data"]["writes_enabled"] is False and ping["data"]["tag_map"]["count"] == 2

        # 2. Telemetry
        speed = await c.call("read_db_typed", db_number=1, start_offset=0, data_type="REAL")
        assert 1440 <= speed["data"]["value"] <= 1460
        assert (await c.call("read_db_typed", db_number=1, start_offset=4, data_type="REAL"))["data"]["value"] == 42.0
        assert (await c.call("read_db_typed", db_number=1, start_offset=8, data_type="REAL"))["data"]["value"] == 22.5
        assert (await c.call("read_db_typed", db_number=1, start_offset=12, data_type="DINT"))["data"][
            "value"
        ] == 12345678
        temp = await c.call("read_db_typed", db_number=1, start_offset=16, data_type="REAL")
        assert 65.5 <= temp["data"]["value"] <= 70.5
        raw = await c.call("read_db", db_number=1, start_offset=0, size=20)
        assert raw["data"]["byte_count"] == 20
        assert raw["data"]["raw_bytes_hex"][8:16] == "42280000" and raw["data"]["raw_bytes_hex"][24:32] == "00bc614e"

        # 3. Alarms & status
        alarm = await c.call("read_db_typed", db_number=2, start_offset=0, data_type="BOOL", bit_index=0)
        assert alarm["data"]["value"] is True
        status = await c.call("read_db_typed", db_number=2, start_offset=2, data_type="STRING", string_length=30)
        assert status["data"]["value"] == "OK"

        # 4. Inputs & outputs, 8. markers
        assert (await c.call("read_input", start_byte=0, size=1))["success"] is True
        output = await c.call("read_output", start_byte=0, size=1)
        assert int(output["data"]["raw_bytes_hex"], 16) & 1 == 0
        marker = await c.call("read_marker", start_byte=0, size=1)
        assert int(marker["data"]["raw_bytes_hex"], 16) & 1 == 1

        # 5.1 is refused while writes are disabled (the default)
        refused = await c.call("write_db", db_number=1, start_offset=8, value=25.0, data_type="REAL")
        assert refused["success"] is False and "S7_WRITES_ENABLED" in refused["error"]

        # 6. Dynamic values change
        first = speed["data"]["value"]
        for _ in range(30):
            await asyncio.sleep(0.2)
            again = await c.call("read_db_typed", db_number=1, start_offset=0, data_type="REAL")
            if again["data"]["value"] != first:
                break
        assert again["data"]["value"] != first

        # 7.1 Batch read
        batch = await c.call(
            "read_multiple_vars",
            variables=[
                {"area": "db", "db_number": 1, "offset": 4, "data_type": "REAL"},
                {"area": "db", "db_number": 1, "start_offset": 8, "data_type": "REAL"},
            ],
        )
        assert [r["data"]["value"] for r in batch["data"]["results"]] == [42.0, 22.5]

        # 9. Error handling: errors are reported and the session keeps working
        invalid = await c.call("read_db_typed", db_number=1, start_offset=0, data_type="INVALID_TYPE")
        assert invalid["success"] is False and "Unsupported data_type 'INVALID_TYPE'" in invalid["error"]
        missing = await c.call("read_db", db_number=999, start_offset=0, size=10)
        assert missing["success"] is False and "CPU : Item not available" in missing["error"]
        out_of_range = await c.call("read_db", db_number=1, start_offset=1000, size=10)
        assert out_of_range["success"] is False and "CPU : Address out of range" in out_of_range["error"]
        bad_type = await c.call("read_db", db_number="DB1", start_offset=0, size=1)
        assert bad_type["success"] is False and bad_type["error"].startswith("Invalid arguments")
        assert (await c.call("read_db_typed", db_number=1, start_offset=4, data_type="REAL"))["data"]["value"] == 42.0

        # SZL (formerly a serialisation error)
        szl = await c.call("read_szl", szl_id=0x0011, szl_index=0)
        assert szl["success"] is True and szl["data"]["record_count"] == 4

        # 10.1-10.2 Tags
        listed = await c.call("list_tags")
        assert {tag["name"] for tag in listed["data"]["tags"]} == {"MotorSpeed", "AlarmActive"}
        assert 1440 <= (await c.call("read_tag", name="MotorSpeed"))["data"]["value"] <= 1460


async def test_scenarios_with_writes_enabled(mock_plc: MockPLC, tmp_path: Path) -> None:
    tags = tmp_path / "tags.json"
    tags.write_text(json.dumps({"MotorSpeed": {"area": "db", "db_number": 1, "offset": 0, "data_type": "REAL"}}))
    env = server_env(mock_plc.port, S7_WRITES_ENABLED="true", TAG_MAP_FILE=str(tags))
    async with mcp_session(env) as c:
        # 5. Writing values
        write = await c.call("write_db", db_number=1, start_offset=8, value=25.0, data_type="REAL")
        assert write["success"] is True and write["data"]["written_bytes"] == 4
        assert (await c.call("read_db_typed", db_number=1, start_offset=8, data_type="REAL"))["data"]["value"] == 25.0

        # Pump command: only Q0.0 changes, Q0.1-Q0.7 keep their state
        assert (await c.call("write_output", start_byte=0, value="06"))["success"]
        pump = await c.call("write_output", start_byte=0, value=True, data_type="BOOL", bit_index=0)
        assert pump["success"] is True and pump["data"]["written_bits"] == 1
        assert (await c.call("read_output", start_byte=0, size=1))["data"]["raw_bytes_hex"] == "07"

        # 7.2 Batch write
        batch = await c.call(
            "write_multiple_vars",
            variables=[
                {"area": "db", "db_number": 1, "offset": 8, "value": 30.0, "data_type": "REAL"},
                {"area": "output", "start_byte": 0, "value": False, "data_type": "BOOL", "bit_index": 1},
            ],
        )
        assert batch["success"] is True, batch
        assert (await c.call("read_db_typed", db_number=1, start_offset=8, data_type="REAL"))["data"]["value"] == 30.0
        assert (await c.call("read_output", start_byte=0, size=1))["data"]["raw_bytes_hex"] == "05"

        # STRING write keeps the S7 header
        text = await c.call(
            "write_db", db_number=2, start_offset=2, value="RUNNING", data_type="STRING", string_length=30
        )
        assert text["success"] is True
        back = await c.call("read_db_typed", db_number=2, start_offset=2, data_type="STRING")
        assert (back["data"]["value"], back["data"]["max_length"]) == ("RUNNING", 30)

        # 10.3 Write tag
        tag = await c.call("write_tag", name="MotorSpeed", value=1500.0)
        assert tag["success"] is True and tag["data"]["written_bytes"] == 4

        # Restore what the read-only scenarios expect
        assert (await c.call("write_db", db_number=1, start_offset=8, value=22.5, data_type="REAL"))["success"]
        assert (await c.call("write_output", start_byte=0, value="00"))["success"]
        assert (
            await c.call("write_db", db_number=2, start_offset=2, value="OK", data_type="STRING", string_length=30)
        )["success"]


async def test_server_starts_and_lists_tools_without_a_plc(tmp_path: Path) -> None:
    # Bug 8: the lifespan connected at startup, so the process exited before initialize.
    port = free_port()  # nothing listens here
    errlog = (tmp_path / "stderr.log").open("w")
    try:
        async with mcp_session(server_env(port, S7_MAX_RETRIES="0"), errlog=errlog) as c:
            listed = await c.session.list_tools()
            assert len(listed.tools) == 20
            started = time.monotonic()
            result = await c.call("read_db", db_number=1, start_offset=0, size=1)
            assert result["success"] is False and "Cannot reach" in result["error"]
            assert time.monotonic() - started < 10
            status = await c.call("get_connection_status")
            assert status["data"]["connected"] is False and "Cannot reach" in status["data"]["last_error"]
            ping = await c.call("ping")
            assert ping["success"] is False
    finally:
        errlog.close()


#: One call per tool (all 20), with arguments that succeed against the mock.
EVERY_TOOL_CALL: dict[str, dict[str, Any]] = {
    "read_db": {"db_number": 1, "start_offset": 0, "size": 4},
    "read_db_typed": {"db_number": 1, "start_offset": 4, "data_type": "REAL"},
    "write_db": {"db_number": 1, "start_offset": 220, "value": "00"},
    "read_input": {"start_byte": 0, "size": 1},
    "read_output": {"start_byte": 0, "size": 1},
    "write_output": {"start_byte": 4, "value": True, "data_type": "BOOL", "bit_index": 3},
    "read_marker": {"start_byte": 0, "size": 1},
    "write_marker": {"start_byte": 4, "value": "00"},
    "read_plc_info": {},
    "read_cpu_state": {},
    "set_cpu_state": {"state": "RUN"},
    "read_system_time": {},
    "read_szl": {"szl_id": 0x0011, "szl_index": 0},
    "read_multiple_vars": {"variables": [{"db_number": 1, "offset": 4, "data_type": "REAL"}]},
    "write_multiple_vars": {"variables": [{"db_number": 1, "offset": 221, "value": 1, "data_type": "BYTE"}]},
    "list_tags": {},
    "read_tag": {"name": "MotorSpeed"},
    "write_tag": {"name": "Spare", "value": 2},
    "ping": {},
    "get_connection_status": {},
}


def test_stdout_carries_only_json_rpc(mock_plc: MockPLC, tmp_path: Path) -> None:
    """Every stdout line is JSON-RPC, with every tool called and stdout unbuffered.

    PYTHONUNBUFFERED=1 matters: with a block-buffered stdout a stray print()
    would sit in the buffer and could be lost at exit instead of failing here.
    """
    tags = tmp_path / "tags.json"
    tags.write_text(
        json.dumps(
            {
                "MotorSpeed": {"area": "db", "db_number": 1, "offset": 0, "data_type": "REAL"},
                "Spare": {"area": "db", "db_number": 1, "offset": 222, "data_type": "BYTE"},
            }
        )
    )
    env = {
        **server_env(
            mock_plc.port,
            S7_DEBUG="true",
            S7_WRITES_ENABLED="true",
            S7_SYSTEM_CMDS_ENABLED="true",
            TAG_MAP_FILE=str(tags),
        ),
        "PATH": "/usr/bin:/bin",
        "PYTHONUNBUFFERED": "1",
    }
    calls = [
        *EVERY_TOOL_CALL.items(),
        ("read_db", {"db_number": 999, "start_offset": 0, "size": 1}),  # PLC error path
        ("read_db", {"db_number": "x"}),  # argument-validation path
    ]
    requests: list[dict[str, Any]] = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    for offset, (name, arguments) in enumerate(calls):
        requests.append(
            {
                "jsonrpc": "2.0",
                "id": 10 + offset,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
    process = subprocess.Popen(
        SERVER_COMMAND,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=SERVER_ROOT,
        env=env,
    )
    responses: dict[int, dict[str, Any]] = {}
    try:
        assert process.stdin and process.stdout
        for request in requests:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            if "id" not in request:
                continue
            while request["id"] not in responses:
                line = process.stdout.readline()
                assert line, "server closed stdout early"
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    pytest.fail(f"non-JSON line on stdout: {line!r}")
                assert message.get("jsonrpc") == "2.0", line
                if "id" in message:
                    responses[message["id"]] = message
        rest, stderr = process.communicate(timeout=30)  # closes stdin: the server exits on EOF
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    for line in rest.splitlines():
        assert json.loads(line).get("jsonrpc") == "2.0", line

    listed = {tool["name"] for tool in responses[2]["result"]["tools"]}
    assert listed == set(EVERY_TOOL_CALL), "every tool must be exercised here"
    for offset, (name, _arguments) in enumerate(calls[: len(EVERY_TOOL_CALL)]):
        envelope = responses[10 + offset]["result"]["structuredContent"]
        assert envelope["success"] is True, (name, envelope)
    assert responses[10 + len(EVERY_TOOL_CALL)]["result"]["structuredContent"]["success"] is False
    assert responses[11 + len(EVERY_TOOL_CALL)]["result"]["structuredContent"]["error"].startswith("Invalid arguments")
    # The debug and error logs went somewhere, and it was stderr.
    assert "Item not available" in stderr
    assert "DEBUG" in stderr


@pytest.mark.parametrize("value", ["yes-please", "2"])
def test_bad_configuration_exits_before_serving(value: str) -> None:
    result = subprocess.run(
        SERVER_COMMAND,
        input="",
        capture_output=True,
        text=True,
        cwd=SERVER_ROOT,
        env={**server_env(102, S7_WRITES_ENABLED=value), "PATH": "/usr/bin:/bin"},
        timeout=30,
    )
    assert result.returncode == 2 and result.stdout == ""
    assert "S7_WRITES_ENABLED" in result.stderr


# ----------------------------------------------- client cancellation and disconnect (rules 1/12)


class RawSession:
    """The real server over stdio, driven with raw JSON-RPC lines."""

    def __init__(self, env: dict[str, str]) -> None:
        self.process = subprocess.Popen(
            SERVER_COMMAND,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=SERVER_ROOT,
            env={**env, "PATH": "/usr/bin:/bin", "PYTHONUNBUFFERED": "1"},
        )
        self.send(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            }
        )
        self.wait_for(0)
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def call(self, request_id: int, tool: str, **arguments: Any) -> None:
        self.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
        )

    def cancel(self, request_id: int) -> None:
        self.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": request_id}})

    def wait_for(self, request_id: int) -> dict[str, Any]:
        assert self.process.stdout
        while True:
            line = self.process.stdout.readline()
            assert line, "server closed stdout"
            message = json.loads(line)
            if message.get("id") == request_id:
                return message

    def disconnect(self) -> None:
        """The client goes away: close the server's stdin."""
        assert self.process.stdin
        self.process.stdin.close()

    def close(self, timeout: float = 30) -> int:
        """Disconnect (if not done yet) and wait for the server to exit."""
        assert self.process.stdin and self.process.stdout and self.process.stderr
        if not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=timeout)
            self.process.stdout.read()
            self.stderr = self.process.stderr.read()
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.communicate()
        return self.process.returncode


def hung_plc_env(proxy: S7Proxy, **extra: str) -> dict[str, str]:
    # S7_TIMEOUT=10: Snap7 waits that long for the held answer.
    return {**server_env(proxy.port, S7_TIMEOUT="10", S7_MAX_RETRIES="0"), "S7_WRITES_ENABLED": "true", **extra}


@pytest.fixture
def proxy(mock_plc: MockPLC) -> Any:
    plc_proxy = S7Proxy(mock_plc.port)
    yield plc_proxy
    plc_proxy.release.set()
    plc_proxy.close()


@pytest.fixture
async def direct(mock_plc: MockPLC) -> Any:
    from s7comm_mcp.s7_client import S7Client, S7ClientConfig

    client = S7Client(S7ClientConfig(host=mock_plc.host, port=mock_plc.port, max_retries=0))
    yield client
    await client.close()


async def test_write_pending_when_the_client_disconnects_is_never_sent(proxy: S7Proxy, direct: Any) -> None:
    await direct.write_db(3, 300, b"\x00")
    session = RawSession(hung_plc_env(proxy))
    proxy.hold_reply_to = {S7_READ_VAR}  # the PLC hangs: the write's pre-send check gets no answer
    session.call(1, "write_db", db_number=3, start_offset=300, value="5a")
    time.sleep(0.5)
    session.disconnect()  # the client goes away while the write waits
    time.sleep(0.5)
    proxy.release.set()  # the PLC comes back
    assert session.close() == 0, session.stderr[-3000:]
    assert "Traceback" not in session.stderr
    time.sleep(0.3)
    assert proxy.count(S7_WRITE_VAR) == 0
    assert (await direct.read_db(3, 300, 1))[0] == b"\x00"


async def test_write_cancelled_by_the_client_while_queued_is_never_sent(proxy: S7Proxy, direct: Any) -> None:
    await direct.write_db(3, 301, b"\x00")
    session = RawSession(hung_plc_env(proxy))
    proxy.hold_reply_to = {S7_READ_VAR}
    session.call(1, "read_db", db_number=1, start_offset=0, size=1)  # holds the client lock
    time.sleep(0.3)
    session.call(2, "write_db", db_number=3, start_offset=301, value="5a")  # queued behind it
    time.sleep(0.3)
    session.cancel(2)
    time.sleep(0.3)
    proxy.release.set()
    assert session.wait_for(1)["result"]["structuredContent"]["success"] is True
    session.call(3, "get_connection_status")
    assert session.wait_for(3)["result"]["structuredContent"]["success"] is True  # session still fine
    time.sleep(0.3)
    assert session.close() == 0
    assert proxy.count(S7_WRITE_VAR) == 0
    assert (await direct.read_db(3, 301, 1))[0] == b"\x00"


async def test_cpu_command_cancelled_by_the_client_is_never_sent(proxy: S7Proxy, direct: Any) -> None:
    session = RawSession(hung_plc_env(proxy, S7_SYSTEM_CMDS_ENABLED="true"))
    proxy.hold_reply_to = {S7_READ_VAR}
    session.call(1, "read_db", db_number=1, start_offset=0, size=1)
    time.sleep(0.3)
    session.call(2, "set_cpu_state", state="STOP")
    time.sleep(0.3)
    session.cancel(2)
    time.sleep(0.3)
    proxy.release.set()
    session.wait_for(1)
    time.sleep(0.3)
    assert session.close() == 0
    assert proxy.count(S7_PLC_STOP) == 0
    assert (await direct.read_cpu_state())[0]["mode"] == "RUN"


def test_server_exits_promptly_with_a_request_stuck_in_snap7(proxy: S7Proxy) -> None:
    session = RawSession({**server_env(proxy.port, S7_TIMEOUT="0.5", S7_MAX_RETRIES="0"), "S7_WRITES_ENABLED": "true"})
    proxy.trickle_reply_to = {S7_WRITE_VAR}  # the answer never completes
    session.call(1, "write_db", db_number=3, start_offset=302, value="01")
    time.sleep(0.5)
    started = time.monotonic()
    assert session.close(timeout=20) == 0, session.stderr[-3000:]
    assert time.monotonic() - started < 8
    assert "Traceback" not in session.stderr
    assert proxy.count(S7_WRITE_VAR) == 1
