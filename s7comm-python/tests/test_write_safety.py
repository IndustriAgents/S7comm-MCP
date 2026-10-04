"""Regression tests for the write-safety review findings, against the mock PLC.

Every refused write must leave PLC memory exactly as it was.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from support import MockPLC, call

from s7comm_mcp.s7_client import S7Client, S7ClientConfig
from s7comm_mcp.server import S7CommMCPServer
from s7comm_mcp.tools import ToolConfig

ServerFactory = Callable[..., S7CommMCPServer]


@pytest.fixture
async def make_server(mock_plc: MockPLC, clean_env: pytest.MonkeyPatch) -> AsyncIterator[ServerFactory]:
    servers: list[S7CommMCPServer] = []

    def factory(writes: bool = True, system: bool = False, tag_map: Path | None = None) -> S7CommMCPServer:
        config = S7ClientConfig(
            host=mock_plc.host, port=mock_plc.port, timeout=2.0, max_retries=1, retry_backoff_base=0
        )
        server = S7CommMCPServer(
            client=S7Client(config),
            tool_config=ToolConfig(writes_enabled=writes, system_cmds_enabled=system, tag_map_path=tag_map),
        )
        servers.append(server)
        return server

    yield factory
    for server in servers:
        await server.client.close()


async def read_hex(server: S7CommMCPServer, area: str, offset: int, size: int = 1, db: int | None = None) -> str:
    if area == "db":
        result = await call(server, "read_db", db_number=db, start_offset=offset, size=size)
    else:
        result = await call(server, f"read_{area}", start_byte=offset, size=size)
    assert result["success"], result
    return result["data"]["raw_bytes_hex"]


async def assert_refused_and_unchanged(
    server: S7CommMCPServer, tool: str, args: dict[str, Any], message: str, area: str, offset: int, **where: Any
) -> None:
    size = where.pop("size", 1)
    before = await read_hex(server, area, offset, size, **where)
    result = await call(server, tool, **args)
    assert result["success"] is False, result
    assert message in result["error"], result["error"]
    assert await read_hex(server, area, offset, size, **where) == before


# ----------------------------------------------- finding 1: bit_index ignored for non-BOOL


async def test_bit_index_without_bool_is_refused_for_raw_output_writes(make_server: ServerFactory) -> None:
    server = make_server()
    assert (await call(server, "write_output", start_byte=2, value="a5"))["success"]
    await assert_refused_and_unchanged(
        server,
        "write_output",
        {"start_byte": 2, "value": [8], "bit_index": 3},
        "bit_index only applies to data_type BOOL",
        "output",
        2,
    )
    assert await read_hex(server, "output", 2) == "a5"


async def test_bit_index_with_byte_type_is_refused(make_server: ServerFactory) -> None:
    server = make_server()
    assert (await call(server, "write_db", db_number=1, start_offset=60, value="a5"))["success"]
    await assert_refused_and_unchanged(
        server,
        "write_db",
        {"db_number": 1, "start_offset": 60, "value": 1, "data_type": "BYTE", "bit_index": 0},
        "bit_index only applies to data_type BOOL",
        "db",
        60,
        db=1,
    )


async def test_bit_index_without_bool_is_refused_for_markers(make_server: ServerFactory) -> None:
    server = make_server()
    assert (await call(server, "write_marker", start_byte=3, value="a5"))["success"]
    await assert_refused_and_unchanged(
        server, "write_marker", {"start_byte": 3, "value": "ff", "bit_index": 1}, "only applies", "marker", 3
    )


async def test_batch_entry_with_bit_but_no_bool_is_refused(make_server: ServerFactory) -> None:
    server = make_server()
    assert (await call(server, "write_output", start_byte=2, value="a5"))["success"]
    await assert_refused_and_unchanged(
        server,
        "write_multiple_vars",
        {"variables": [{"area": "output", "byte": 2, "bit": 6, "value": "40"}]},
        "Nothing was written",
        "output",
        2,
    )


async def test_tag_with_bit_but_no_bool_is_refused(make_server: ServerFactory, tmp_path: Path) -> None:
    tags = tmp_path / "tags.json"
    tags.write_text(json.dumps({"Valve": {"area": "output", "byte": 2, "bit": 6}}))
    server = make_server(tag_map=tags)
    assert (await call(server, "write_output", start_byte=2, value="a5"))["success"]
    await assert_refused_and_unchanged(
        server, "write_tag", {"name": "Valve", "value": "40"}, "bit_index only applies", "output", 2
    )


async def test_string_length_without_string_is_refused(make_server: ServerFactory) -> None:
    server = make_server()
    await assert_refused_and_unchanged(
        server,
        "write_db",
        {"db_number": 1, "start_offset": 8, "value": 1.0, "data_type": "REAL", "string_length": 4},
        "string_length only applies to data_type STRING",
        "db",
        8,
        db=1,
        size=4,
    )


async def test_bit_index_on_typed_read_is_refused(make_server: ServerFactory) -> None:
    result = await call(make_server(), "read_db_typed", db_number=1, start_offset=4, data_type="REAL", bit_index=0)
    assert result["success"] is False and "bit_index only applies" in result["error"]


# ----------------------------------------------- finding 3: RUN/STOP needs both switches


@pytest.mark.parametrize(("writes", "system"), [(False, True), (True, False), (False, False)])
async def test_set_cpu_state_needs_writes_and_system_commands(
    make_server: ServerFactory, writes: bool, system: bool
) -> None:
    server = make_server(writes=writes, system=system)
    result = await call(server, "set_cpu_state", state="STOP")
    assert result["success"] is False
    assert "S7_WRITES_ENABLED=true and S7_SYSTEM_CMDS_ENABLED=true" in result["error"]
    assert (await call(server, "read_cpu_state"))["data"]["mode"] == "RUN"
    assert (await call(server, "ping"))["data"]["cpu_control_enabled"] is False


async def test_set_cpu_state_works_with_both_switches(make_server: ServerFactory) -> None:
    server = make_server(writes=True, system=True)
    try:
        stop = await call(server, "set_cpu_state", state="STOP")
        assert stop["success"] is True and stop["data"]["mode"] == "STOP"
    finally:
        assert (await call(server, "set_cpu_state", state="RUN"))["success"]


def test_startup_warns_when_system_commands_lack_writes(
    clean_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    clean_env.setenv("S7_SYSTEM_CMDS_ENABLED", "true")
    with caplog.at_level("WARNING", logger="s7comm_mcp"):
        S7CommMCPServer()
    assert "S7_SYSTEM_CMDS_ENABLED=true has no effect while S7_WRITES_ENABLED is false" in caplog.text
