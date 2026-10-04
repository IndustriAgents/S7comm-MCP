"""Every tool, in-process, against the real mock PLC (snap7 server).

Addresses written here are chosen away from the bytes the mock's update loop
rewrites (DB1.DBD0, DB1.DBD16, input bits 0-1, marker M0.0).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from support import MockPLC, call

from s7comm_mcp.s7_client import S7Client, S7ClientConfig, S7ConnectionError
from s7comm_mcp.server import S7CommMCPServer
from s7comm_mcp.tools import ToolConfig

ServerFactory = Callable[..., S7CommMCPServer]


@pytest.fixture
async def make_server(mock_plc: MockPLC, clean_env: pytest.MonkeyPatch) -> AsyncIterator[ServerFactory]:
    servers: list[S7CommMCPServer] = []

    def factory(writes: bool = False, system: bool = False, tag_map: Path | None = None) -> S7CommMCPServer:
        config = S7ClientConfig(
            host=mock_plc.host, port=mock_plc.port, timeout=2.0, max_retries=1, retry_backoff_base=0.0
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


async def raw(server: S7CommMCPServer, area: str, offset: int, db_number: int | None = None) -> int:
    """Read one byte (bypassing the typed tools) to check bit-level effects."""
    if area == "db":
        result = await call(server, "read_db", db_number=db_number, start_offset=offset, size=1)
    else:
        result = await call(server, f"read_{area}", start_byte=offset, size=1)
    assert result["success"], result
    return int(result["data"]["raw_bytes_hex"], 16)


# ------------------------------------------------------------- reads (memory map)


@pytest.mark.parametrize(
    ("db", "offset", "dtype", "expected"),
    [(1, 4, "REAL", 42.0), (1, 8, "REAL", 22.5), (1, 12, "DINT", 12345678)],
)
async def test_typed_db_reads(make_server: ServerFactory, db: int, offset: int, dtype: str, expected: Any) -> None:
    result = await call(make_server(), "read_db_typed", db_number=db, start_offset=offset, data_type=dtype)
    assert result["success"] is True
    assert result["data"]["value"] == expected


async def test_bool_read(make_server: ServerFactory) -> None:
    result = await call(make_server(), "read_db_typed", db_number=2, start_offset=0, data_type="BOOL", bit_index=0)
    assert result["data"]["value"] is True


@pytest.mark.parametrize("string_length", [30, None])
async def test_string_read_returns_only_the_text(make_server: ServerFactory, string_length: int | None) -> None:
    args = {"db_number": 2, "start_offset": 2, "data_type": "STRING"}
    if string_length:
        args["string_length"] = string_length
    result = await call(make_server(), "read_db_typed", **args)
    assert result["success"] is True, result
    assert result["data"]["value"] == "OK"  # was '\x1e\x02OK' + 26 spaces
    assert (result["data"]["length"], result["data"]["max_length"]) == (2, 30)


async def test_string_read_with_wrong_length_is_an_error(make_server: ServerFactory) -> None:
    result = await call(
        make_server(), "read_db_typed", db_number=2, start_offset=2, data_type="STRING", string_length=10
    )
    assert result["success"] is False
    assert "max length of 30" in result["error"]


@pytest.mark.parametrize(("tool", "expected_bit0"), [("read_input", None), ("read_output", None), ("read_marker", 1)])
async def test_io_area_reads_work(make_server: ServerFactory, tool: str, expected_bit0: int | None) -> None:
    # Bug 1: these always failed with "unsupported operand type(s) for 'in'".
    result = await call(make_server(), tool, start_byte=0, size=2)
    assert result["success"] is True, result
    assert result["data"]["byte_count"] == 2
    if expected_bit0 is not None:
        assert int(result["data"]["raw_bytes_hex"][:2], 16) & 1 == expected_bit0


async def test_input_bits_toggle(make_server: ServerFactory) -> None:
    server = make_server()
    seen = set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(seen) < 2:
        seen.add(await raw(server, "input", 0) & 0b11)
        await asyncio.sleep(0.1)
    assert len(seen) >= 2


async def test_system_info_tools(make_server: ServerFactory) -> None:
    server = make_server()
    info = await call(server, "read_plc_info")
    assert info["success"] is True
    assert info["data"]["module_type"] == "CPU 315-2 PN/DP"
    assert info["data"]["as_name"] == "SNAP7-SERVER"
    assert info["data"]["order_code"] == "6ES7 315-2EH14-0AB0"
    assert info["data"]["version"] == "V3.2.6"  # firmware record of the emulated CPU 315-2
    state = await call(server, "read_cpu_state")
    assert state["data"] == {"state": "S7CpuStatusRun", "mode": "RUN"}
    clock = await call(server, "read_system_time")
    assert clock["success"] is True and "T" in clock["data"]["system_time"]


async def test_read_szl_is_json(make_server: ServerFactory) -> None:
    # Bug 3: the raw ctypes struct could not be serialised.
    result = await call(make_server(), "read_szl", szl_id=0x0011, szl_index=0)
    assert result["success"] is True, result
    data = result["data"]
    assert data["record_length"] == 28 and data["record_count"] == len(data["records_hex"]) >= 1
    assert bytes.fromhex(data["records_hex"][0])[2:22].decode() == "6ES7 315-2EH14-0AB0 "
    json.dumps(result)


# ------------------------------------------------------------- errors keep the session alive


async def test_plc_errors_do_not_break_the_session(make_server: ServerFactory) -> None:
    # Bug 2: any failed operation left the session unable to reconnect.
    server = make_server()
    missing = await call(server, "read_db", db_number=999, start_offset=0, size=10)
    assert missing["success"] is False
    assert "Item not available" in missing["error"] and "connect" not in missing["error"].lower()
    out_of_range = await call(server, "read_db", db_number=1, start_offset=1000, size=10)
    assert out_of_range["success"] is False and "Address out of range" in out_of_range["error"]
    after = await call(server, "read_db_typed", db_number=1, start_offset=4, data_type="REAL")
    assert after["success"] is True and after["data"]["value"] == 42.0


async def test_reconnects_after_the_link_drops(make_server: ServerFactory) -> None:
    server = make_server()
    assert (await call(server, "ping"))["success"] is True
    # Simulate a dropped link: tear the Snap7 connection down underneath the wrapper.
    server.client._client.disconnect()
    result = await call(server, "read_db_typed", db_number=1, start_offset=8, data_type="REAL")
    assert result["success"] is True, result
    assert result["meta"]["attempts"] == 2


# ------------------------------------------------------------- write gating


async def test_writes_are_refused_by_default(make_server: ServerFactory) -> None:
    server = make_server()
    before = await raw(server, "db", 40, db_number=1)
    for tool, args in [
        ("write_db", {"db_number": 1, "start_offset": 40, "value": "ff"}),
        ("write_output", {"start_byte": 4, "value": True, "data_type": "BOOL", "bit_index": 0}),
        ("write_marker", {"start_byte": 4, "value": "01"}),
        ("write_multiple_vars", {"variables": [{"db_number": 1, "offset": 40, "value": "ff"}]}),
        ("write_tag", {"name": "x", "value": 1}),
    ]:
        result = await call(server, tool, **args)
        assert result["success"] is False and "S7_WRITES_ENABLED" in result["error"], tool
    assert await raw(server, "db", 40, db_number=1) == before
    stop = await call(server, "set_cpu_state", state="STOP")
    assert stop["success"] is False and "S7_SYSTEM_CMDS_ENABLED" in stop["error"]
    assert "S7_WRITES_ENABLED" in stop["error"]


# ------------------------------------------------------------- BOOL writes touch one bit only


@pytest.mark.parametrize(
    ("tool", "area", "location"),
    [
        ("write_db", "db", {"db_number": 1, "start_offset": 100}),
        ("write_output", "output", {"start_byte": 1}),
        ("write_marker", "marker", {"start_byte": 2}),
    ],
)
async def test_bool_write_keeps_the_other_seven_bits(
    make_server: ServerFactory, tool: str, area: str, location: dict[str, int]
) -> None:
    # Bug 5: a BOOL write used to write the whole byte (1 << bit or 0).
    server = make_server(writes=True)
    offset = location.get("start_offset", location.get("start_byte", 0))
    db = location.get("db_number")
    assert (await call(server, tool, **location, value="aa"))["success"]
    assert await raw(server, area, offset, db) == 0b1010_1010

    steps = [(0, True, 0b1010_1011), (7, False, 0b0010_1011), (1, "false", 0b0010_1001), (6, 1, 0b0110_1001)]
    for bit, value, expected in steps:
        result = await call(server, tool, **location, value=value, data_type="BOOL", bit_index=bit)
        assert result["success"] is True, result
        assert result["data"]["written_bits"] == 1
        assert await raw(server, area, offset, db) == expected, (bit, value)


async def test_bool_write_needs_a_bit_and_a_real_boolean(make_server: ServerFactory) -> None:
    server = make_server(writes=True)
    no_bit = await call(server, "write_output", start_byte=1, value=True, data_type="BOOL")
    assert no_bit["success"] is False and "bit_index" in no_bit["error"]
    bad_value = await call(server, "write_output", start_byte=1, value="maybe", data_type="BOOL", bit_index=0)
    assert bad_value["success"] is False


async def test_batch_and_tag_bool_writes_keep_other_bits(make_server: ServerFactory, tmp_path: Path) -> None:
    tags = tmp_path / "tags.json"
    tags.write_text(json.dumps({"Lamp": {"area": "output", "byte": 5, "bit": 3, "data_type": "BOOL"}}))
    server = make_server(writes=True, tag_map=tags)
    assert (await call(server, "write_output", start_byte=5, value="f0"))["success"]
    batch = await call(
        server,
        "write_multiple_vars",
        variables=[{"area": "output", "start_byte": 5, "bit_index": 0, "data_type": "BOOL", "value": True}],
    )
    assert batch["success"] is True, batch
    assert await raw(server, "output", 5) == 0xF1
    tag = await call(server, "write_tag", name="Lamp", value=True)
    assert tag["success"] is True, tag
    assert await raw(server, "output", 5) == 0xF9


# ------------------------------------------------------------- STRING writes


async def test_string_write_round_trip_keeps_the_header(make_server: ServerFactory) -> None:
    server = make_server(writes=True)
    write = await call(
        server, "write_db", db_number=2, start_offset=2, value="HELLO", data_type="STRING", string_length=30
    )
    assert write["success"] is True, write
    assert write["data"]["written_bytes"] == 7
    header = await call(server, "read_db", db_number=2, start_offset=2, size=7)
    assert header["data"]["raw_bytes_hex"] == bytes([30, 5]).hex() + b"HELLO".hex()
    read = await call(server, "read_db_typed", db_number=2, start_offset=2, data_type="STRING")
    assert read["data"]["value"] == "HELLO"
    restore = await call(
        server, "write_db", db_number=2, start_offset=2, value="OK", data_type="STRING", string_length=30
    )
    assert restore["success"] is True


@pytest.mark.parametrize(
    ("value", "string_length", "message"),
    [
        ("X" * 31, 30, "at most 30"),
        ("SHORT", 20, "max length of 30, not 20"),
        ("SHORT", None, "string_length"),
    ],
)
async def test_bad_string_writes_change_nothing(
    make_server: ServerFactory, value: str, string_length: int | None, message: str
) -> None:
    server = make_server(writes=True)
    before = await call(server, "read_db", db_number=2, start_offset=2, size=32)
    args: dict[str, Any] = {"db_number": 2, "start_offset": 2, "value": value, "data_type": "STRING"}
    if string_length is not None:
        args["string_length"] = string_length
    result = await call(server, "write_db", **args)
    assert result["success"] is False and message in result["error"]
    after = await call(server, "read_db", db_number=2, start_offset=2, size=32)
    assert after["data"] == before["data"]


async def test_string_write_needs_an_existing_matching_header(make_server: ServerFactory) -> None:
    # Review finding 2: a 00 00 header used to be "initialised", planting a
    # STRING header over non-STRING data (here a DINT).
    server = make_server(writes=True)
    assert (await call(server, "write_db", db_number=2, start_offset=60, value="0000000c2238"))["success"]
    for value, string_length in [("HACK", 20), ("", 254)]:
        result = await call(
            server,
            "write_db",
            db_number=2,
            start_offset=60,
            value=value,
            data_type="STRING",
            string_length=string_length,
        )
        assert result["success"] is False
        assert "no S7 STRING header" in result["error"] and "Nothing was written" in result["error"]
        after = await call(server, "read_db", db_number=2, start_offset=60, size=6)
        assert after["data"]["raw_bytes_hex"] == "0000000c2238"


# ------------------------------------------------------------- typed writes and validation


@pytest.mark.parametrize(
    ("dtype", "value", "offset"),
    [("BYTE", 200, 120), ("WORD", 65535, 122), ("INT", -1234, 124), ("DINT", -7, 126), ("REAL", -3.5, 130)],
)
async def test_typed_write_read_back(make_server: ServerFactory, dtype: str, value: Any, offset: int) -> None:
    server = make_server(writes=True)
    write = await call(server, "write_db", db_number=1, start_offset=offset, value=value, data_type=dtype)
    assert write["success"] is True, write
    read = await call(server, "read_db_typed", db_number=1, start_offset=offset, data_type=dtype)
    assert read["data"]["value"] == value


@pytest.mark.parametrize(
    ("tool", "args", "message"),
    [
        ("write_db", {"db_number": 1, "start_offset": 140, "value": 70000, "data_type": "INT"}, "between"),
        ("write_db", {"db_number": 1, "start_offset": 140, "value": [1, 999]}, "between 0 and 255"),
        ("write_db", {"db_number": 0, "start_offset": 140, "value": "00"}, "db_number"),
        ("read_db", {"db_number": 1, "start_offset": 0, "size": 0}, "size"),
        ("read_db", {"db_number": "one", "start_offset": 0, "size": 1}, "Invalid arguments"),
        ("read_db", {"db_number": 1, "start_offset": 0}, "Invalid arguments"),
        ("read_input", {"start_byte": -1, "size": 1}, "offset"),
        ("read_db_typed", {"db_number": 1, "start_offset": 0, "data_type": "INVALID_TYPE"}, "Unsupported data_type"),
        ("read_szl", {"szl_id": 70000}, "szl_id"),
        ("read_multiple_vars", {"variables": []}, "non-empty"),
        ("set_cpu_state", {"state": "PAUSE"}, "disabled"),
    ],
)
async def test_bad_arguments_return_envelopes(
    make_server: ServerFactory, tool: str, args: dict[str, Any], message: str
) -> None:
    result = await call(make_server(writes=True), tool, **args)
    assert set(result) == {"success", "data", "error", "meta"}
    assert result["success"] is False
    assert message in result["error"], result["error"]


async def test_invalid_cpu_state_with_system_commands_enabled(make_server: ServerFactory) -> None:
    result = await call(make_server(writes=True, system=True), "set_cpu_state", state="PAUSE")
    assert result["success"] is False and "RUN or STOP" in result["error"]


async def test_set_cpu_state_reports_the_observed_mode(make_server: ServerFactory) -> None:
    server = make_server(writes=True, system=True)
    try:
        stop = await call(server, "set_cpu_state", state="stop")
        assert stop["success"] is True, stop
        assert stop["data"]["mode"] == "STOP"
        assert (await call(server, "read_cpu_state"))["data"]["mode"] == "STOP"
    finally:
        run = await call(server, "set_cpu_state", state="RUN")
    assert run["success"] is True and run["data"]["mode"] == "RUN"


# ------------------------------------------------------------- batch operations


async def test_batch_read_uses_the_given_offsets(make_server: ServerFactory) -> None:
    result = await call(
        make_server(),
        "read_multiple_vars",
        variables=[
            {"area": "db", "db_number": 1, "start_offset": 4, "data_type": "REAL"},
            {"area": "db", "db_number": 1, "offset": 12, "data_type": "DINT"},
            {"area": "marker", "byte": 0, "data_type": "BOOL", "bit": 0},
            {"area": "db", "db_number": 2, "offset": 2, "data_type": "STRING"},
        ],
    )
    assert result["success"] is True, result
    values = [item["data"]["value"] for item in result["data"]["results"]]
    assert values == [42.0, 12345678, True, "OK"]


async def test_batch_read_reports_partial_failure(make_server: ServerFactory) -> None:
    result = await call(
        make_server(),
        "read_multiple_vars",
        variables=[
            {"db_number": 1, "offset": 4, "data_type": "REAL"},
            {"db_number": 999, "offset": 0, "data_type": "REAL"},
            {"db_number": 1},
        ],
    )
    assert result["success"] is False
    assert result["error"] == "2 of 3 entries could not be read"
    first, second, third = result["data"]["results"]
    assert first["success"] and first["data"]["value"] == 42.0
    assert not second["success"] and "Item not available" in second["error"]
    assert not third["success"] and "offset" in third["error"]


async def test_invalid_batch_entry_means_nothing_is_written(make_server: ServerFactory) -> None:
    server = make_server(writes=True)
    assert (await call(server, "write_db", db_number=1, start_offset=150, value="0000"))["success"]
    result = await call(
        server,
        "write_multiple_vars",
        variables=[
            {"db_number": 1, "offset": 150, "value": 5, "data_type": "BYTE"},
            {"db_number": 1, "offset": 151, "value": 300, "data_type": "BYTE"},
        ],
    )
    assert result["success"] is False and result["error"].startswith("Nothing was written")
    assert result["data"]["results"][0]["skipped"] is True
    assert (await call(server, "read_db", db_number=1, start_offset=150, size=2))["data"]["raw_bytes_hex"] == "0000"


async def test_batch_write_stops_at_the_first_plc_error(make_server: ServerFactory) -> None:
    server = make_server(writes=True)
    result = await call(
        server,
        "write_multiple_vars",
        variables=[
            {"db_number": 1, "offset": 160, "value": 1, "data_type": "BYTE"},
            {"db_number": 999, "offset": 0, "value": 2, "data_type": "BYTE"},
            {"db_number": 1, "offset": 161, "value": 3, "data_type": "BYTE"},
        ],
    )
    assert result["success"] is False
    assert result["meta"] == {"count": 3, "written": 1}
    ok_entry, failed, skipped = result["data"]["results"]
    assert ok_entry["success"] is True
    assert failed["success"] is False and "Item not available" in failed["error"]
    assert skipped["skipped"] is True
    assert (await call(server, "read_db", db_number=1, start_offset=160, size=2))["data"]["raw_bytes_hex"] == "0100"


# ------------------------------------------------------------- tag map


async def test_missing_or_broken_tag_map_is_an_error(make_server: ServerFactory, tmp_path: Path) -> None:
    missing = await call(make_server(tag_map=tmp_path / "nope.json"), "list_tags")
    assert missing["success"] is False and "cannot be read" in missing["error"]
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    result = await call(make_server(tag_map=broken), "read_tag", name="MotorSpeed")
    assert result["success"] is False and "not valid JSON" in result["error"]


async def test_tag_map_round_trip(make_server: ServerFactory, tmp_path: Path) -> None:
    tags = tmp_path / "tags.json"
    tags.write_text(
        json.dumps(
            {
                "MotorSpeed": {"area": "db", "db_number": 1, "offset": 0, "data_type": "REAL", "description": "rpm"},
                "AlarmActive": {"area": "db", "db_number": 2, "offset": 0, "data_type": "BOOL", "bit": 0},
                "Setpoint": {"area": "db", "db_number": 1, "start_offset": 200, "data_type": "INT"},
            }
        )
    )
    server = make_server(writes=True, tag_map=tags)
    listed = await call(server, "list_tags")
    assert [tag["name"] for tag in listed["data"]["tags"]] == ["MotorSpeed", "AlarmActive", "Setpoint"]
    assert (await call(server, "read_tag", name="AlarmActive"))["data"]["value"] is True
    speed = (await call(server, "read_tag", name="MotorSpeed"))["data"]["value"]
    assert 1430 <= speed <= 1470
    assert (await call(server, "write_tag", name="Setpoint", value=-42))["success"]
    assert (await call(server, "read_tag", name="Setpoint"))["data"]["value"] == -42
    unknown = await call(server, "read_tag", name="Nope")
    assert unknown["success"] is False and "Unknown tag" in unknown["error"]


# ------------------------------------------------------------- health


async def test_ping_and_status(make_server: ServerFactory) -> None:
    server = make_server()
    status = await call(server, "get_connection_status")
    assert status["success"] is True and status["data"]["connected"] is False  # lazy connect
    ping = await call(server, "ping")
    assert ping["success"] is True
    assert ping["data"]["cpu_state"] == "S7CpuStatusRun"
    assert ping["data"]["writes_enabled"] is False
    assert (await call(server, "get_connection_status"))["data"]["connected"] is True


async def test_ping_fails_honestly_without_a_plc(clean_env: pytest.MonkeyPatch) -> None:
    from support import free_port

    server = S7CommMCPServer(
        client=S7Client(S7ClientConfig(port=free_port(), timeout=1.0, max_retries=0)),
        tool_config=ToolConfig(),
    )
    result = await call(server, "ping")
    assert result["success"] is False and "Cannot reach" in result["error"]
    assert result["meta"]["connection"]["connected"] is False


async def test_s7_timeout_bounds_a_hung_plc(silent_tcp_server: int) -> None:
    client = S7Client(S7ClientConfig(port=silent_tcp_server, timeout=0.5, max_retries=0))
    started = time.monotonic()
    with pytest.raises(S7ConnectionError, match="timed out"):
        await client.ensure_connection()
    assert time.monotonic() - started < 5
