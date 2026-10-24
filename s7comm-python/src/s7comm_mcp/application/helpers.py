from __future__ import annotations

from typing import Any

from s7comm_mcp.domain.errors import OperationMeta, WritePreparationError
from s7comm_mcp.domain.model import (
    TYPE_SIZES,
    Address,
    PreparedWrite,
    check_string_header,
    decode_s7_string,
    decode_scalar,
)
from s7comm_mcp.domain.ports import S7ClientPort


async def read_value(client: S7ClientPort, addr: Address) -> tuple[dict[str, Any], OperationMeta]:
    dtype = addr.data_type
    if dtype is None:
        payload, meta = await client.read_area(addr.area, addr.offset, addr.size or 1, addr.db_number)
        return ({"raw_bytes_hex": payload.hex(), "byte_count": len(payload)}, meta)
    if dtype == "STRING":
        max_len = addr.string_length
        if max_len is None:
            header, _ = await client.read_area(addr.area, addr.offset, 2, addr.db_number)
            max_len = header[0]
            if max_len == 0:
                raise ValueError("No S7 STRING at this address: its max-length byte is 0")
        payload, meta = await client.read_area(addr.area, addr.offset, 2 + max_len, addr.db_number)
        text, cur_len = decode_s7_string(payload, max_len)
        data = {"value": text, "data_type": dtype, "length": cur_len, "max_length": max_len}
        return ({**data, "raw_bytes_hex": payload.hex()}, meta)
    size = TYPE_SIZES[dtype]
    payload, meta = await client.read_area(addr.area, addr.offset, size, addr.db_number)
    if len(payload) != size:
        raise ValueError(f"PLC returned {len(payload)} bytes for a {dtype}, expected {size}")
    data = {"value": decode_scalar(payload, dtype, addr.bit), "data_type": dtype, "raw_bytes_hex": payload.hex()}
    if dtype == "BOOL":
        data["bit"] = addr.bit or 0
    return (data, meta)


async def precheck_write(client: S7ClientPort, prepared: PreparedWrite) -> None:
    """Read the target of a prepared write from the PLC without writing anything.

    Confirms that the whole range exists and is readable and, for STRING, that
    the header matches string_length. Used by write_multiple_vars so that a
    batch is refused as a whole before its first write.
    """
    addr = prepared.address
    if prepared.kind == "string":
        size = 2 + (addr.string_length or 0)
    else:
        size = 1 if prepared.kind == "bit" else len(prepared.payload)
    payload, _ = await client.read_area(addr.area, addr.offset, size, addr.db_number)
    if len(payload) != size:
        raise ValueError(f"PLC returned {len(payload)} bytes during write precheck, expected {size}")
    if prepared.kind == "string":
        check_string_header(payload[:2], addr.string_length or 0)


async def execute_write(client: S7ClientPort, prepared: PreparedWrite) -> tuple[dict[str, Any], OperationMeta]:
    addr = prepared.address
    if prepared.kind == "bit":
        meta = await client.write_bit(addr.area, addr.offset, addr.bit or 0, prepared.flag, addr.db_number)
        return ({**addr.describe(), "value": prepared.flag, "written_bits": 1}, meta)
    if prepared.kind == "string":
        declared = addr.string_length or 0
        try:
            header, _ = await client.read_area(addr.area, addr.offset, 2, addr.db_number)
            check_string_header(header, declared)
            payload = bytes([declared, len(prepared.payload)]) + prepared.payload
        except Exception as exc:
            raise WritePreparationError(str(exc)) from exc
        meta = await client.write_area(addr.area, addr.offset, payload, addr.db_number)
        data = {**addr.describe(), "value": prepared.payload.decode("latin-1"), "max_length": declared}
        return ({**data, "length": len(prepared.payload), "written_bytes": len(payload)}, meta)
    meta = await client.write_area(addr.area, addr.offset, prepared.payload, addr.db_number)
    return ({**addr.describe(), "written_bytes": len(prepared.payload)}, meta)
