from __future__ import annotations

import logging
import math
import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from s7comm_mcp.domain.errors import MAX_BYTE_OFFSET, S7ClientError, describe_error

logger = logging.getLogger(__name__)
Envelope = dict[str, Any]
MAX_IO_BYTES = 65535
MAX_BATCH = 100
STRING_MAX_CHARS = 254
FLOAT32_MAX = 3.4028234663852886e38
TYPE_SIZES: dict[str, int] = {
    "BOOL": 1,
    "BYTE": 1,
    "WORD": 2,
    "DWORD": 4,
    "INT": 2,
    "DINT": 4,
    "REAL": 4,
    "LINT": 8,
    "ULINT": 8,
    "LREAL": 8,
}
SUPPORTED_TYPES = (*TYPE_SIZES, "STRING")
INT_RANGES: dict[str, tuple[int, int]] = {
    "BYTE": (0, 255),
    "WORD": (0, 65535),
    "DWORD": (0, 4294967295),
    "INT": (-32768, 32767),
    "DINT": (-2147483648, 2147483647),
    "LINT": (-(2**63), 2**63 - 1),
    "ULINT": (0, 2**64 - 1),
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


def envelope(success: bool, data: Any = None, error: str | None = None, meta: dict[str, Any] | None = None) -> Envelope:
    return {"success": success, "data": data, "error": error, "meta": meta or {}}


def ok(data: Any = None, meta: dict[str, Any] | None = None) -> Envelope:
    return envelope(True, data=data, meta=meta)


def fail(message: str, meta: dict[str, Any] | None = None, data: Any = None) -> Envelope:
    return envelope(False, data=data, error=message, meta=meta)


class TagMapError(ValueError):
    """The configured tag map file is missing or invalid."""


def int_arg(value: Any, name: str, minimum: int, maximum: int) -> int:
    """Return ``value`` if it is a real integer in ``[minimum, maximum]``.

    Strict: booleans, strings and floats (even 4.0) are refused rather than
    converted, so true never becomes 1 and "12" never becomes 12.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {type(value).__name__} {value!r}")
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
    if any((value != first for _, value in present[1:])):
        listed = ", ".join((f"{name}={value!r}" for name, value in present))
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


def parse_bool(value: Any) -> bool:
    """Strict: only a JSON boolean (not 1, "true" or "false")."""
    if isinstance(value, bool):
        return value
    raise ValueError(f"BOOL value must be true or false (a JSON boolean), got {type(value).__name__} {value!r}")


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
        return bool(payload[0] & 1 << (bit or 0))
    if dtype == "REAL":
        value = struct.unpack(">f", payload[:4])[0]
        return _float32_shortest(value) if math.isfinite(value) else str(value)
    if dtype == "LREAL":
        value = struct.unpack(">d", payload[:8])[0]
        return value if math.isfinite(value) else str(value)
    width = TYPE_SIZES[dtype]
    return int.from_bytes(payload[:width], "big", signed=dtype in {"INT", "DINT", "LINT"})


def encode_scalar(value: Any, dtype: str) -> bytes:
    if dtype in {"REAL", "LREAL"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{dtype} value must be a number, got {type(value).__name__} {value!r}")
        try:
            number = float(value)
        except OverflowError:
            raise ValueError(f"{dtype} value exceeds its finite range") from None
        if not math.isfinite(number) or (dtype == "REAL" and abs(number) > FLOAT32_MAX):
            raise ValueError(f"{dtype} value must be finite and within its representable range")
        return struct.pack(">f" if dtype == "REAL" else ">d", number)
    lo, hi = INT_RANGES[dtype]
    if dtype in {"LINT", "ULINT"} and isinstance(value, str):
        # Canonical decimal only: no float conversion, exponents, whitespace or prefixes.
        if len(value) > 20 or re.fullmatch(r"(?:0|-?[1-9][0-9]*)", value) is None:
            raise ValueError(f"{dtype} value must be an integer or canonical decimal string")
        value = int(value)
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
        payload = bytes((int_arg(item, f"value[{i}]", 0, 255) for i, item in enumerate(value)))
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
    max_len, cur_len = (payload[0], payload[1])
    if max_len != declared_max:
        raise ValueError(
            "The STRING header at this address declares a max"
            " length of "
            f"{max_len}"
            ", not "
            f"{declared_max}"
            "; check the offset and string_length"
        )
    if cur_len > max_len:
        raise ValueError(
            "Invalid STRING header: current length "
            f"{cur_len}"
            " exceeds max length "
            f"{max_len}"
            ". Is there a STRING at this offset?"
        )
    if 2 + cur_len > len(payload):
        raise ValueError("STRING read returned fewer bytes than its header announces")
    return (payload[2 : 2 + cur_len].decode("latin-1"), cur_len)


def check_string_header(header: bytes, declared: int) -> None:
    """Refuse a STRING write unless the PLC already holds a STRING[declared] there.

    There is no "initialise" mode: a header of 00 00 is not a STRING[N], and
    writing one would plant a STRING header over whatever data is there.
    """
    plc_max, plc_cur = (header[0], header[1])
    if plc_max != declared:
        hint = " (no S7 STRING header at this address)" if plc_max == 0 else "; check the offset and string_length"
        raise ValueError(
            "The STRING header at this address declares a max"
            " length of "
            f"{plc_max}"
            ", not "
            f"{declared}"
            f"{hint}"
            ". Nothing was written"
        )
    if plc_cur > plc_max:
        raise ValueError(
            "No valid S7 STRING header at this address (current length exceeds max length). Nothing was written"
        )


@dataclass(slots=True)
class PreparedWrite:
    """A write whose value has already been validated and encoded."""

    address: Address
    kind: str
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


def write_outcome(exc: BaseException | None = None) -> dict[str, Any]:
    """meta.outcome / meta.request_sent for a write or CPU command.

    written: the PLC confirmed it. rejected: the CPU refused a single-PDU
    request, so nothing was applied. unknown: sent, then something else went
    wrong (lost or garbled reply, partly applied multi-PDU write, failed mode
    read-back); it may have been applied, so check before retrying. not_sent:
    stopped before the request went out (disabled, invalid, failed pre-check,
    or the PLC was unreachable).
    """
    outcome = "written" if exc is None else getattr(exc, "outcome", "unknown")
    sent = outcome != "not_sent"
    if exc is not None and not hasattr(exc, "outcome"):
        sent = None  # An unclassified exception cannot establish whether a send occurred.
    return {"outcome": outcome, "request_sent": sent}


NOT_SENT = {"outcome": "not_sent", "request_sent": False}


def _error_text(exc: BaseException) -> str:
    if not isinstance(exc, (ValueError, S7ClientError)):
        logger.exception("unexpected error in tool", exc_info=exc)
    return describe_error(exc)


WRITE_TOOLS = frozenset(
    {"write_db", "write_output", "write_marker", "write_multiple_vars", "write_tag", "set_cpu_state"}
)
