"""S7Client logic against a scripted fake of python-snap7's Client."""

from __future__ import annotations

import ctypes
import json
from typing import Any

import pytest
from snap7.type import S7SZL, Area, Parameter, S7CpuInfo, WordLen
from support import free_port

from s7comm_mcp import s7_client
from s7comm_mcp.s7_client import (
    AREAS,
    OperationFailedError,
    S7Client,
    S7ClientConfig,
    S7ConnectionError,
    area_for,
    is_link_error,
)

LINK_ERROR = RuntimeError(b" ISO : An error occurred during recv TCP : Connection reset by peer")
CPU_ITEM_NOT_AVAILABLE = 0x00C00000
CPU_CANNOT_STOP = 0x01000000
CPU_ALREADY_RUN = 0x00F00000


class FakeLib:
    def __init__(self, owner: FakeClient) -> None:
        self.owner = owner

    def Cli_GetPlcStatus(self, handle: Any, status_ref: Any) -> int:  # noqa: N802 - Snap7 name
        status_ref._obj.value = self.owner.cpu_status
        return self.owner.status_rc

    def Cli_WriteArea(self, handle, area, db, start, amount, wordlen, data_ref) -> int:  # noqa: N802
        self.owner.calls.append(("Cli_WriteArea", area, db, start, amount, wordlen, data_ref._obj[0]))
        return 0


class FakeClient:
    """Records calls; ``script`` holds exceptions to raise on the next operations."""

    instances: list[FakeClient] = []
    script: list[BaseException | None] = []
    cpu_status = 0x08
    status_rc = 0
    command_rc = 0

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.connected = False
        self._lib = FakeLib(self)
        self._s7_client = object()
        FakeClient.instances.append(self)

    def set_param(self, parameter: Parameter, value: int) -> None:
        self.calls.append(("set_param", parameter, value))

    def set_connection_type(self, value: int) -> None:
        self.calls.append(("set_connection_type", value))

    def connect(self, host: str, rack: int, slot: int, tcp_port: int) -> None:
        if self.connected:
            raise RuntimeError(b"CLI : Cannot change this param now")
        self.calls.append(("connect", host, rack, slot, tcp_port))
        self.connected = True

    def disconnect(self) -> None:
        self.calls.append(("disconnect",))
        self.connected = False

    def destroy(self) -> None:
        self.calls.append(("destroy",))

    def _next(self) -> None:
        if FakeClient.script:
            exc = FakeClient.script.pop(0)
            if exc is not None:
                raise exc

    def read_area(self, area: Any, db_number: int, start: int, size: int) -> bytearray:
        if not isinstance(area, Area):  # python-snap7 2.x on Python 3.11 raises TypeError
            raise TypeError("unsupported operand type(s) for 'in': 'int' and 'EnumType'")
        self.calls.append(("read_area", area, db_number, start, size))
        self._next()
        return bytearray(i & 0xFF for i in range(size))

    def write_area(self, area: Any, db_number: int, start: int, data: bytearray) -> None:
        assert isinstance(area, Area)
        self.calls.append(("write_area", area, db_number, start, bytes(data)))
        self._next()

    def plc_stop(self) -> int:
        return FakeClient.command_rc

    def plc_hot_start(self) -> int:
        return FakeClient.command_rc

    def get_cpu_info(self) -> S7CpuInfo:
        info = S7CpuInfo()
        info.ModuleTypeName = b"CPU 1516-3 PN/DP"
        info.SerialNumber = b"S C-X4U012345"
        info.ASName = b"PLC_1"
        info.Copyright = b"Original Siemens Equipment"
        info.ModuleName = b"PLC_1"
        return info

    def read_szl(self, szl_id: int, index: int) -> S7SZL:
        record_length, data = SZL_LISTS[szl_id]
        szl = S7SZL()
        szl.Header.LengthDR = record_length
        szl.Header.NDR = len(data) // record_length
        for i, byte in enumerate(data):
            szl.Data[i] = byte if byte < 128 else byte - 256
        return szl


def _module_record(index: int, mlfb: str, ausbg: bytes, ausbe: bytes) -> bytes:
    return index.to_bytes(2, "big") + mlfb.ljust(20).encode() + b"\x00\xc0" + ausbg + ausbe


SZL_LISTS: dict[int, tuple[int, bytes]] = {
    0x0011: (
        28,
        _module_record(0x0001, "6ES7 516-3AN02-0AB0", b"\x00\x04", b"\x00\x01")
        + _module_record(0x0007, "", b"V\x02", b"\x09\x04")
        + _module_record(0x0081, "Boot Loader", b"A\x20", b"\x09\x09"),
    ),
    0x001C: (4, b"\x00\x01\xab\xcd\x00\x06\xef\x10"),
}


@pytest.fixture
def fake_snap7(monkeypatch: pytest.MonkeyPatch) -> type[FakeClient]:
    FakeClient.instances = []
    FakeClient.script = []
    FakeClient.cpu_status, FakeClient.status_rc, FakeClient.command_rc = 0x08, 0, 0
    monkeypatch.setattr(s7_client.snap7.client, "Client", FakeClient)
    monkeypatch.setattr(S7Client, "_probe_tcp", lambda self: None)
    return FakeClient


def make_client(**overrides: Any) -> S7Client:
    settings = {"host": "192.0.2.1", "port": 102, "retry_backoff_base": 0.0, "max_retries": 2, **overrides}
    return S7Client(S7ClientConfig(**settings))


# ------------------------------------------------------------- bug 1: Area enum


def test_area_names_map_to_snap7_area_enum() -> None:
    assert AREAS == {"db": Area.DB, "input": Area.PE, "output": Area.PA, "marker": Area.MK}
    assert all(isinstance(area, Area) for area in AREAS.values())
    with pytest.raises(ValueError, match="Unsupported area"):
        area_for("timer")


@pytest.mark.parametrize(("name", "area"), [("input", Area.PE), ("output", Area.PA), ("marker", Area.MK)])
async def test_area_reads_pass_the_enum_to_snap7(fake_snap7: type[FakeClient], name: str, area: Area) -> None:
    client = make_client()
    payload, meta = await client.read_area(name, 3, 2)
    assert payload == b"\x00\x01"
    assert ("read_area", area, 0, 3, 2) in fake_snap7.instances[0].calls
    assert meta["attempts"] == 1


async def test_db_writes_pass_the_enum_to_snap7(fake_snap7: type[FakeClient]) -> None:
    await make_client().write_db(5, 10, b"\x01\x02")
    assert ("write_area", Area.DB, 5, 10, b"\x01\x02") in fake_snap7.instances[0].calls


# ------------------------------------------------------------- bug 2: reconnect


async def test_link_error_disconnects_and_destroys_before_reconnecting(fake_snap7: type[FakeClient]) -> None:
    client = make_client()
    await client.ensure_connection()
    fake_snap7.script = [LINK_ERROR, None]
    payload, meta = await client.read_db(1, 0, 4)
    assert payload == b"\x00\x01\x02\x03"
    assert meta["attempts"] == 2
    first, second = fake_snap7.instances
    assert first.calls[-2:] == [("disconnect",), ("destroy",)]
    assert any(call[0] == "connect" for call in second.calls)
    assert client.connection_status()["connected"] is True


async def test_plc_error_is_reported_unchanged_and_the_session_survives(fake_snap7: type[FakeClient]) -> None:
    client = make_client()
    fake_snap7.script = [RuntimeError(b"CPU : Item not available")]
    with pytest.raises(OperationFailedError, match="CPU : Item not available") as excinfo:
        await client.read_db(999, 0, 1)
    assert "attempt" not in str(excinfo.value)  # not retried, not reported as a connection failure
    assert len(fake_snap7.instances) == 1
    assert client.connection_status()["connected"] is True
    payload, _ = await client.read_db(1, 0, 2)  # same connection still works
    assert payload == b"\x00\x01"
    assert len(fake_snap7.instances) == 1


async def test_link_errors_give_up_after_max_retries(fake_snap7: type[FakeClient]) -> None:
    client = make_client(max_retries=2)
    fake_snap7.script = [LINK_ERROR, LINK_ERROR, LINK_ERROR]
    with pytest.raises(OperationFailedError, match="after 3 attempt"):
        await client.read_db(1, 0, 1)
    status = client.connection_status()
    assert status["connected"] is False
    assert "Connection reset by peer" in status["last_error"]
    # Every client that was created was also torn down.
    for fake in fake_snap7.instances:
        assert ("destroy",) in fake.calls


def test_error_classification() -> None:
    assert is_link_error(LINK_ERROR)
    assert is_link_error(RuntimeError(b" TCP : Connection refused"))
    assert is_link_error(RuntimeError(b"CLI : Job Timeout"))
    assert is_link_error(ConnectionRefusedError())
    assert not is_link_error(RuntimeError(b"CPU : Address out of range"))
    assert not is_link_error(ValueError("bad"))


# ------------------------------------------------------------- bug 6: timeout & connection type


async def test_timeout_and_connection_type_are_applied_before_connect(fake_snap7: type[FakeClient]) -> None:
    client = make_client(timeout=2.5, connection_type="op")
    await client.ensure_connection()
    calls = fake_snap7.instances[0].calls
    connect_at = next(i for i, call in enumerate(calls) if call[0] == "connect")
    before = calls[:connect_at]
    for parameter in (Parameter.PingTimeout, Parameter.SendTimeout, Parameter.RecvTimeout):
        assert ("set_param", parameter, 2500) in before
    assert ("set_connection_type", 2) in before
    assert calls[connect_at] == ("connect", "192.0.2.1", 0, 2, 102)


async def test_unreachable_plc_is_a_clean_connection_error() -> None:
    client = S7Client(S7ClientConfig(port=free_port(), timeout=1.0, max_retries=0))
    with pytest.raises(S7ConnectionError, match="Cannot reach 127.0.0.1"):
        await client.ensure_connection()
    assert client.connection_status()["connected"] is False


# ------------------------------------------------------------- bug 3: SZL serialisation


async def test_read_szl_returns_plain_json(fake_snap7: type[FakeClient]) -> None:
    data, _ = await make_client().read_szl(0x001C, 0)
    assert data == {
        "szl_id": 0x001C,
        "szl_index": 0,
        "record_length": 4,
        "record_count": 2,
        "records_hex": ["0001abcd", "0006ef10"],
    }
    json.dumps(data)


# ------------------------------------------------------------- PLC info and CPU state


async def test_plc_info_reports_text_and_firmware_version(fake_snap7: type[FakeClient]) -> None:
    data, _ = await make_client().read_plc_info()
    assert data["module_type"] == "CPU 1516-3 PN/DP"
    assert data["order_code"] == "6ES7 516-3AN02-0AB0"
    # Was always null (S7OrderCode has V1..V3, not Version). Taken from the
    # firmware record, not the trailing boot loader record Snap7 would use.
    assert data["version"] == "V2.9.4"
    json.dumps(data)


async def test_cpu_state_failure_is_an_error_not_unknown(fake_snap7: type[FakeClient]) -> None:
    client = make_client(max_retries=0)
    await client.ensure_connection()
    fake_snap7.status_rc = CPU_ITEM_NOT_AVAILABLE
    with pytest.raises(OperationFailedError, match="Item not available"):
        await client.read_cpu_state()
    fake_snap7.status_rc = 0
    data, _ = await client.read_cpu_state()
    assert data == {"state": "S7CpuStatusRun", "mode": "RUN"}


async def test_refused_cpu_command_is_an_error(fake_snap7: type[FakeClient]) -> None:
    fake_snap7.command_rc = CPU_CANNOT_STOP  # python-snap7's plc_stop returns this without raising
    with pytest.raises(OperationFailedError, match="Cannot stop PLC") as excinfo:
        await make_client().set_cpu_state("STOP")
    assert (excinfo.value.outcome, excinfo.value.request_sent) == ("rejected", True)


async def test_cpu_command_succeeds_only_once_the_mode_is_reached(fake_snap7: type[FakeClient]) -> None:
    fake_snap7.cpu_status = 0x08  # stays in RUN although STOP was accepted
    with pytest.raises(OperationFailedError, match="still reports S7CpuStatusRun") as excinfo:
        await make_client(timeout=0.3).set_cpu_state("STOP")
    assert excinfo.value.outcome == "unknown" and "check before retrying" in str(excinfo.value)
    fake_snap7.cpu_status = 0x04
    observed, _ = await make_client().set_cpu_state("stop")
    assert observed["mode"] == "STOP"


async def test_already_in_requested_mode_counts_as_success(fake_snap7: type[FakeClient]) -> None:
    fake_snap7.command_rc = CPU_ALREADY_RUN
    observed, meta = await make_client().set_cpu_state("RUN")
    assert observed["mode"] == "RUN" and meta["already_in_state"] is True


# ------------------------------------------------------------- bug 5: single-bit writes


async def test_bool_writes_use_the_bit_transport_size(fake_snap7: type[FakeClient]) -> None:
    await make_client().write_bit("output", 3, 5, True)
    call = next(c for c in fake_snap7.instances[0].calls if c[0] == "Cli_WriteArea")
    _, area, db, start, amount, wordlen, value = call
    assert (area, db, start, amount, wordlen, value) == (Area.PA, 0, 3 * 8 + 5, 1, WordLen.Bit, 1)
    assert not any(c[0] == "write_area" for c in fake_snap7.instances[0].calls)


async def test_bit_index_is_validated() -> None:
    with pytest.raises(ValueError):
        await make_client().write_bit("db", 0, 8, True, db_number=1)


def test_ctypes_byref_exposes_the_buffer() -> None:
    # FakeLib relies on this; keep it explicit so a ctypes change is obvious.
    value = ctypes.c_int(0)
    ctypes.byref(value)._obj.value = 7
    assert value.value == 7


async def test_link_error_after_a_write_was_sent_is_not_retried(fake_snap7: type[FakeClient]) -> None:
    client = make_client(max_retries=3)
    await client.ensure_connection()
    fake_snap7.script = [None, LINK_ERROR]  # probe read succeeds, the write's reply is lost
    with pytest.raises(OperationFailedError, match="may have been applied; check before retrying") as excinfo:
        await client.write_db(1, 0, b"\x01")
    assert (excinfo.value.outcome, excinfo.value.request_sent) == ("unknown", True)
    writes = [call for fake in fake_snap7.instances for call in fake.calls if call[0] == "write_area"]
    assert len(writes) == 1


async def test_link_error_in_the_probe_reconnects_before_writing(fake_snap7: type[FakeClient]) -> None:
    client = make_client(max_retries=3)
    await client.ensure_connection()
    fake_snap7.script = [LINK_ERROR, None, None]  # stale link found by the probe
    meta = await client.write_db(1, 0, b"\x01")
    assert meta["attempts"] == 2
    writes = [call for fake in fake_snap7.instances for call in fake.calls if call[0] == "write_area"]
    assert len(writes) == 1 and len(fake_snap7.instances) == 2


@pytest.mark.parametrize(
    ("payload_size", "error", "expected"),
    [
        (4, b"CPU : Address out of range", "rejected"),
        (4, b"CLI : function refused by CPU (Unknown error)", "rejected"),
        (4, b"CLI : Partial data written", "unknown"),
        (4, b"CLI : Invalid PLC answer", "unknown"),
        (300, b"CPU : Address out of range", "unknown"),  # may span several PDUs
        (300, b"CLI : Partial data written", "unknown"),
    ],
)
async def test_errors_after_the_send_are_classified(
    fake_snap7: type[FakeClient], payload_size: int, error: bytes, expected: str
) -> None:
    client = make_client(max_retries=3)
    fake_snap7.script = [None, RuntimeError(error)]  # probe read passes, the write fails
    with pytest.raises(OperationFailedError) as excinfo:
        await client.write_db(1, 0, b"\x01" * payload_size)
    assert (excinfo.value.outcome, excinfo.value.request_sent) == (expected, True)
    if expected == "unknown":
        assert "may have been applied" in str(excinfo.value)
    writes = [call for fake in fake_snap7.instances for call in fake.calls if call[0] == "write_area"]
    assert len(writes) == 1


async def test_write_probe_reads_the_whole_target_range(fake_snap7: type[FakeClient]) -> None:
    await make_client().write_db(4, 10, b"\x01" * 900)
    reads = [call for call in fake_snap7.instances[0].calls if call[0] == "read_area"]
    assert reads == [("read_area", Area.DB, 4, 10, 900)]


async def test_failed_mode_poll_after_an_accepted_command_is_unknown(
    fake_snap7: type[FakeClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    client = make_client()

    async def broken_poll() -> Any:
        raise OperationFailedError("read_cpu_state failed after 1 attempt(s): TCP : Connection refused")

    monkeypatch.setattr(client, "read_cpu_state", broken_poll)
    with pytest.raises(OperationFailedError, match="accepted the STOP command") as excinfo:
        await client.set_cpu_state("STOP")
    assert (excinfo.value.outcome, excinfo.value.request_sent) == ("unknown", True)
