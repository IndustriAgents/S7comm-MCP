"""Thread-safe wrapper around python-snap7 with retries and honest errors.

python-snap7 is pinned to 2.x (see ``pyproject.toml``). Two helpers below call
the bundled Snap7 library directly through ``Client._lib``/``Client._s7_client``
because the 2.x Python wrapper does not expose what they need:

* single-bit writes (``WordLen.Bit``) - ``Client.write_area`` always writes
  whole bytes, and a read-modify-write of the byte would race the PLC program;
* the CPU status call - ``Client.get_cpu_state`` ignores the library's return
  code and reports ``S7CpuStatusUnknown`` when the request failed.
"""

from __future__ import annotations

import ctypes
import logging
import math
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import anyio
import snap7
from snap7.error import check_error
from snap7.type import Area, Parameter, WordLen

from .config import ConfigError, env_bool, env_float, env_int, env_str

logger = logging.getLogger(__name__)

OperationMeta = dict[str, Any]
OperationResult = tuple[Any, OperationMeta]
OpCallable = Callable[[Any], Any]

#: Memory areas reachable through the tools, keyed by the names the tools use.
AREAS: dict[str, Area] = {
    "db": Area.DB,
    "input": Area.PE,
    "output": Area.PA,
    "marker": Area.MK,
}

#: Snap7 connection resource types (``Cli_SetConnectionType``).
CONNECTION_TYPES: dict[str, int] = {"PG": 1, "OP": 2, "S7BASIC": 3}

#: CPU status codes returned by ``Cli_GetPlcStatus``.
CPU_STATUSES: dict[int, str] = {
    0x00: "S7CpuStatusUnknown",
    0x04: "S7CpuStatusStop",
    0x08: "S7CpuStatusRun",
}
CPU_MODES: dict[str, str] = {
    "S7CpuStatusUnknown": "UNKNOWN",
    "S7CpuStatusStop": "STOP",
    "S7CpuStatusRun": "RUN",
}

#: S7 addresses carry a 24-bit bit address, so byte offsets stop at 2**21 - 1.
MAX_BYTE_OFFSET = 0x1FFFFF

#: Largest write Snap7 always sends as one PDU: the smallest PDU an S7 CPU may
#: negotiate is 240 bytes and the write headers take at most 35 of them. A
#: longer write may be split into several requests (452-byte chunks at the
#: mock's 480-byte PDU), and a failure part-way leaves it partly applied.
SINGLE_PDU_WRITE_MAX = 240 - 35

#: Snap7 errors that mean the PLC explicitly refused a request (a negative
#: return code for it), so a single-PDU request was not applied.
REFUSAL_MARKERS = ("CPU :", "CLI : FUNCTION REFUSED BY CPU")


def is_refusal(message: str) -> bool:
    return message.strip().upper().startswith(REFUSAL_MARKERS)


@dataclass(slots=True)
class S7ClientConfig:
    """Runtime configuration for a Snap7 client."""

    host: str = "127.0.0.1"
    port: int = 102
    rack: int = 0
    slot: int = 2
    connection_type: str = "PG"
    timeout: float = 5.0
    max_retries: int = 3
    retry_backoff_base: float = 0.5
    debug: bool = False

    def __post_init__(self) -> None:
        if not self.host:
            raise ConfigError("S7_HOST must not be empty")
        self.connection_type = self.connection_type.strip().upper().replace("_", "").replace("-", "")
        if self.connection_type not in CONNECTION_TYPES:
            raise ConfigError(f"S7_CONNECTION_TYPE={self.connection_type!r} is not supported (use PG, OP or S7BASIC)")
        if self.timeout <= 0:
            raise ConfigError("S7_TIMEOUT must be greater than 0")

    @property
    def connection_type_code(self) -> int:
        return CONNECTION_TYPES[self.connection_type]

    @property
    def timeout_ms(self) -> int:
        return max(1, int(round(self.timeout * 1000)))

    @classmethod
    def from_env(cls) -> S7ClientConfig:
        """Build the configuration from ``S7_*`` environment variables."""
        return cls(
            host=env_str("S7_HOST", "127.0.0.1"),
            port=env_int("S7_PORT", 102, minimum=1, maximum=65535),
            rack=env_int("S7_RACK", 0, minimum=0, maximum=7),
            slot=env_int("S7_SLOT", 2, minimum=0, maximum=31),
            connection_type=env_str("S7_CONNECTION_TYPE", "PG"),
            timeout=env_float("S7_TIMEOUT", 5.0, minimum=0.1, maximum=600.0),
            max_retries=env_int("S7_MAX_RETRIES", 3, minimum=0, maximum=10),
            retry_backoff_base=env_float("S7_RETRY_BACKOFF_BASE", 0.5, minimum=0.0, maximum=60.0),
            debug=env_bool("S7_DEBUG", False),
        )


class S7ClientError(RuntimeError):
    """Raised when an S7 operation fails."""


class S7ConnectionError(S7ClientError):
    """Raised when the PLC cannot be reached or the connection broke."""


class OperationFailedError(S7ClientError):
    """Raised when an S7 operation fails (after retries, for link errors).

    ``outcome`` says what happened to a write or CPU command: ``not_sent``
    (refused or failed before the request went out), ``rejected`` (the CPU
    refused a single-PDU request, so nothing was applied) or ``unknown``
    (sent, then anything else went wrong: a lost or garbled reply, a partly
    applied multi-PDU write, a failed read-back; it may have been applied).
    """

    def __init__(self, message: str, *, outcome: str = "not_sent") -> None:
        super().__init__(message)
        self.outcome = outcome

    @property
    def request_sent(self) -> bool:
        return self.outcome in ("rejected", "unknown")


class WriteOutcomeUnknownError(OperationFailedError):
    """A write or CPU command was sent, but the link failed before the reply.

    The PLC may or may not have applied it. It is never resent automatically.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, outcome="unknown")


#: Areas the client will write to. Process-image inputs are read-only.
WRITABLE_AREAS = frozenset({"db", "output", "marker"})


def writable_area_for(name: str) -> Area:
    area = area_for(name)
    if name not in WRITABLE_AREAS:
        raise ValueError(f"Area '{name}' is read-only; writable areas: {', '.join(sorted(WRITABLE_AREAS))}")
    return area


def area_for(name: str) -> Area:
    """Map a tool-level area name to the python-snap7 ``Area`` enum."""
    try:
        return AREAS[name]
    except KeyError:
        raise ValueError(f"Unsupported area '{name}'. Valid areas: {', '.join(AREAS)}") from None


def describe_error(exc: BaseException) -> str:
    """Render a python-snap7 error (whose message is ``bytes``) as plain text."""
    if exc.args and isinstance(exc.args[0], (bytes, bytearray)):
        return bytes(exc.args[0]).decode("latin-1", errors="replace").strip()
    return str(exc).strip() or type(exc).__name__


def is_snap7_error(exc: BaseException) -> bool:
    """True for an error the Snap7 library reported (RuntimeError with a bytes message).

    python-snap7 raises anything else (TypeError, ValueError, ...) from its own
    Python code while preparing a call, i.e. before the library sent anything.
    """
    return isinstance(exc, RuntimeError) and bool(exc.args) and isinstance(exc.args[0], (bytes, bytearray))


def is_link_error(exc: BaseException) -> bool:
    """True when an error means the TCP/ISO link is gone, so a reconnect may help.

    Errors the CPU reports for a request (``CPU : Item not available``,
    ``CPU : Address out of range`` ...) leave the connection intact and are
    returned to the caller as they are, without reconnecting or retrying.
    """
    if isinstance(exc, (S7ConnectionError, OSError)):
        return True
    if not isinstance(exc, RuntimeError):
        return False
    message = describe_error(exc).upper()
    return "TCP :" in message or "ISO :" in message or "TIMEOUT" in message


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("latin-1", errors="replace")
    text = str(value).strip("\x00 ").strip()
    return text or None


def _cpu_status(client: Any) -> str:
    status = ctypes.c_int(0)
    code = client._lib.Cli_GetPlcStatus(client._s7_client, ctypes.byref(status))
    check_error(code, context="client")
    try:
        return CPU_STATUSES[status.value]
    except KeyError:
        raise S7ClientError(f"PLC reported an unknown CPU status code 0x{status.value:02x}") from None


def _write_bit(client: Any, area: Area, db_number: int, byte_offset: int, bit: int, value: bool) -> None:
    data = (ctypes.c_uint8 * 1)(1 if value else 0)
    code = client._lib.Cli_WriteArea(
        client._s7_client,
        area,
        db_number,
        byte_offset * 8 + bit,
        1,
        WordLen.Bit,
        ctypes.byref(data),
    )
    check_error(code, context="client")


def _szl_to_dict(szl: Any, szl_id: int, szl_index: int) -> dict[str, Any]:
    record_length = int(szl.Header.LengthDR)
    record_count = int(szl.Header.NDR)
    total = record_length * record_count
    if total > ctypes.sizeof(szl.Data):
        raise S7ClientError(
            f"SZL answer claims {record_count} records of {record_length} bytes, more than the buffer holds"
        )
    raw = ctypes.string_at(ctypes.addressof(szl.Data), total)
    records = [raw[i * record_length : (i + 1) * record_length].hex() for i in range(record_count)]
    return {
        "szl_id": szl_id,
        "szl_index": szl_index,
        "record_length": record_length,
        "record_count": record_count,
        "records_hex": records,
    }


def _identity_from_szl(records_hex: list[str]) -> tuple[str | None, str | None]:
    """Order code and firmware version from SZL 0x0011 (module identification).

    Each 28-byte record is INDEX, MLFB[20], BGTyp, Ausbg, Ausbe. Index 0x0001
    names the module's order code; index 0x0007 is the firmware, whose
    Ausbg/Ausbe hold 'V', major, minor, patch. (Snap7's get_order_code takes the
    last three bytes of the list instead, which on CPUs that append a boot
    loader record, the mock included, is the boot loader's version.)
    """
    order_code: str | None = None
    version: str | None = None
    for record_hex in records_hex:
        record = bytes.fromhex(record_hex)
        if len(record) < 28:
            continue
        index = int.from_bytes(record[0:2], "big")
        if index == 0x0001:
            order_code = _text(record[2:22])
        elif index == 0x0007 and record[24] == ord("V"):
            version = f"V{record[25]}.{record[26]}.{record[27]}"
    return order_code, version


class _Call:
    """Handshake between the async caller and the worker thread of one request.

    The worker cannot be killed when the caller's deadline expires, so it must
    not send the request afterwards: begin_send() and cancel() decide, under
    one lock, whether the request went out before the caller gave up.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.cancelled = False
        self.sent = False

    def begin_send(self) -> bool:
        with self._lock:
            if self.cancelled:
                return False
            self.sent = True
            return True

    def cancel(self) -> bool:
        """Give up on the request; returns whether it had already been sent."""
        with self._lock:
            self.cancelled = True
            return self.sent


class _Abandoned(Exception):
    """The caller's deadline expired; the worker stops without sending."""


def _safe(call: Callable[[], Any]) -> None:
    try:
        call()
    except Exception:  # noqa: BLE001 - best-effort cleanup
        logger.debug("ignored error during snap7 cleanup", exc_info=True)


class S7Client:
    """Serialises Snap7 calls and exposes them as async helpers for the tools.

    The PLC connection is opened on first use, not at server start, so the MCP
    server answers ``initialize`` and ``tools/list`` without a reachable PLC.
    """

    def __init__(self, config: S7ClientConfig | None = None) -> None:
        self.config = config or S7ClientConfig.from_env()
        self._client: Any = None
        self._lock = threading.RLock()
        self._connected = False
        self._last_error: str | None = None
        self._reset_requested = False

    # -----------------------------
    # Overall deadline
    # -----------------------------

    def request_deadline(self, size: int = 0) -> float:
        """Upper bound, in seconds, for one request including reconnects and retries.

        Snap7's own timeouts normally end a request much earlier; this is the
        guarantee that no call can hang, whatever the PLC (or Snap7) does.
        """
        chunks = max(1, math.ceil(size / SINGLE_PDU_WRITE_MAX))
        per_attempt = self.config.timeout * (4 + 2 * chunks)  # TCP probe, connect, pre-check, request
        backoff = sum(self.config.retry_backoff_base * 2**i for i in range(self.config.max_retries))
        return per_attempt * (self.config.max_retries + 1) + backoff + 1.0

    async def _run(
        self,
        op_name: str,
        operation: OpCallable,
        probe: OpCallable | None = None,
        single_pdu: bool = True,
        *,
        size: int = 0,
    ) -> OperationResult:
        """Run ``_execute`` in a worker thread under :meth:`request_deadline`.

        On expiry a write or command that was already sent is "unknown"; one
        that was not is "not_sent" and is guaranteed never to be sent later. A
        read fails with a clean error. The connection is reset before the next
        request.
        """
        call = _Call()
        deadline = self.request_deadline(size)
        try:
            with anyio.fail_after(deadline):
                return await anyio.to_thread.run_sync(
                    self._execute, op_name, operation, probe, single_pdu, call, abandon_on_cancel=True
                )
        except TimeoutError:
            sent = call.cancel()
            self._reset_requested = True
            message = f"{op_name}: no answer within the {deadline:g}s deadline"
            self._last_error = message
            if probe is not None and sent:
                raise WriteOutcomeUnknownError(
                    f"{message}. The request was sent once and may have been applied; check before retrying. "
                    "It was not resent."
                ) from None
            if probe is not None:
                raise OperationFailedError(f"{message}; the request was not sent") from None
            raise OperationFailedError(message) from None

    # -----------------------------
    # Async API used by the tools
    # -----------------------------

    async def ensure_connection(self) -> OperationMeta:
        """Connect if needed; raises :class:`S7ConnectionError` on failure."""
        _, meta = await self._run("connect", lambda client: None)
        return meta

    async def close(self) -> None:
        try:
            with anyio.fail_after(self.config.timeout + 1.0):
                await anyio.to_thread.run_sync(self._close_sync, abandon_on_cancel=True)
        except TimeoutError:
            # A request is still stuck in Snap7; it resets the link when it ends.
            self._reset_requested = True
            logger.warning("Closing the PLC connection timed out; a request is still pending")

    async def read_area(self, area: str, start: int, size: int, db_number: int = 0) -> OperationResult:
        area_enum = area_for(area)
        label = f"read_area(area={area},db={db_number},start={start},size={size})"
        return await self._run(
            label,
            lambda client: bytes(client.read_area(area_enum, db_number, start, size)),
            size=size,
        )

    async def write_area(self, area: str, start: int, payload: bytes, db_number: int = 0) -> OperationMeta:
        if not payload:
            raise ValueError("payload must not be empty")
        area_enum = writable_area_for(area)
        label = f"write_area(area={area},db={db_number},start={start},size={len(payload)})"
        _, meta = await self._run(
            label,
            lambda client: client.write_area(area_enum, db_number, start, bytearray(payload)),
            # The probe reads the whole target range, so a range the PLC does
            # not have is refused before anything is sent (a multi-PDU write
            # would otherwise be applied up to the end of the area).
            lambda client: client.read_area(area_enum, db_number, start, len(payload)),
            len(payload) <= SINGLE_PDU_WRITE_MAX,
            size=len(payload),
        )
        return meta

    async def write_bit(self, area: str, byte_offset: int, bit: int, value: bool, db_number: int = 0) -> OperationMeta:
        """Write one bit with the S7 BIT transport size; the other 7 bits are untouched."""
        if not 0 <= bit <= 7:
            raise ValueError("bit index must be between 0 and 7")
        area_enum = writable_area_for(area)
        label = f"write_bit(area={area},db={db_number},byte={byte_offset},bit={bit})"
        _, meta = await self._run(
            label,
            lambda client: _write_bit(client, area_enum, db_number, byte_offset, bit, bool(value)),
            lambda client: client.read_area(area_enum, db_number, byte_offset, 1),
        )
        return meta

    async def read_db(self, db_number: int, start_offset: int, size: int) -> OperationResult:
        return await self.read_area("db", start_offset, size, db_number)

    async def write_db(self, db_number: int, start_offset: int, payload: bytes) -> OperationMeta:
        return await self.write_area("db", start_offset, payload, db_number)

    async def read_plc_info(self) -> OperationResult:
        def _op(client: Any) -> dict[str, Any]:
            cpu_info = client.get_cpu_info()
            identity = _szl_to_dict(client.read_szl(0x0011, 0), 0x0011, 0)
            order_code, version = _identity_from_szl(identity["records_hex"])
            return {
                "module_type": _text(cpu_info.ModuleTypeName),
                "module_name": _text(cpu_info.ModuleName),
                "serial_number": _text(cpu_info.SerialNumber),
                "as_name": _text(cpu_info.ASName),
                "copyright": _text(cpu_info.Copyright),
                "order_code": order_code,
                # None when the CPU has no firmware record; never guessed.
                "version": version,
            }

        return await self._run("read_plc_info", _op)

    async def read_cpu_state(self) -> OperationResult:
        def _op(client: Any) -> dict[str, str]:
            state = _cpu_status(client)
            return {"state": state, "mode": CPU_MODES[state]}

        return await self._run("read_cpu_state", _op)

    async def set_cpu_state(self, state: str) -> OperationResult:
        """Send RUN (hot start) or STOP, then poll until the CPU reports that mode.

        Returns the observed state. Raises :class:`OperationFailedError` when the
        PLC refuses the command or has not reached the mode within ``S7_TIMEOUT``.
        """
        normalized = state.strip().upper()
        if normalized not in {"RUN", "STOP"}:
            raise ValueError("state must be RUN or STOP")

        def _command(client: Any) -> None:
            code = client.plc_hot_start() if normalized == "RUN" else client.plc_stop()
            check_error(code, context="client")

        try:
            _, meta = await self._run(f"set_cpu_state({normalized})", _command, _cpu_status)
        except WriteOutcomeUnknownError:
            raise
        except OperationFailedError as exc:
            # Snap7 reports "already RUN/STOP" as an error; the poll below
            # still has to confirm the mode before this counts as success.
            if "ALREADY" not in str(exc).upper():
                raise
            meta = {"attempts": 1, "already_in_state": True}
        # From here on the PLC has accepted the command: a failing poll must not
        # turn that into "not sent".
        deadline = time.monotonic() + self.config.timeout
        try:
            while True:
                observed, _ = await self.read_cpu_state()
                if observed["mode"] == normalized or time.monotonic() >= deadline:
                    break
                await anyio.sleep(0.2)
        except Exception as exc:
            raise OperationFailedError(
                f"The PLC accepted the {normalized} command but its mode could not be read back "
                f"({describe_error(exc)}); it may have been applied, check before retrying",
                outcome="unknown",
            ) from exc
        if observed["mode"] != normalized:
            raise OperationFailedError(
                f"The PLC accepted the {normalized} command but still reports {observed['state']} "
                f"after {self.config.timeout:g}s; it may still be changing mode, check before retrying",
                outcome="unknown",
            )
        return observed, meta

    async def read_system_time(self) -> OperationResult:
        return await self._run("read_system_time", lambda client: client.get_plc_datetime().isoformat())

    async def read_szl(self, szl_id: int, szl_index: int) -> OperationResult:
        label = f"read_szl(id=0x{szl_id:04x},index=0x{szl_index:04x})"
        return await self._run(
            label,
            lambda client: _szl_to_dict(client.read_szl(szl_id, szl_index), szl_id, szl_index),
        )

    def connection_status(self) -> dict[str, Any]:
        return {
            "connected": self._connected,
            "host": self.config.host,
            "port": self.config.port,
            "rack": self.config.rack,
            "slot": self.config.slot,
            "connection_type": self.config.connection_type,
            "timeout_s": self.config.timeout,
            "last_error": self._last_error,
        }

    # -----------------------------
    # Internal helpers (run in a worker thread)
    # -----------------------------

    def _reset_locked(self) -> None:
        """Disconnect and destroy the current Snap7 client, if any."""
        client, self._client, self._connected = self._client, None, False
        if client is not None:
            _safe(client.disconnect)
            _safe(client.destroy)

    def _probe_tcp(self) -> None:
        # Snap7 leaves the TCP connect to the OS, which can block for over a
        # minute on an unreachable host; this probe bounds it by S7_TIMEOUT.
        try:
            with socket.create_connection((self.config.host, self.config.port), timeout=self.config.timeout):
                pass
        except OSError as exc:
            raise S7ConnectionError(
                f"Cannot reach {self.config.host}:{self.config.port} within {self.config.timeout:g}s: "
                f"{describe_error(exc)}"
            ) from exc

    def _connect_locked(self) -> None:
        self._reset_locked()
        self._probe_tcp()
        client = snap7.client.Client()
        try:
            client.set_param(Parameter.PingTimeout, self.config.timeout_ms)
            client.set_param(Parameter.SendTimeout, self.config.timeout_ms)
            client.set_param(Parameter.RecvTimeout, self.config.timeout_ms)
            client.set_connection_type(self.config.connection_type_code)
            client.connect(self.config.host, self.config.rack, self.config.slot, tcp_port=self.config.port)
        except Exception as exc:
            _safe(client.destroy)
            raise S7ConnectionError(
                f"Failed to connect to {self.config.host}:{self.config.port} "
                f"(rack={self.config.rack}, slot={self.config.slot}): {describe_error(exc)}"
            ) from exc
        self._client = client
        self._connected = True
        logger.info(
            "Connected to S7 PLC %s:%s rack=%s slot=%s",
            self.config.host,
            self.config.port,
            self.config.rack,
            self.config.slot,
        )

    def _close_sync(self) -> None:
        with self._lock:
            self._reset_locked()

    def _execute(
        self,
        op_name: str,
        operation: OpCallable,
        probe: OpCallable | None = None,
        single_pdu: bool = True,
        call: _Call | None = None,
    ) -> OperationResult:
        """Run ``operation`` on a connected client, reconnecting after link errors.

        Reads (no ``probe``) are simply repeated after a reconnect. Writes and
        CPU commands pass a ``probe``: a small read that checks the link right
        before the request. Connecting and the probe may be retried, but the
        operation itself is sent at most once. If the link fails after it was
        sent, :class:`WriteOutcomeUnknownError` reports that it may have been
        applied, and nothing is resent.

        After the send, only an explicit refusal by the CPU (see
        :func:`is_refusal`) of a ``single_pdu`` request counts as "rejected"
        (nothing applied). Anything else (a partial multi-PDU write, a garbled
        answer, an unexpected client error) is "unknown".
        """
        start = time.perf_counter()
        attempt = 0
        send_once = probe is not None
        with self._lock:
            if self._reset_requested:
                # An earlier request was abandoned at its deadline: start clean.
                self._reset_requested = False
                self._reset_locked()
            while True:
                attempt += 1
                sent = False
                try:
                    if call is not None and call.cancelled:
                        raise _Abandoned
                    if not self._connected:
                        self._connect_locked()
                    if probe is not None:
                        probe(self._client)
                    # The last point at which the caller may still give up.
                    if call is not None and not call.begin_send():
                        raise _Abandoned
                    sent = send_once
                    result = operation(self._client)
                except _Abandoned:
                    raise
                except Exception as exc:
                    message = describe_error(exc)
                    self._last_error = f"{op_name}: {message}"
                    if not is_link_error(exc):
                        # The connection is still fine; report the error unchanged.
                        if not sent or not is_snap7_error(exc):
                            # Not sent, or failed while being built locally.
                            raise OperationFailedError(f"{op_name} failed: {message}") from exc
                        if single_pdu and is_refusal(message):
                            raise OperationFailedError(f"{op_name} failed: {message}", outcome="rejected") from exc
                        self._last_error = f"{op_name}: {message}; may have been applied"
                        raise WriteOutcomeUnknownError(
                            f"{op_name} failed after the request was sent ({message}). It may have been "
                            "applied, possibly in part; check before retrying. It was not resent."
                        ) from exc
                    self._reset_locked()
                    if sent:
                        self._last_error = f"{op_name}: no reply ({message}); may have been applied"
                        raise WriteOutcomeUnknownError(
                            f"{op_name}: no reply from the PLC ({message}). The request was sent once and "
                            "may have been applied; check before retrying. It was not resent."
                        ) from exc
                    if attempt > self.config.max_retries:
                        error_cls = S7ConnectionError if isinstance(exc, S7ConnectionError) else OperationFailedError
                        raise error_cls(f"{op_name} failed after {attempt} attempt(s): {message}") from exc
                    delay = self.config.retry_backoff_base * (2 ** (attempt - 1))
                    logger.warning("%s: %s; reconnecting in %.2fs", op_name, message, delay)
                    time.sleep(delay)
                    continue
                self._last_error = None
                duration = (time.perf_counter() - start) * 1000.0
                return result, {"attempts": attempt, "duration_ms": round(duration, 3)}
