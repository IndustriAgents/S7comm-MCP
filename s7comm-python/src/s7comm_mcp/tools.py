"""MCP tool definitions for the S7comm server.

Every tool returns the IndustriConnect envelope ``{success, data, error, meta}``.
Bad arguments, PLC errors and connection problems all come back as
``success: false`` with a message; nothing a tool does raises into the MCP layer.
"""

from __future__ import annotations

import inspect
import json
import logging
import math
import struct
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import env_bool, env_str
from .s7_client import MAX_BYTE_OFFSET, OperationMeta, S7Client, S7ClientError, describe_error

logger = logging.getLogger(__name__)

Envelope = dict[str, Any]

#: Largest raw read or write a single tool call accepts.
MAX_IO_BYTES = 65535
#: Largest number of entries in one read_multiple_vars / write_multiple_vars call.
MAX_BATCH = 100
#: An S7 STRING holds at most 254 characters after its 2-byte header.
STRING_MAX_CHARS = 254
#: Largest finite IEEE 754 single-precision value.
FLOAT32_MAX = 3.4028234663852886e38

TYPE_SIZES: dict[str, int] = {
    "BOOL": 1,
    "BYTE": 1,
    "WORD": 2,
    "DWORD": 4,
    "INT": 2,
    "DINT": 4,
    "REAL": 4,
}
SUPPORTED_TYPES = (*TYPE_SIZES, "STRING")
INT_RANGES: dict[str, tuple[int, int]] = {
    "BYTE": (0, 0xFF),
    "WORD": (0, 0xFFFF),
    "DWORD": (0, 0xFFFFFFFF),
    "INT": (-0x8000, 0x7FFF),
    "DINT": (-0x80000000, 0x7FFFFFFF),
}
AREA_ALIASES: dict[str, str] = {
    "db": "db",
    "input": "input",
    "inputs": "input",
    "i": "input",
    "pe": "input",
    "output": "output",
    "outputs": "output",
    "q": "output",
    "pa": "output",
    "marker": "marker",
    "markers": "marker",
    "m": "marker",
    "mk": "marker",
}
WRITES_DISABLED = "Write operations are disabled. Set S7_WRITES_ENABLED=true in the server's environment to allow them."


# ------------------------------------------------------------------------------
# Envelope
# ------------------------------------------------------------------------------


def envelope(success: bool, data: Any = None, error: str | None = None, meta: dict[str, Any] | None = None) -> Envelope:
    return {"success": success, "data": data, "error": error, "meta": meta or {}}


def ok(data: Any = None, meta: dict[str, Any] | None = None) -> Envelope:
    return envelope(True, data=data, meta=meta)


def fail(message: str, meta: dict[str, Any] | None = None, data: Any = None) -> Envelope:
    return envelope(False, data=data, error=message, meta=meta)


# ------------------------------------------------------------------------------
# Configuration and tag map
# ------------------------------------------------------------------------------


@dataclass(slots=True)
class ToolConfig:
    """Safety switches and the tag map location. Read-only by default."""

    writes_enabled: bool = False
    system_cmds_enabled: bool = False
    tag_map_path: Path | None = None

    @classmethod
    def from_env(cls) -> ToolConfig:
        tag_path = env_str("TAG_MAP_FILE", "")
        return cls(
            writes_enabled=env_bool("S7_WRITES_ENABLED", False),
            system_cmds_enabled=env_bool("S7_SYSTEM_CMDS_ENABLED", False),
            # Relative paths resolve against the server's working directory.
            tag_map_path=Path(tag_path).expanduser().resolve() if tag_path else None,
        )


class TagMapError(ValueError):
    """The configured tag map file is missing or invalid."""


class TagMap:
    """JSON-backed tag registry, re-read whenever the file changes.

    A configured file that is missing or unparsable is reported as an error by
    the tag tools instead of looking like an empty tag map.
    """

    def __init__(self, path: Path | str | None) -> None:
        self.path = Path(path).expanduser() if path else None
        self._tags: dict[str, dict[str, Any]] = {}
        self._mtime: int | None = None
        self.error: str | None = None
        self.refresh()

    def refresh(self) -> None:
        if self.path is None:
            return
        try:
            mtime = self.path.stat().st_mtime_ns
        except OSError as exc:
            self._tags, self._mtime = {}, None
            self.error = f"TAG_MAP_FILE {self.path} cannot be read: {exc.strerror or exc}"
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._tags, self.error = {}, f"TAG_MAP_FILE {self.path} is not valid JSON: {exc}"
            return
        if not isinstance(data, dict):
            self._tags, self.error = {}, f"TAG_MAP_FILE {self.path} must hold a JSON object of tag name -> address"
            return
        bad = [str(name) for name, spec in data.items() if not isinstance(spec, dict)]
        if bad:
            self._tags, self.error = {}, f"TAG_MAP_FILE {self.path}: entries must be objects ({', '.join(bad)})"
            return
        self._tags = {str(name): dict(spec) for name, spec in data.items()}
        self.error = None

    def _checked(self) -> dict[str, dict[str, Any]]:
        self.refresh()
        if self.error:
            raise TagMapError(self.error)
        return self._tags

    def get(self, name: str) -> dict[str, Any] | None:
        return self._checked().get(name)

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "area": spec.get("area", "db"),
                "description": spec.get("description"),
                "meta": {k: v for k, v in spec.items() if k not in {"area", "description"}},
            }
            for name, spec in self._checked().items()
        ]

    def status(self) -> dict[str, Any]:
        self.refresh()
        return {"path": str(self.path) if self.path else None, "count": len(self._tags), "error": self.error}

    def __len__(self) -> int:
        return len(self._tags)


@dataclass(slots=True)
class ToolResources:
    client: S7Client
    config: ToolConfig
    tag_map: TagMap


# ------------------------------------------------------------------------------
# Argument validation
# ------------------------------------------------------------------------------


def int_arg(value: Any, name: str, minimum: int, maximum: int) -> int:
    """Strictly convert ``value`` to an int in ``[minimum, maximum]``."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{name} must be a whole number, got {value}")
        value = int(value)
    elif isinstance(value, str):
        text = value.strip()
        try:
            value = int(text, 10)
        except ValueError:
            try:
                value = int(text, 0)  # 0x.. / 0b.. literals
            except ValueError:
                raise ValueError(f"{name} must be an integer, got {text!r}") from None
    elif not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {type(value).__name__}")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def normalize_dtype(data_type: Any) -> str:
    if not isinstance(data_type, str) or not data_type.strip():
        raise ValueError(f"data_type is required (one of {', '.join(SUPPORTED_TYPES)})")
    dtype = data_type.strip().upper()
    if dtype not in SUPPORTED_TYPES:
        raise ValueError(f"Unsupported data_type '{data_type}'. Supported: {', '.join(SUPPORTED_TYPES)}")
    return dtype


def normalize_area(area: Any) -> str:
    key = str(area).strip().lower() if area is not None else ""
    try:
        return AREA_ALIASES[key]
    except KeyError:
        raise ValueError(f"Unsupported area '{area}'. Valid areas: db, input, output, marker") from None


@dataclass(slots=True)
class Address:
    """A validated location in PLC memory, plus how to interpret it."""

    area: str
    offset: int
    db_number: int = 0
    data_type: str | None = None
    bit: int | None = None
    string_length: int | None = None
    size: int | None = None

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"area": self.area}
        if self.area == "db":
            out["db_number"] = self.db_number
        out["offset"] = self.offset
        if self.data_type:
            out["data_type"] = self.data_type
        if self.data_type == "BOOL":
            out["bit"] = self.bit or 0
        return out


def make_address(
    area: Any,
    offset: Any,
    *,
    db_number: Any = None,
    data_type: Any = None,
    bit: Any = None,
    string_length: Any = None,
    size: Any = None,
) -> Address:
    area_name = normalize_area(area)
    if area_name == "db":
        if db_number is None:
            raise ValueError("db_number is required for area 'db'")
        db = int_arg(db_number, "db_number", 1, 65535)
    else:
        if db_number not in (None, 0):
            raise ValueError(f"db_number only applies to area 'db', not '{area_name}'")
        db = 0
    if offset is None:
        raise ValueError("an offset is required")
    dtype = normalize_dtype(data_type) if data_type is not None else None
    # A parameter that does not apply must not be silently ignored: a bit index
    # on a raw or BYTE write would otherwise rewrite the whole byte.
    if bit is not None and dtype != "BOOL":
        raise ValueError(
            "bit_index only applies to data_type BOOL"
            + ("; without data_type the value is written as whole bytes" if dtype is None else f", not {dtype}")
        )
    if string_length is not None and dtype != "STRING":
        raise ValueError("string_length only applies to data_type STRING")
    if size is not None and dtype is not None:
        if dtype == "STRING":
            raise ValueError("size does not apply to data_type STRING; use string_length")
        if int_arg(size, "size", 1, MAX_IO_BYTES) != TYPE_SIZES[dtype]:
            raise ValueError(f"size={size} does not match data_type {dtype} ({TYPE_SIZES[dtype]} bytes)")
    return Address(
        area=area_name,
        offset=int_arg(offset, "offset", 0, MAX_BYTE_OFFSET),
        db_number=db,
        data_type=dtype,
        bit=int_arg(bit, "bit_index", 0, 7) if bit is not None else None,
        string_length=int_arg(string_length, "string_length", 1, STRING_MAX_CHARS)
        if string_length is not None
        else None,
        size=int_arg(size, "size", 1, MAX_IO_BYTES) if size is not None else None,
    )


def _pick(spec: Mapping[str, Any], names: tuple[str, ...], label: str) -> Any:
    present = [(name, spec[name]) for name in names if spec.get(name) is not None]
    if not present:
        return None
    first = present[0][1]
    if any(value != first for _, value in present[1:]):
        listed = ", ".join(f"{name}={value!r}" for name, value in present)
        raise ValueError(f"conflicting {label} keys: {listed}")
    return first


def address_from_spec(spec: Any) -> Address:
    """Parse a batch entry or tag-map entry.

    ``area`` defaults to ``db``. The byte offset may be given as ``offset``,
    ``start_offset``, ``byte`` or ``start_byte``, and the bit as ``bit`` or
    ``bit_index``. There is no default offset: an entry without one is rejected.
    """
    if not isinstance(spec, Mapping):
        raise ValueError("each entry must be an object")
    offset = _pick(spec, ("offset", "start_offset", "byte", "start_byte"), "offset")
    if offset is None:
        raise ValueError("entry needs an offset ('offset', 'start_offset', 'byte' or 'start_byte')")
    return make_address(
        spec.get("area", spec.get("table", "db")),
        offset,
        db_number=spec.get("db_number"),
        data_type=spec.get("data_type"),
        bit=_pick(spec, ("bit", "bit_index"), "bit"),
        string_length=spec.get("string_length"),
        size=spec.get("size"),
    )


# ------------------------------------------------------------------------------
# Encoding helpers
# ------------------------------------------------------------------------------


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "on", "yes"}:
            return True
        if text in {"false", "0", "off", "no"}:
            return False
    raise ValueError(f"BOOL value must be true or false, got {value!r}")


def _float32_shortest(value: float) -> float:
    """Shortest decimal that maps to the same float32 (1450.3, not 1450.300048828125)."""
    packed = struct.pack(">f", value)
    for digits in range(6, 10):
        candidate = float(f"{value:.{digits}g}")
        if struct.pack(">f", candidate) == packed:
            return candidate
    return value


def decode_scalar(payload: bytes, dtype: str, bit: int | None = None) -> Any:
    if dtype == "BOOL":
        return bool(payload[0] & (1 << (bit or 0)))
    if dtype == "REAL":
        value = struct.unpack(">f", payload[:4])[0]
        # JSON has no NaN/Infinity; report them as text rather than as null.
        return _float32_shortest(value) if math.isfinite(value) else str(value)
    width = TYPE_SIZES[dtype]
    return int.from_bytes(payload[:width], "big", signed=dtype in {"INT", "DINT"})


def encode_scalar(value: Any, dtype: str) -> bytes:
    if dtype == "REAL":
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"REAL value must be a number, got {value!r}")
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f"REAL value must be a number, got {value!r}") from None
        if not math.isfinite(number) or abs(number) > FLOAT32_MAX:
            raise ValueError(f"REAL value must be finite and within +/-{FLOAT32_MAX:g}, got {value!r}")
        return struct.pack(">f", number)
    lo, hi = INT_RANGES[dtype]
    number = int_arg(value, f"{dtype} value", lo, hi)
    return number.to_bytes(TYPE_SIZES[dtype], "big", signed=lo < 0)


def raw_bytes(value: Any) -> bytes:
    """Payload for an untyped write: hex string, list of 0-255 ints, or bytes."""
    if isinstance(value, (bytes, bytearray)):
        payload = bytes(value)
    elif isinstance(value, str):
        try:
            payload = bytes.fromhex(value.replace("0x", "").replace(":", " "))
        except ValueError:
            raise ValueError("Without data_type, a string value must be hex bytes such as '0a ff 10'") from None
    elif isinstance(value, list):
        payload = bytes(int_arg(item, f"value[{i}]", 0, 255) for i, item in enumerate(value))
    else:
        raise ValueError("Without data_type, value must be a hex string or a list of byte values (0-255)")
    if not payload:
        raise ValueError("value must contain at least one byte")
    if len(payload) > MAX_IO_BYTES:
        raise ValueError(f"value is {len(payload)} bytes; at most {MAX_IO_BYTES} can be written at once")
    return payload


def encode_string_text(value: Any, max_chars: int) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"STRING value must be text, got {type(value).__name__}")
    try:
        data = value.encode("latin-1")
    except UnicodeEncodeError:
        raise ValueError("STRING values must be single-byte (Latin-1) text") from None
    if len(data) > max_chars:
        raise ValueError(f"STRING value is {len(data)} characters but the STRING holds at most {max_chars}")
    return data


def decode_s7_string(payload: bytes, declared_max: int) -> tuple[str, int]:
    """Decode an S7 STRING: max-length byte, current-length byte, then the characters."""
    if len(payload) < 2:
        raise ValueError("STRING read returned fewer than the 2 header bytes")
    max_len, cur_len = payload[0], payload[1]
    if max_len != declared_max:
        raise ValueError(
            f"The STRING header at this address declares a max length of {max_len}, not {declared_max}; "
            "check the offset and string_length"
        )
    if cur_len > max_len:
        raise ValueError(
            f"Invalid STRING header: current length {cur_len} exceeds max length {max_len}. "
            "Is there a STRING at this offset?"
        )
    if 2 + cur_len > len(payload):
        raise ValueError("STRING read returned fewer bytes than its header announces")
    return payload[2 : 2 + cur_len].decode("latin-1"), cur_len


# ------------------------------------------------------------------------------
# Typed reads and writes (shared by the DB, I/O, batch and tag tools)
# ------------------------------------------------------------------------------


async def read_value(client: S7Client, addr: Address) -> tuple[dict[str, Any], OperationMeta]:
    dtype = addr.data_type
    if dtype is None:
        payload, meta = await client.read_area(addr.area, addr.offset, addr.size or 1, addr.db_number)
        return {"raw_bytes_hex": payload.hex(), "byte_count": len(payload)}, meta
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
        return {**data, "raw_bytes_hex": payload.hex()}, meta
    size = TYPE_SIZES[dtype]
    payload, meta = await client.read_area(addr.area, addr.offset, size, addr.db_number)
    if len(payload) != size:
        raise ValueError(f"PLC returned {len(payload)} bytes for a {dtype}, expected {size}")
    data = {"value": decode_scalar(payload, dtype, addr.bit), "data_type": dtype, "raw_bytes_hex": payload.hex()}
    if dtype == "BOOL":
        data["bit"] = addr.bit or 0
    return data, meta


def check_string_header(header: bytes, declared: int) -> None:
    """Refuse a STRING write unless the PLC already holds a STRING[declared] there.

    There is no "initialise" mode: a header of 00 00 is not a STRING[N], and
    writing one would plant a STRING header over whatever data is there.
    """
    plc_max, plc_cur = header[0], header[1]
    if plc_max != declared:
        hint = " (no S7 STRING header at this address)" if plc_max == 0 else "; check the offset and string_length"
        raise ValueError(
            f"The STRING header at this address declares a max length of {plc_max}, not {declared}{hint}. "
            "Nothing was written"
        )
    if plc_cur > plc_max:
        raise ValueError(
            "No valid S7 STRING header at this address (current length exceeds max length). Nothing was written"
        )


@dataclass(slots=True)
class PreparedWrite:
    """A write whose value has already been validated and encoded."""

    address: Address
    kind: str  # "bit", "bytes" or "string"
    payload: bytes = b""
    flag: bool = False


INPUTS_READ_ONLY = "Inputs (I / PE area) are read-only: the process image is written by the input modules"


def prepare_write(addr: Address, value: Any) -> PreparedWrite:
    if addr.area == "input":
        raise ValueError(INPUTS_READ_ONLY)
    dtype = addr.data_type
    if dtype == "BOOL":
        if addr.bit is None:
            raise ValueError("BOOL writes need bit_index (0-7)")
        return PreparedWrite(addr, "bit", flag=parse_bool(value))
    if dtype == "STRING":
        if addr.string_length is None:
            raise ValueError(
                "STRING writes need string_length (the N of STRING[N]); it is checked against the header in the PLC"
            )
        return PreparedWrite(addr, "string", payload=encode_string_text(value, addr.string_length))
    if dtype is None:
        payload = raw_bytes(value)
        if addr.size is not None and addr.size != len(payload):
            raise ValueError(f"size={addr.size} does not match the {len(payload)} bytes in value")
        return PreparedWrite(addr, "bytes", payload=payload)
    return PreparedWrite(addr, "bytes", payload=encode_scalar(value, dtype))


async def execute_write(client: S7Client, prepared: PreparedWrite) -> tuple[dict[str, Any], OperationMeta]:
    addr = prepared.address
    if prepared.kind == "bit":
        meta = await client.write_bit(addr.area, addr.offset, addr.bit or 0, prepared.flag, addr.db_number)
        return {**addr.describe(), "value": prepared.flag, "written_bits": 1}, meta
    if prepared.kind == "string":
        declared = addr.string_length or 0
        header, _ = await client.read_area(addr.area, addr.offset, 2, addr.db_number)
        check_string_header(header, declared)
        payload = bytes([declared, len(prepared.payload)]) + prepared.payload
        meta = await client.write_area(addr.area, addr.offset, payload, addr.db_number)
        data = {**addr.describe(), "value": prepared.payload.decode("latin-1"), "max_length": declared}
        return {**data, "length": len(prepared.payload), "written_bytes": len(payload)}, meta
    meta = await client.write_area(addr.area, addr.offset, prepared.payload, addr.db_number)
    return {**addr.describe(), "written_bytes": len(prepared.payload)}, meta


def _error_text(exc: BaseException) -> str:
    if not isinstance(exc, (ValueError, S7ClientError)):
        logger.exception("unexpected error in tool", exc_info=exc)
    return describe_error(exc)


# ------------------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------------------


def register_tools(server: FastMCP, resources: ToolResources) -> None:
    """Register all MCP tools on the provided server."""

    client = resources.client

    def tool(fn: Callable[..., Awaitable[Envelope]]) -> Callable[..., Awaitable[Envelope]]:
        # The docstring is the description clients show in tools/list.
        if not (fn.__doc__ or "").strip():
            raise ValueError(f"tool {fn.__name__} needs a docstring")
        return server.tool(description=inspect.cleandoc(fn.__doc__ or ""))(fn)

    def _writes_refused(tool: str) -> Envelope | None:
        if resources.config.writes_enabled:
            return None
        return fail(WRITES_DISABLED, {"tool": tool})

    def _system_cmds_refused() -> Envelope | None:
        # A CPU command changes the plant at least as much as a write, so it
        # needs the write switch as well as its own.
        cfg = resources.config
        if cfg.writes_enabled and cfg.system_cmds_enabled:
            return None
        return fail(
            "set_cpu_state is disabled: it needs both S7_WRITES_ENABLED=true and S7_SYSTEM_CMDS_ENABLED=true "
            f"(currently S7_WRITES_ENABLED={str(cfg.writes_enabled).lower()}, "
            f"S7_SYSTEM_CMDS_ENABLED={str(cfg.system_cmds_enabled).lower()})",
            {"tool": "set_cpu_state"},
        )

    async def _read(addr_args: dict[str, Any], **kwargs: Any) -> Envelope:
        meta = {k: v for k, v in addr_args.items() if v is not None}
        try:
            addr = make_address(**addr_args, **kwargs)
            data, op_meta = await read_value(client, addr)
        except Exception as exc:
            return fail(_error_text(exc), meta)
        return ok(data, {**op_meta, **addr.describe()})

    async def _write(tool: str, value: Any, addr_args: dict[str, Any], **kwargs: Any) -> Envelope:
        refused = _writes_refused(tool)
        if refused:
            return refused
        meta = {k: v for k, v in addr_args.items() if v is not None}
        try:
            prepared = prepare_write(make_address(**addr_args, **kwargs), value)
            data, op_meta = await execute_write(client, prepared)
        except Exception as exc:
            return fail(_error_text(exc), meta)
        return ok(data, {**op_meta, **prepared.address.describe()})

    # -----------------------------
    # Data blocks
    # -----------------------------

    @tool
    async def read_db(db_number: int, start_offset: int, size: int) -> Envelope:
        """Read raw bytes from a Siemens data block (DB).

        Returns the bytes as hex in data.raw_bytes_hex. Absolute offsets need a DB
        with standard (non-optimized) block access on S7-1200/1500.

        Args:
            db_number: Data block number, 1-65535.
            start_offset: First byte to read.
            size: Number of bytes to read, 1-65535.
        """
        return await _read({"area": "db", "db_number": db_number, "offset": start_offset}, size=size)

    @tool
    async def read_db_typed(
        db_number: int,
        start_offset: int,
        data_type: str,
        size: int | None = None,
        bit_index: int | None = None,
        string_length: int | None = None,
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
        return await _read(
            {"area": "db", "db_number": db_number, "offset": start_offset},
            data_type=data_type,
            size=size,
            bit=bit_index,
            string_length=string_length,
        )

    @tool
    async def write_db(
        db_number: int,
        start_offset: int,
        value: Any,
        data_type: str | None = None,
        size: int | None = None,
        bit_index: int | None = None,
        string_length: int | None = None,
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
        return await _write(
            "write_db",
            value,
            {"area": "db", "db_number": db_number, "offset": start_offset},
            data_type=data_type,
            size=size,
            bit=bit_index,
            string_length=string_length,
        )

    # -----------------------------
    # I/O areas
    # -----------------------------

    @tool
    async def read_input(start_byte: int, size: int) -> Envelope:
        """Read process-image inputs (I / PE area) as raw bytes.

        Args:
            start_byte: First input byte (IB) to read.
            size: Number of bytes, 1-65535.
        """
        return await _read({"area": "input", "offset": start_byte}, size=size)

    @tool
    async def read_output(start_byte: int, size: int) -> Envelope:
        """Read process-image outputs (Q / PA area) as raw bytes.

        Args:
            start_byte: First output byte (QB) to read.
            size: Number of bytes, 1-65535.
        """
        return await _read({"area": "output", "offset": start_byte}, size=size)

    @tool
    async def write_output(
        start_byte: int,
        value: Any,
        data_type: str | None = None,
        size: int | None = None,
        bit_index: int | None = None,
        string_length: int | None = None,
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
        return await _write(
            "write_output",
            value,
            {"area": "output", "offset": start_byte},
            data_type=data_type,
            size=size,
            bit=bit_index,
            string_length=string_length,
        )

    @tool
    async def read_marker(start_byte: int, size: int) -> Envelope:
        """Read marker (bit memory, M / MK area) bytes.

        Args:
            start_byte: First marker byte (MB) to read.
            size: Number of bytes, 1-65535.
        """
        return await _read({"area": "marker", "offset": start_byte}, size=size)

    @tool
    async def write_marker(
        start_byte: int,
        value: Any,
        data_type: str | None = None,
        size: int | None = None,
        bit_index: int | None = None,
        string_length: int | None = None,
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
        return await _write(
            "write_marker",
            value,
            {"area": "marker", "offset": start_byte},
            data_type=data_type,
            size=size,
            bit=bit_index,
            string_length=string_length,
        )

    # -----------------------------
    # PLC information and control
    # -----------------------------

    @tool
    async def read_plc_info() -> Envelope:
        """Read the CPU's identity: module type and name, serial number, order code and firmware version."""
        try:
            data, meta = await client.read_plc_info()
        except Exception as exc:
            return fail(_error_text(exc))
        return ok(data, meta)

    @tool
    async def read_cpu_state() -> Envelope:
        """Read the CPU operating mode.

        data.state is the Snap7 status (S7CpuStatusRun, S7CpuStatusStop or
        S7CpuStatusUnknown) and data.mode the short form (RUN, STOP or UNKNOWN).
        """
        try:
            data, meta = await client.read_cpu_state()
        except Exception as exc:
            return fail(_error_text(exc))
        return ok(data, meta)

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
        refused = _system_cmds_refused()
        if refused:
            return refused
        try:
            observed, meta = await client.set_cpu_state(state)
        except Exception as exc:
            return fail(_error_text(exc), {"requested_state": state})
        return ok({**observed, "requested_state": state.strip().upper()}, meta)

    @tool
    async def read_system_time() -> Envelope:
        """Read the PLC's real-time clock as an ISO 8601 timestamp (PLC local time, no time zone)."""
        try:
            timestamp, meta = await client.read_system_time()
        except Exception as exc:
            return fail(_error_text(exc))
        return ok({"system_time": timestamp}, meta)

    @tool
    async def read_szl(szl_id: int, szl_index: int = 0) -> Envelope:
        """Read a system status list (SZL/SSL) partial list.

        Returns the record length, record count and each record as hex. Common
        IDs: 0x0011 module identification, 0x001C component identification,
        0x0424 operating mode, 0x0000 list of available SZL IDs.

        Args:
            szl_id: SZL ID, 0-65535 (e.g. 17 for 0x0011).
            szl_index: SZL index, 0-65535 (default 0).
        """
        meta = {"szl_id": szl_id, "szl_index": szl_index}
        try:
            int_arg(szl_id, "szl_id", 0, 0xFFFF)
            int_arg(szl_index, "szl_index", 0, 0xFFFF)
            data, op_meta = await client.read_szl(szl_id, szl_index)
        except Exception as exc:
            return fail(_error_text(exc), meta)
        return ok(data, {**op_meta, **meta})

    # -----------------------------
    # Batch operations
    # -----------------------------

    def _batch_size_error(variables: Any) -> str | None:
        if not isinstance(variables, list) or not variables:
            return "variables must be a non-empty list of address objects"
        if len(variables) > MAX_BATCH:
            return f"at most {MAX_BATCH} variables per call, got {len(variables)}"
        return None

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
        problem = _batch_size_error(variables)
        if problem:
            return fail(problem)
        results: list[dict[str, Any]] = []
        for index, spec in enumerate(variables):
            try:
                addr = address_from_spec(spec)
                data, _ = await read_value(client, addr)
            except Exception as exc:
                results.append({"index": index, "spec": spec, "success": False, "error": _error_text(exc)})
                continue
            results.append({"index": index, "spec": spec, "success": True, "address": addr.describe(), "data": data})
        failed = sum(1 for item in results if not item["success"])
        meta = {"count": len(results), "failed": failed}
        if failed:
            return fail(f"{failed} of {len(results)} entries could not be read", meta, data={"results": results})
        return ok({"results": results}, meta)

    @tool
    async def write_multiple_vars(variables: list[dict[str, Any]]) -> Envelope:
        """Write several addresses in one call. Disabled unless S7_WRITES_ENABLED=true.

        Entries use the read_multiple_vars address keys plus value, with the
        write_db rules (BOOL needs a bit and writes only that bit; STRING needs
        string_length). Every entry is validated first and nothing is written if
        any is invalid. Entries are then written in order; on the first PLC error
        the rest are skipped. success is true only if every entry was written.
        """
        refused = _writes_refused("write_multiple_vars")
        if refused:
            return refused
        problem = _batch_size_error(variables)
        if problem:
            return fail(problem)
        prepared: list[PreparedWrite | None] = []
        results: list[dict[str, Any]] = []
        for index, spec in enumerate(variables):
            try:
                if not isinstance(spec, Mapping) or "value" not in spec:
                    raise ValueError("entry needs a value")
                prepared.append(prepare_write(address_from_spec(spec), spec["value"]))
                results.append({"index": index, "spec": spec, "success": None})
            except Exception as exc:
                prepared.append(None)
                results.append({"index": index, "spec": spec, "success": False, "error": _error_text(exc)})
        invalid = sum(1 for item in results if item["success"] is False)
        if invalid:
            for item in results:
                if item["success"] is None:
                    item.update(success=False, skipped=True, error="not written: another entry is invalid")
            return fail(
                f"Nothing was written: {invalid} of {len(results)} entries are invalid",
                {"count": len(results), "written": 0},
                data={"results": results},
            )
        written = 0
        failure: str | None = None
        for index, (item, job) in enumerate(zip(results, prepared, strict=True)):
            if failure is not None:
                item.update(success=False, skipped=True, error=f"not written: entry {failure} failed")
                continue
            try:
                entry, _ = await execute_write(client, job)  # type: ignore[arg-type]
            except Exception as exc:
                item.update(success=False, error=_error_text(exc))
                failure = str(index)
                continue
            item.update(success=True, entry=entry)
            written += 1
        meta = {"count": len(results), "written": written}
        if failure is not None:
            return fail(
                f"Entry {failure} failed; {written} of {len(results)} entries were written",
                meta,
                data={"results": results},
            )
        return ok({"results": results}, meta)

    # -----------------------------
    # Tag map
    # -----------------------------

    @tool
    async def list_tags() -> Envelope:
        """List the tags defined in the TAG_MAP_FILE JSON file (empty if TAG_MAP_FILE is not set)."""
        try:
            tags = resources.tag_map.list()
        except TagMapError as exc:
            return fail(str(exc))
        return ok({"tags": tags}, {"tag_map_file": str(resources.tag_map.path) if resources.tag_map.path else None})

    @tool
    async def read_tag(name: str) -> Envelope:
        """Read a tag from the tag map by name, using its area, offset and data_type.

        Args:
            name: Tag name as listed by list_tags.
        """
        try:
            spec = resources.tag_map.get(name)
            if spec is None:
                return fail(f"Unknown tag '{name}'", {"tag": name})
            addr = address_from_spec(spec)
            data, meta = await read_value(client, addr)
        except Exception as exc:
            return fail(_error_text(exc), {"tag": name})
        return ok({"tag": name, **data}, {**meta, **addr.describe(), "tag": name})

    @tool
    async def write_tag(name: str, value: Any) -> Envelope:
        """Write a value to a tag from the tag map. Disabled unless S7_WRITES_ENABLED=true.

        Uses the tag's data_type with the write_db rules (a BOOL tag needs a bit;
        a STRING tag needs string_length).

        Args:
            name: Tag name as listed by list_tags.
            value: The value to write.
        """
        refused = _writes_refused("write_tag")
        if refused:
            return refused
        try:
            spec = resources.tag_map.get(name)
            if spec is None:
                return fail(f"Unknown tag '{name}'", {"tag": name})
            prepared = prepare_write(address_from_spec(spec), value)
            entry, meta = await execute_write(client, prepared)
        except Exception as exc:
            return fail(_error_text(exc), {"tag": name})
        return ok({"tag": name, **entry}, {**meta, "tag": name})

    # -----------------------------
    # Health
    # -----------------------------

    @tool
    async def ping() -> Envelope:
        """Check that the PLC answers: connects if needed and reads the CPU state.

        Fails if the PLC cannot be reached. Also reports the server's safety
        switches and tag map status.
        """
        settings = {
            "writes_enabled": resources.config.writes_enabled,
            "system_cmds_enabled": resources.config.system_cmds_enabled,
            # set_cpu_state needs both switches.
            "cpu_control_enabled": resources.config.writes_enabled and resources.config.system_cmds_enabled,
            "tag_map": resources.tag_map.status(),
        }
        try:
            observed, meta = await client.read_cpu_state()
        except Exception as exc:
            return fail(_error_text(exc), {"connection": client.connection_status(), **settings})
        return ok(
            {"reachable": True, "cpu_state": observed["state"], "connection": client.connection_status(), **settings},
            meta,
        )

    @tool
    async def get_connection_status() -> Envelope:
        """Report the configured PLC address and whether a connection is currently open.

        Does not contact the PLC; the connection opens on the first tool call that
        needs it (use ping to test reachability). last_error holds the most recent
        failure, if any.
        """
        return ok(client.connection_status())
