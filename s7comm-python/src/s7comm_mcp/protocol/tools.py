from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import StrictInt

from s7comm_mcp.application.resources import ToolResources
from s7comm_mcp.domain.model import WRITE_TOOLS, Envelope

from ..application.operations import Operations
from .support import READ_ONLY_HINTS, WRITE_HINTS, FastMCP, mcp_request_cancel_scope


def register_tools(server: FastMCP, resources: ToolResources) -> None:
    client = resources.client
    operations = Operations(resources, lambda: mcp_request_cancel_scope(server))

    def tool(fn: Callable[..., Awaitable[Envelope]]) -> Callable[..., Awaitable[Envelope]]:
        if not (fn.__doc__ or "").strip():
            raise ValueError(f"tool {fn.__name__} needs a docstring")
        hints = WRITE_HINTS if fn.__name__ in WRITE_TOOLS else READ_ONLY_HINTS

        @functools.wraps(fn)
        async def within_one_deadline(*args: Any, **kwargs: Any) -> Envelope:
            with client.tool_call(operations.cancel_scope()):
                return await fn(*args, **kwargs)

        return server.tool(description=inspect.cleandoc(fn.__doc__ or ""), annotations=hints)(within_one_deadline)

    @tool
    async def read_db(db_number: StrictInt, start_offset: StrictInt, size: StrictInt) -> Envelope:
        """Read raw bytes from a Siemens data block (DB).

        Returns the bytes as hex in data.raw_bytes_hex. Absolute offsets need a DB
        with standard (non-optimized) block access on S7-1200/1500.

        Args:
            db_number: Data block number, 1-65535.
            start_offset: First byte to read.
            size: Number of bytes to read, 1-65535.
        """
        return await operations.read_db(db_number, start_offset, size)

    @tool
    async def read_db_typed(
        db_number: StrictInt,
        start_offset: StrictInt,
        data_type: str,
        size: StrictInt | None = None,
        bit_index: StrictInt | None = None,
        string_length: StrictInt | None = None,
    ) -> Envelope:
        """Read one typed value from a data block and decode it.

        data_type is BOOL, BYTE, WORD, DWORD, INT, DINT, REAL or STRING (S7 STRING
        with its 2-byte max/current length header; the text is returned without it).
        Values are big-endian as stored by the PLC.

        Args:
            db_number: Data block number, 1-65535.
            start_offset: Byte offset of the value.
            data_type: One of the types above.
            size: Optional; if given it must equal the type's size (not used for STRING).
            bit_index: Bit 0-7, BOOL only (default 0). Rejected for other types.
            string_length: For STRING, the declared N of STRING[N]. If omitted it is
                taken from the header in the PLC; if given it must match it.
        """
        return await operations.read_db_typed(db_number, start_offset, data_type, size, bit_index, string_length)

    @tool
    async def write_db(
        db_number: StrictInt,
        start_offset: StrictInt,
        value: Any,
        data_type: str | None = None,
        size: StrictInt | None = None,
        bit_index: StrictInt | None = None,
        string_length: StrictInt | None = None,
    ) -> Envelope:
        """Write a typed value or raw bytes into a data block. Disabled unless S7_WRITES_ENABLED=true.

        With data_type (BOOL, BYTE, WORD, DWORD, INT, DINT, REAL, STRING) the value is
        range-checked and encoded big-endian. BOOL needs bit_index and writes only
        that bit; the other 7 bits of the byte are left untouched. STRING needs
        string_length, which must match the STRING header in the PLC. Without
        data_type, value is raw bytes: a hex string ("0a ff") or a list of 0-255.
        bit_index is only accepted with BOOL and string_length only with STRING;
        to change a single bit, use data_type BOOL.

        Args:
            db_number: Data block number, 1-65535.
            start_offset: Byte offset to write at.
            value: The value to write.
            data_type: Optional type, see above.
            size: Optional expected byte count (checked against value or data_type).
            bit_index: Bit 0-7, required for BOOL and rejected otherwise.
            string_length: N of STRING[N], required for STRING and rejected otherwise.
        """
        return await operations.write_db(db_number, start_offset, value, data_type, size, bit_index, string_length)

    @tool
    async def read_input(start_byte: StrictInt, size: StrictInt) -> Envelope:
        """Read process-image inputs (I / PE area) as raw bytes.

        Args:
            start_byte: First input byte (IB) to read.
            size: Number of bytes, 1-65535.
        """
        return await operations.read_input(start_byte, size)

    @tool
    async def read_output(start_byte: StrictInt, size: StrictInt) -> Envelope:
        """Read process-image outputs (Q / PA area) as raw bytes.

        Args:
            start_byte: First output byte (QB) to read.
            size: Number of bytes, 1-65535.
        """
        return await operations.read_output(start_byte, size)

    @tool
    async def write_output(
        start_byte: StrictInt,
        value: Any,
        data_type: str | None = None,
        size: StrictInt | None = None,
        bit_index: StrictInt | None = None,
        string_length: StrictInt | None = None,
    ) -> Envelope:
        """Write process-image outputs (Q area). Disabled unless S7_WRITES_ENABLED=true.

        Outputs drive physical equipment. Takes the same value/data_type rules as
        write_db; a BOOL write (with bit_index) changes only that output bit.

        Args:
            start_byte: Output byte (QB) to write at.
            value: The value to write.
            data_type: Optional BOOL, BYTE, WORD, DWORD, INT, DINT, REAL or STRING.
            size: Optional expected byte count (checked against value or data_type).
            bit_index: Bit 0-7, required for BOOL and rejected otherwise.
            string_length: N of STRING[N], required for STRING and rejected otherwise.
        """
        return await operations.write_output(start_byte, value, data_type, size, bit_index, string_length)

    @tool
    async def read_marker(start_byte: StrictInt, size: StrictInt) -> Envelope:
        """Read marker (bit memory, M / MK area) bytes.

        Args:
            start_byte: First marker byte (MB) to read.
            size: Number of bytes, 1-65535.
        """
        return await operations.read_marker(start_byte, size)

    @tool
    async def write_marker(
        start_byte: StrictInt,
        value: Any,
        data_type: str | None = None,
        size: StrictInt | None = None,
        bit_index: StrictInt | None = None,
        string_length: StrictInt | None = None,
    ) -> Envelope:
        """Write marker memory (M area). Disabled unless S7_WRITES_ENABLED=true.

        Same value/data_type rules as write_db; a BOOL write (with bit_index)
        changes only that marker bit.

        Args:
            start_byte: Marker byte (MB) to write at.
            value: The value to write.
            data_type: Optional BOOL, BYTE, WORD, DWORD, INT, DINT, REAL or STRING.
            size: Optional expected byte count (checked against value or data_type).
            bit_index: Bit 0-7, required for BOOL and rejected otherwise.
            string_length: N of STRING[N], required for STRING and rejected otherwise.
        """
        return await operations.write_marker(start_byte, value, data_type, size, bit_index, string_length)

    @tool
    async def read_plc_info() -> Envelope:
        """Read the CPU's identity: module type and name, serial number, order code and firmware version."""
        return await operations.read_plc_info()

    @tool
    async def read_cpu_state() -> Envelope:
        """Read the CPU operating mode.

        data.state is the Snap7 status (S7CpuStatusRun, S7CpuStatusStop or
        S7CpuStatusUnknown) and data.mode the short form (RUN, STOP or UNKNOWN).
        """
        return await operations.read_cpu_state()

    @tool
    async def set_cpu_state(state: str) -> Envelope:
        """Switch the CPU to RUN (hot start) or STOP.

        Disabled unless both S7_WRITES_ENABLED=true and S7_SYSTEM_CMDS_ENABLED=true.
        Succeeds only when the PLC accepted the command and then reports the
        requested mode (polled for up to S7_TIMEOUT seconds). The command is sent
        at most once.

        Args:
            state: "RUN" or "STOP".
        """
        return await operations.set_cpu_state(state)

    @tool
    async def read_system_time() -> Envelope:
        """Read the PLC's real-time clock as an ISO 8601 timestamp (PLC local time, no time zone)."""
        return await operations.read_system_time()

    @tool
    async def read_szl(szl_id: StrictInt, szl_index: StrictInt = 0) -> Envelope:
        """Read a system status list (SZL/SSL) partial list.

        Returns the record length, record count and each record as hex. Common
        IDs: 0x0011 module identification, 0x001C component identification,
        0x0424 operating mode, 0x0000 list of available SZL IDs.

        Args:
            szl_id: SZL ID, 0-65535 (e.g. 17 for 0x0011).
            szl_index: SZL index, 0-65535 (default 0).
        """
        return await operations.read_szl(szl_id, szl_index)

    @tool
    async def read_multiple_vars(variables: list[dict[str, Any]]) -> Envelope:
        """Read several addresses in one call.

        Each entry is an object with: area (db, input, output or marker; default
        db), db_number (for db), offset (alias start_offset, byte, start_byte;
        required), data_type (optional, as for read_db_typed; raw bytes without
        it), size (raw byte count, default 1), bit / bit_index (BOOL),
        string_length (STRING). success is true only if every entry was read;
        data.results always holds one result per entry.
        """
        return await operations.read_multiple_vars(variables)

    @tool
    async def write_multiple_vars(variables: list[dict[str, Any]]) -> Envelope:
        """Write several addresses in one call. Disabled unless S7_WRITES_ENABLED=true.

        Entries use the read_multiple_vars address keys plus value, with the
        write_db rules (BOOL needs a bit and writes only that bit; STRING needs
        string_length; inputs are read-only). Every entry is checked before
        anything is written: values and addresses locally, then each target
        range is read from the PLC (STRING headers included). If any check fails,
        nothing is written. Entries are then written in order, each sent once; on
        the first PLC error or lost reply the rest are skipped. success is true
        only if every entry was written.
        """
        return await operations.write_multiple_vars(variables)

    @tool
    async def list_tags() -> Envelope:
        """List the tags defined in the TAG_MAP_FILE JSON file (empty if TAG_MAP_FILE is not set)."""
        return await operations.list_tags()

    @tool
    async def read_tag(name: str) -> Envelope:
        """Read a tag from the tag map by name, using its area, offset and data_type.

        Args:
            name: Tag name as listed by list_tags.
        """
        return await operations.read_tag(name)

    @tool
    async def write_tag(name: str, value: Any) -> Envelope:
        """Write a value to a tag from the tag map. Disabled unless S7_WRITES_ENABLED=true.

        Uses the tag's data_type with the write_db rules (a BOOL tag needs a bit;
        a STRING tag needs string_length).

        Args:
            name: Tag name as listed by list_tags.
            value: The value to write.
        """
        return await operations.write_tag(name, value)

    @tool
    async def ping() -> Envelope:
        """Check that the PLC answers: connects if needed and reads the CPU state.

        Fails if the PLC cannot be reached. Also reports the server's safety
        switches and tag map status.
        """
        return await operations.ping()

    @tool
    async def get_connection_status() -> Envelope:
        """Report the configured PLC address and whether a connection is currently open.

        Does not contact the PLC; the connection opens on the first tool call that
        needs it (use ping to test reachability). last_error holds the most recent
        failure, if any.
        """
        return await operations.get_connection_status()

    from .contract import install_contract

    install_contract(server)
