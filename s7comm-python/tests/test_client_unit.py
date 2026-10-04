"""S7Client logic against a scripted fake of python-snap7's Client."""

from __future__ import annotations

import asyncio
import ctypes
import json
import threading
import time
from typing import Any

import anyio
import pytest
from snap7.type import S7SZL, Area, Parameter, S7CpuInfo, WordLen
from support import free_port

from s7comm_mcp import s7_client
from s7comm_mcp.config import ConfigError
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
            step = FakeClient.script.pop(0)
            if callable(step) and not isinstance(step, BaseException):
                step()  # e.g. a blocking hang
            elif step is not None:
                raise step

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

    @property
    def writes(self) -> int:
        return sum(1 for call in self.calls if call[0] == "write_area")

    def plc_stop(self) -> int:
        self.calls.append(("plc_stop",))
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


# ------------------------------------------------------------- rule 1a: local failures before the send


async def test_failure_while_building_the_request_is_not_sent(fake_snap7: type[FakeClient]) -> None:
    # python-snap7 raises a plain Python error while preparing the call, before Snap7 sends anything.
    fake_snap7.script = [None, TypeError("unsupported operand type(s)")]
    with pytest.raises(OperationFailedError) as excinfo:
        await make_client().write_db(1, 0, b"\x01")
    assert (excinfo.value.outcome, excinfo.value.request_sent) == ("not_sent", False)


# ------------------------------------------------------------- rule 12: overall deadline


def hang(seconds: float, done: threading.Event | None = None) -> Any:
    def _block() -> None:
        time.sleep(seconds)
        if done is not None:
            done.set()

    return _block


def fast_client() -> S7Client:
    client = make_client(timeout=0.1, max_retries=0)  # deadline = 0.1 * 6 + 1 = 1.6 s
    assert client.request_deadline() == pytest.approx(1.6)
    return client


async def test_a_hung_read_ends_with_a_clean_error_at_the_deadline(fake_snap7: type[FakeClient]) -> None:
    client = fast_client()
    done = threading.Event()
    fake_snap7.script = [hang(3.0, done)]
    started = time.monotonic()
    with pytest.raises(OperationFailedError, match="no answer within the tool call's 1.6s deadline"):
        await client.read_db(1, 0, 4)
    assert time.monotonic() - started < 2.5
    done.wait(5)


async def test_a_hung_write_after_the_send_is_unknown(fake_snap7: type[FakeClient]) -> None:
    client = fast_client()
    done = threading.Event()
    fake_snap7.script = [None, hang(3.0, done)]  # the probe answers, the write never does
    with pytest.raises(OperationFailedError, match="may have been applied; check before retrying") as excinfo:
        await client.write_db(1, 0, b"\x01")
    assert (excinfo.value.outcome, excinfo.value.request_sent) == ("unknown", True)
    done.wait(5)
    assert fake_snap7.instances[0].writes == 1


async def test_a_write_stuck_before_the_send_is_not_sent_and_never_sent_later(fake_snap7: type[FakeClient]) -> None:
    client = fast_client()
    done = threading.Event()
    fake_snap7.script = [hang(2.5, done)]  # the pre-send probe hangs past the deadline
    with pytest.raises(OperationFailedError, match="the request was not sent") as excinfo:
        await client.write_db(1, 0, b"\x01")
    assert (excinfo.value.outcome, excinfo.value.request_sent) == ("not_sent", False)
    done.wait(5)
    time.sleep(0.2)  # let the abandoned worker finish
    assert sum(fake.writes for fake in fake_snap7.instances) == 0


async def test_a_write_queued_behind_a_hung_call_is_never_sent(fake_snap7: type[FakeClient]) -> None:
    client = fast_client()
    done = threading.Event()
    fake_snap7.script = [hang(3.5, done)]
    stuck_read = asyncio.create_task(client.read_db(1, 0, 1))
    await asyncio.sleep(0.2)
    with pytest.raises(OperationFailedError) as excinfo:
        await client.write_db(1, 0, b"\x01")  # waits for the lock until its deadline
    assert excinfo.value.outcome == "not_sent"
    with pytest.raises(OperationFailedError):
        await stuck_read
    done.wait(5)
    time.sleep(0.3)
    assert sum(fake.writes for fake in fake_snap7.instances) == 0


async def test_the_next_request_after_a_deadline_starts_a_fresh_connection(fake_snap7: type[FakeClient]) -> None:
    client = fast_client()
    done = threading.Event()
    fake_snap7.script = [hang(2.0, done)]
    with pytest.raises(OperationFailedError):
        await client.read_db(1, 0, 1)
    done.wait(5)
    payload, _ = await client.read_db(1, 0, 2)
    assert payload == b"\x00\x01" and len(fake_snap7.instances) == 2
    assert ("disconnect",) in fake_snap7.instances[0].calls


# ------------------------------------------------------------- client cancellation (rules 1/12)


def all_calls(fake_snap7: type[FakeClient], name: str) -> int:
    return sum(1 for fake in fake_snap7.instances for call in fake.calls if call[0] == name)


async def test_a_write_cancelled_while_queued_is_never_sent(fake_snap7: type[FakeClient]) -> None:
    client = make_client(timeout=5.0)
    done = threading.Event()
    fake_snap7.script = [hang(1.5, done)]  # an earlier read holds the client lock
    stuck_read = asyncio.create_task(client.read_db(1, 0, 1))
    await asyncio.sleep(0.2)
    with anyio.move_on_after(0.3) as scope:  # the MCP client cancels the queued write
        await client.write_db(1, 0, b"\x5a")
    assert scope.cancelled_caught
    await stuck_read
    done.wait(5)
    time.sleep(0.3)  # the abandoned worker gets the lock now; it must not send
    assert all_calls(fake_snap7, "write_area") == 0


async def test_a_write_cancelled_while_connecting_is_never_sent(
    fake_snap7: type[FakeClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    finished = threading.Event()

    def slow_probe(self: S7Client) -> None:
        time.sleep(1.0)  # a slow TCP connect
        finished.set()

    monkeypatch.setattr(S7Client, "_probe_tcp", slow_probe)
    client = make_client(timeout=5.0)
    with anyio.move_on_after(0.3):
        await client.write_db(1, 0, b"\x5a")
    finished.wait(5)
    time.sleep(0.3)
    assert all_calls(fake_snap7, "write_area") == 0


async def test_a_cpu_command_cancelled_while_queued_is_never_sent(fake_snap7: type[FakeClient]) -> None:
    client = make_client(timeout=5.0)
    done = threading.Event()
    fake_snap7.script = [hang(1.5, done)]
    stuck_read = asyncio.create_task(client.read_db(1, 0, 1))
    await asyncio.sleep(0.2)
    with anyio.move_on_after(0.3):
        await client.set_cpu_state("STOP")
    await stuck_read
    done.wait(5)
    time.sleep(0.3)
    assert all_calls(fake_snap7, "plc_stop") == 0


async def test_a_write_cancelled_after_the_send_is_not_resent(fake_snap7: type[FakeClient]) -> None:
    client = make_client(timeout=5.0)
    done = threading.Event()
    fake_snap7.script = [None, hang(1.0, done)]  # sent, then the reply is slow
    with anyio.move_on_after(0.3):
        await client.write_db(1, 0, b"\x5a")
    done.wait(5)
    time.sleep(0.3)
    assert all_calls(fake_snap7, "write_area") == 1


def test_default_deadline_stays_below_client_timeouts() -> None:
    client = S7Client(S7ClientConfig())  # S7_TIMEOUT 5, S7_MAX_RETRIES 3
    assert client.request_deadline() == 45.0  # not 124.5: decided before a 60 s client timeout
    assert client.request_deadline(4096) > 45.0  # large transfers get time per extra PDU
    assert S7Client(S7ClientConfig(request_deadline=20)).request_deadline() == 20.0


async def test_no_request_starts_after_the_client_disconnected(fake_snap7: type[FakeClient]) -> None:
    # E.g. the next entry of a batch, or a follow-up after the client went away.
    client = make_client()
    client.begin_shutdown()
    with pytest.raises(OperationFailedError, match="disconnected") as excinfo:
        await client.write_db(1, 0, b"\x01")
    assert excinfo.value.outcome == "not_sent"
    assert all_calls(fake_snap7, "write_area") == 0 and all_calls(fake_snap7, "connect") == 0


async def test_disconnect_cancels_a_queued_write(fake_snap7: type[FakeClient]) -> None:
    client = make_client(timeout=5.0)
    done = threading.Event()
    fake_snap7.script = [hang(1.0, done)]
    stuck_read = asyncio.create_task(client.read_db(1, 0, 1))
    await asyncio.sleep(0.2)
    queued = asyncio.create_task(client.write_db(1, 0, b"\x5a"))
    await asyncio.sleep(0.2)
    client.begin_shutdown()  # stdin EOF
    with pytest.raises(OperationFailedError) as excinfo:
        await queued
    assert excinfo.value.outcome == "not_sent"
    await stuck_read  # it was already executing, so it simply completes
    done.wait(5)
    assert all_calls(fake_snap7, "write_area") == 0


# ------------------------------------------------------------- one deadline per tool call


def test_begin_send_refuses_after_the_deadline() -> None:
    from s7comm_mcp.s7_client import _Call

    late = _Call(not_after=time.monotonic() - 0.01)
    assert late.begin_send() is False and late.expired and not late.sent
    timely = _Call(not_after=time.monotonic() + 10)
    assert timely.begin_send() is True and timely.sent


async def test_requests_in_one_tool_call_share_one_deadline(fake_snap7: type[FakeClient]) -> None:
    client = fast_client()  # 1.6 s per tool call
    fake_snap7.script = [hang(1.0), hang(1.0)]  # a header-like read, then a slow pre-send check
    with client.tool_call():
        await client.read_db(1, 0, 2)  # 1.0 s of the call's 1.6 s
        with pytest.raises(OperationFailedError) as excinfo:
            await client.write_db(1, 0, b"\x01")  # would end at 2.0 s: past the call's deadline
    assert excinfo.value.outcome == "not_sent"
    time.sleep(1.2)  # let the abandoned worker finish its check
    assert all_calls(fake_snap7, "write_area") == 0


def test_s7_timeout_must_be_below_the_request_deadline() -> None:
    with pytest.raises(ConfigError, match="must be smaller than S7_REQUEST_DEADLINE"):
        S7ClientConfig(timeout=10, request_deadline=10)
    with pytest.raises(ConfigError, match="must be smaller than S7_REQUEST_DEADLINE"):
        S7ClientConfig(timeout=60, request_deadline=45)
    assert S7ClientConfig(timeout=5, request_deadline=6).timeout == 5
