"""Regression tests for the write-safety review findings, against the mock PLC.

Every refused write must leave PLC memory exactly as it was.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from support import S7_PLC_STOP, S7_READ_VAR, S7_WRITE_VAR, MockPLC, S7Proxy, call

from s7comm_mcp.s7_client import S7Client, S7ClientConfig, WriteOutcomeUnknownError
from s7comm_mcp.server import S7CommMCPServer
from s7comm_mcp.tools import ToolConfig

ServerFactory = Callable[..., S7CommMCPServer]


@pytest.fixture
async def make_server(mock_plc: MockPLC, clean_env: pytest.MonkeyPatch) -> AsyncIterator[ServerFactory]:
    servers: list[S7CommMCPServer] = []

    def factory(
        writes: bool = True, system: bool = False, tag_map: Path | None = None, port: int | None = None
    ) -> S7CommMCPServer:
        config = S7ClientConfig(
            host=mock_plc.host,
            port=port or mock_plc.port,
            timeout=0.5 if port else 2.0,  # a lost reply costs one receive timeout
            max_retries=3,
            retry_backoff_base=0,
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


# ----------------------------------------------- finding 4: inputs are read-only


async def test_batch_write_to_inputs_is_refused(make_server: ServerFactory) -> None:
    server = make_server()
    await assert_refused_and_unchanged(
        server,
        "write_multiple_vars",
        {"variables": [{"area": "input", "byte": 3, "value": "ff"}]},
        "Nothing was written",
        "input",
        3,
    )
    result = await call(server, "write_multiple_vars", variables=[{"area": "I", "byte": 3, "value": "ff"}])
    assert "read-only" in result["data"]["results"][0]["error"]


async def test_bool_batch_write_to_an_input_bit_is_refused(make_server: ServerFactory) -> None:
    server = make_server()
    await assert_refused_and_unchanged(
        server,
        "write_multiple_vars",
        {"variables": [{"area": "input", "byte": 3, "bit": 2, "data_type": "BOOL", "value": True}]},
        "Nothing was written",
        "input",
        3,
    )


async def test_tag_write_to_inputs_is_refused(make_server: ServerFactory, tmp_path: Path) -> None:
    tags = tmp_path / "tags.json"
    tags.write_text(json.dumps({"Sensor": {"area": "input", "byte": 3, "data_type": "BYTE"}}))
    server = make_server(tag_map=tags)
    await assert_refused_and_unchanged(server, "write_tag", {"name": "Sensor", "value": 255}, "read-only", "input", 3)
    assert (await call(server, "read_tag", name="Sensor"))["success"] is True  # reading is fine


async def test_client_refuses_input_writes(mock_plc: MockPLC) -> None:
    client = S7Client(S7ClientConfig(host=mock_plc.host, port=mock_plc.port, max_retries=0))
    try:
        with pytest.raises(ValueError, match="read-only"):
            await client.write_area("input", 3, b"\xff")
        with pytest.raises(ValueError, match="read-only"):
            await client.write_bit("input", 3, 0, True)
        payload, _ = await client.read_area("input", 3, 1)
        assert payload == b"\x00"
    finally:
        await client.close()


# ----------------------------------------------- finding 5: batch checks happen before any write


async def test_batch_string_header_mismatch_writes_nothing(make_server: ServerFactory) -> None:
    # Review case: entry 0 used to be written before entry 1's STRING header was checked.
    server = make_server()
    assert (await call(server, "write_db", db_number=1, start_offset=170, value="00"))["success"]
    result = await call(
        server,
        "write_multiple_vars",
        variables=[
            {"db_number": 1, "offset": 170, "value": 7, "data_type": "BYTE"},
            {"db_number": 2, "offset": 2, "value": "x", "data_type": "STRING", "string_length": 29},
        ],
    )
    assert result["success"] is False and result["error"].startswith("Nothing was written")
    assert result["meta"]["written"] == 0
    assert "max length of 30, not 29" in result["data"]["results"][1]["error"]
    assert await read_hex(server, "db", 170, db=1) == "00"


# ----------------------------------------------- suite rule: a write reaches the PLC at most once


@pytest.fixture
def proxy(mock_plc: MockPLC) -> Any:
    plc_proxy = S7Proxy(mock_plc.port)
    yield plc_proxy
    plc_proxy.close()


@pytest.fixture
async def direct(mock_plc: MockPLC) -> AsyncIterator[S7Client]:
    """A second client straight to the mock, to see what the PLC really holds."""
    client = S7Client(S7ClientConfig(host=mock_plc.host, port=mock_plc.port, max_retries=0))
    yield client
    await client.close()


def via_proxy(proxy: S7Proxy, retries: int = 3) -> S7Client:
    return S7Client(S7ClientConfig(port=proxy.port, timeout=0.5, max_retries=retries, retry_backoff_base=0))


async def test_lost_write_reply_is_reported_and_not_resent(proxy: S7Proxy, direct: S7Client) -> None:
    await direct.write_db(1, 180, b"\x00\x00")
    client = via_proxy(proxy)
    proxy.drop_reply_to = {S7_WRITE_VAR}
    try:
        with pytest.raises(WriteOutcomeUnknownError, match="may have been applied; check before retrying"):
            await client.write_db(1, 180, b"\xbe\xef")
    finally:
        await client.close()
    assert proxy.count(S7_WRITE_VAR) == 1  # despite S7_MAX_RETRIES=3
    assert (await direct.read_db(1, 180, 2))[0] == b"\xbe\xef"  # it was applied
    assert client.connection_status()["connected"] is False


async def test_lost_bit_write_reply_is_not_resent(proxy: S7Proxy, direct: S7Client) -> None:
    await direct.write_area("output", 6, b"\x00")
    client = via_proxy(proxy)
    proxy.drop_reply_to = {S7_WRITE_VAR}
    try:
        with pytest.raises(WriteOutcomeUnknownError):
            await client.write_bit("output", 6, 4, True)
    finally:
        await client.close()
    assert proxy.count(S7_WRITE_VAR) == 1
    assert (await direct.read_area("output", 6, 1))[0] == b"\x10"


async def test_lost_cpu_command_reply_is_not_resent(proxy: S7Proxy, direct: S7Client) -> None:
    client = via_proxy(proxy)
    proxy.drop_reply_to = {S7_PLC_STOP}
    try:
        with pytest.raises(WriteOutcomeUnknownError):
            await client.set_cpu_state("STOP")
        assert proxy.count(S7_PLC_STOP) == 1
        assert (await direct.read_cpu_state())[0]["mode"] == "STOP"
    finally:
        await client.close()
        await direct.set_cpu_state("RUN")


async def test_reads_may_be_retried(proxy: S7Proxy) -> None:
    client = via_proxy(proxy)
    proxy.drop_reply_to, proxy.drop_once = {S7_READ_VAR}, True
    try:
        payload, meta = await client.read_db(1, 4, 4)
    finally:
        await client.close()
    assert payload == bytes.fromhex("42280000") and meta["attempts"] == 2
    assert proxy.count(S7_READ_VAR) == 2


async def test_write_after_a_dead_idle_link_is_sent_once(proxy: S7Proxy, direct: S7Client) -> None:
    # The link died while idle (PLC reboot, cable): the probe read finds out,
    # the client reconnects, and only then is the write sent, once.
    client = via_proxy(proxy)
    try:
        await client.read_db(1, 4, 4)
        proxy.kill_connections()
        meta = await client.write_db(1, 184, b"\x12\x34")
    finally:
        await client.close()
    assert meta["attempts"] == 2
    assert proxy.count(S7_WRITE_VAR) == 1
    assert (await direct.read_db(1, 184, 2))[0] == b"\x12\x34"


async def test_tools_report_an_unknown_outcome(make_server: ServerFactory, proxy: S7Proxy, direct: S7Client) -> None:
    server = make_server(port=proxy.port)
    proxy.drop_reply_to = {S7_WRITE_VAR}
    result = await call(server, "write_db", db_number=1, start_offset=188, value=7, data_type="INT")
    assert result["success"] is False
    assert "no reply from the PLC" in result["error"] and "not resent" in result["error"]
    assert "may have been applied; check before retrying" in result["error"]
    assert (result["meta"]["outcome"], result["meta"]["request_sent"]) == ("unknown", True)
    assert proxy.count(S7_WRITE_VAR) == 1


async def test_batch_stops_at_an_unknown_outcome(make_server: ServerFactory, proxy: S7Proxy, direct: S7Client) -> None:
    await direct.write_db(1, 192, b"\x00\x00")
    server = make_server(port=proxy.port)
    proxy.drop_reply_to = {S7_WRITE_VAR}
    result = await call(
        server,
        "write_multiple_vars",
        variables=[
            {"db_number": 1, "offset": 192, "value": 1, "data_type": "BYTE"},
            {"db_number": 1, "offset": 193, "value": 2, "data_type": "BYTE"},
        ],
    )
    assert result["success"] is False and "may have been applied" in result["error"]
    first, second = result["data"]["results"]
    assert first["outcome"] == "unknown" and first["request_sent"] is True
    assert second["skipped"] is True and second["outcome"] == "not_sent"
    assert proxy.count(S7_WRITE_VAR) == 1
    assert (await direct.read_db(1, 192, 2))[0] == b"\x01\x00"


# ----------------------------------------------- meta.outcome / meta.request_sent


def outcome(result: dict[str, Any]) -> tuple[str, bool]:
    return result["meta"]["outcome"], result["meta"]["request_sent"]


async def test_outcome_written(make_server: ServerFactory) -> None:
    result = await call(make_server(), "write_db", db_number=1, start_offset=200, value=5, data_type="INT")
    assert result["success"] is True and outcome(result) == ("written", True)
    bit = await call(make_server(), "write_output", start_byte=7, value=True, data_type="BOOL", bit_index=2)
    assert outcome(bit) == ("written", True)


async def test_outcome_not_sent(make_server: ServerFactory) -> None:
    disabled = await call(make_server(writes=False), "write_db", db_number=1, start_offset=200, value="00")
    assert outcome(disabled) == ("not_sent", False)
    invalid = await call(make_server(), "write_db", db_number=1, start_offset=200, value=70000, data_type="INT")
    assert outcome(invalid) == ("not_sent", False)
    # The pre-send probe finds the DB missing, so the write never goes out.
    missing = await call(make_server(), "write_db", db_number=999, start_offset=0, value="00")
    assert outcome(missing) == ("not_sent", False) and "Item not available" in missing["error"]
    cpu = await call(make_server(writes=False, system=True), "set_cpu_state", state="STOP")
    assert outcome(cpu) == ("not_sent", False)


async def test_outcome_rejected(make_server: ServerFactory) -> None:
    # The probe (byte 254) is fine; the 4-byte write runs past DB1's end and the PLC refuses it.
    server = make_server()
    before = await read_hex(server, "db", 254, size=2, db=1)
    result = await call(server, "write_db", db_number=1, start_offset=254, value="01020304")
    assert result["success"] is False and "Address out of range" in result["error"]
    assert outcome(result) == ("rejected", True)
    assert await read_hex(server, "db", 254, size=2, db=1) == before


async def test_batch_reports_the_outcome_per_entry(make_server: ServerFactory) -> None:
    result = await call(
        make_server(),
        "write_multiple_vars",
        variables=[
            {"db_number": 1, "offset": 210, "value": 1, "data_type": "BYTE"},
            {"area": "marker", "byte": 5, "bit": 1, "value": True, "data_type": "BOOL"},
        ],
    )
    assert result["success"] is True and outcome(result) == ("written", True)
    assert [(r["outcome"], r["request_sent"]) for r in result["data"]["results"]] == [("written", True)] * 2


# ----------------------------------------------- rule 7: strict argument types


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("read_db", {"db_number": True, "start_offset": 0, "size": 1}),
        ("read_db", {"db_number": 1, "start_offset": "4", "size": 1}),
        ("read_db", {"db_number": 1, "start_offset": 4.0, "size": 1}),
        ("read_input", {"start_byte": 0, "size": True}),
        ("write_output", {"start_byte": 1, "value": True, "data_type": "BOOL", "bit_index": True}),
        ("read_szl", {"szl_id": True}),
    ],
)
async def test_tool_integers_are_strict(make_server: ServerFactory, tool: str, args: dict[str, Any]) -> None:
    result = await call(make_server(), tool, **args)
    assert result["success"] is False and result["error"].startswith("Invalid arguments"), result


async def test_bool_value_must_be_a_boolean(make_server: ServerFactory) -> None:
    server = make_server()
    assert (await call(server, "write_output", start_byte=1, value="00"))["success"]
    for value in ("true", 1, "false"):
        await assert_refused_and_unchanged(
            server,
            "write_output",
            {"start_byte": 1, "value": value, "data_type": "BOOL", "bit_index": 0},
            "JSON boolean",
            "output",
            1,
        )
