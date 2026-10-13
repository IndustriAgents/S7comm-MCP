"""Pure encoding/decoding and address validation (no PLC needed)."""

from __future__ import annotations

import struct

import pytest

from s7comm_mcp.tools import (
    address_from_spec,
    check_string_header,
    decode_s7_string,
    decode_scalar,
    encode_scalar,
    encode_string_text,
    int_arg,
    make_address,
    normalize_dtype,
    parse_bool,
    prepare_write,
    raw_bytes,
)

# ---------------------------------------------------------------- S7 STRING


def test_string_decode_strips_the_two_byte_header_and_padding() -> None:
    # What the mock holds at DB2.DBB2: STRING[30] = 'OK', padded with spaces.
    payload = bytes([30, 2]) + b"OK" + b" " * 28
    assert decode_s7_string(payload, 30) == ("OK", 2)


def test_string_decode_rejects_a_header_that_does_not_match_string_length() -> None:
    payload = bytes([30, 2]) + b"OK" + b" " * 28
    with pytest.raises(ValueError, match="max length of 30, not 20"):
        decode_s7_string(payload, 20)


def test_string_decode_rejects_current_length_above_max() -> None:
    with pytest.raises(ValueError, match="Invalid STRING header"):
        decode_s7_string(bytes([4, 9]) + b"abcd", 4)


def test_string_encode_validates_length_and_charset() -> None:
    assert encode_string_text("HELLO", 30) == b"HELLO"
    with pytest.raises(ValueError, match="at most 4"):
        encode_string_text("HELLO", 4)
    with pytest.raises(ValueError, match="Latin-1"):
        encode_string_text("日本", 30)
    with pytest.raises(ValueError, match="must be text"):
        encode_string_text(12, 30)


@pytest.mark.parametrize(
    ("header", "declared", "message"),
    [
        (b"\x00\x00", 20, "no S7 STRING header"),  # was accepted as "uninitialised"
        (b"\x00\x00", 254, "no S7 STRING header"),
        (b"\x1e\x02", 20, "max length of 30, not 20"),
        (b"\x04\x09", 4, "current length exceeds max length"),
    ],
)
def test_string_write_header_check_refuses(header: bytes, declared: int, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        check_string_header(header, declared)


def test_string_write_header_check_accepts_a_matching_header() -> None:
    check_string_header(b"\x1e\x02", 30)
    check_string_header(b"\x1e\x00", 30)


def test_string_write_requires_string_length() -> None:
    with pytest.raises(ValueError, match="string_length"):
        prepare_write(make_address("db", 2, db_number=2, data_type="STRING"), "x")


# ---------------------------------------------------------------- scalars


@pytest.mark.parametrize(
    ("dtype", "value", "encoded"),
    [
        ("BYTE", 255, b"\xff"),
        ("WORD", 0xBEEF, b"\xbe\xef"),
        ("DWORD", 0xDEADBEEF, b"\xde\xad\xbe\xef"),
        ("INT", -2, b"\xff\xfe"),
        ("DINT", 12345678, b"\x00\xbc\x61\x4e"),
        ("REAL", 42.0, b"\x42\x28\x00\x00"),
    ],
)
def test_scalar_round_trip(dtype: str, value: float, encoded: bytes) -> None:
    assert encode_scalar(value, dtype) == encoded
    assert decode_scalar(encoded, dtype) == value


@pytest.mark.parametrize(
    ("dtype", "value"),
    [
        ("BYTE", 256),
        ("BYTE", -1),
        ("INT", 40000),
        ("WORD", 1.5),
        ("DINT", "twelve"),
        ("INT", True),
        ("REAL", float("nan")),
        ("REAL", 1e39),
        ("REAL", "abc"),
        ("REAL", None),
        ("REAL", "1.5"),
        ("REAL", True),
        ("INT", "12"),
        ("BYTE", 4.0),
        ("DINT", False),
    ],
)
def test_scalar_encode_rejects_bad_values(dtype: str, value: object) -> None:
    with pytest.raises(ValueError):
        encode_scalar(value, dtype)


@pytest.mark.parametrize("value", [True, False, "5", 5.0, [5]])
def test_integers_must_be_real_ints(value: object) -> None:
    # Strict: booleans are never integers, and strings/floats are not converted.
    with pytest.raises(ValueError, match="must be an integer"):
        int_arg(value, "offset", 0, 100)
    with pytest.raises(ValueError, match="must be an integer"):
        make_address("db", value, db_number=1)


def test_batch_address_fields_are_strict() -> None:
    with pytest.raises(ValueError, match="db_number must be an integer"):
        address_from_spec({"db_number": True, "offset": 0})
    with pytest.raises(ValueError, match="offset must be an integer"):
        address_from_spec({"db_number": 1, "offset": "4"})
    with pytest.raises(ValueError, match="bit_index must be an integer"):
        address_from_spec({"db_number": 1, "offset": 0, "bit": True, "data_type": "BOOL"})
    with pytest.raises(ValueError, match="must be an integer"):
        raw_bytes([1, True])


def test_real_decode_is_the_shortest_float32_repr() -> None:
    raw = struct.pack(">f", 1450.3)
    assert decode_scalar(raw, "REAL") == 1450.3
    assert decode_scalar(struct.pack(">f", float("inf")), "REAL") == "inf"


def test_bool_decode_uses_the_bit() -> None:
    assert decode_scalar(b"\x02", "BOOL", 1) is True
    assert decode_scalar(b"\x02", "BOOL", 0) is False


def test_parse_bool_accepts_json_booleans() -> None:
    assert parse_bool(True) is True
    assert parse_bool(False) is False


@pytest.mark.parametrize("value", ["false", "true", "ON", 1, 0, 1.0, None, [1], "maybe"])
def test_parse_bool_rejects_everything_else(value: object) -> None:
    # bool("false") is True in Python; the old code would have set the bit.
    with pytest.raises(ValueError, match="JSON boolean"):
        parse_bool(value)


def test_bool_write_requires_a_bit_index() -> None:
    with pytest.raises(ValueError, match="bit_index"):
        prepare_write(make_address("output", 0, data_type="BOOL"), True)
    prepared = prepare_write(make_address("output", 0, data_type="BOOL", bit=3), False)
    assert (prepared.kind, prepared.flag) == ("bit", False)


def test_raw_payloads() -> None:
    assert raw_bytes("0a ff 10") == b"\x0a\xff\x10"
    assert raw_bytes([1, 2, 255]) == b"\x01\x02\xff"
    with pytest.raises(ValueError):
        raw_bytes([1, 300])  # used to be silently truncated to 44
    with pytest.raises(ValueError):
        raw_bytes("zz")
    with pytest.raises(ValueError):
        raw_bytes("")
    with pytest.raises(ValueError, match="does not match"):
        prepare_write(make_address("db", 0, db_number=1, size=3), "0102")


def test_unknown_data_type() -> None:
    with pytest.raises(ValueError, match="Unsupported data_type 'INVALID_TYPE'"):
        normalize_dtype("INVALID_TYPE")


# ---------------------------------------------------------------- addresses


def test_batch_entries_accept_start_offset() -> None:
    # Bug: start_offset used to be ignored, so the entry read offset 0.
    addr = address_from_spec({"area": "db", "db_number": 1, "start_offset": 12, "data_type": "DINT"})
    assert (addr.area, addr.db_number, addr.offset) == ("db", 1, 12)


@pytest.mark.parametrize("key", ["offset", "start_offset", "byte", "start_byte"])
def test_io_entries_accept_every_offset_alias(key: str) -> None:
    addr = address_from_spec({"area": "Q", key: 3})
    assert (addr.area, addr.offset, addr.db_number) == ("output", 3, 0)


def test_entries_without_an_offset_are_rejected() -> None:
    with pytest.raises(ValueError, match="needs an offset"):
        address_from_spec({"area": "db", "db_number": 1, "data_type": "REAL"})


def test_conflicting_offsets_are_rejected() -> None:
    with pytest.raises(ValueError, match="conflicting offset"):
        address_from_spec({"area": "db", "db_number": 1, "offset": 0, "start_offset": 4})


def test_bit_zero_is_not_treated_as_missing() -> None:
    # Old code: spec.get("bit") or spec.get("bit_index") turned bit 0 into bit_index.
    addr = address_from_spec({"area": "db", "db_number": 2, "offset": 0, "bit": 0, "data_type": "BOOL"})
    assert addr.bit == 0
    with pytest.raises(ValueError, match="conflicting bit"):
        address_from_spec({"area": "db", "db_number": 2, "offset": 0, "bit": 0, "bit_index": 5})


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"area": "db", "offset": 0}, "db_number is required"),
        ({"area": "db", "offset": 0, "db_number": 0}, "db_number must be between 1"),
        ({"area": "output", "offset": 0, "db_number": 3}, "only applies"),
        ({"area": "timer", "offset": 0}, "Unsupported area"),
        ({"area": "db", "offset": -1, "db_number": 1}, "offset must be between"),
        ({"area": "db", "offset": 0, "db_number": 1, "data_type": "BOOL", "bit": 8}, "bit_index must be between"),
        (
            {"area": "db", "offset": 0, "db_number": 1, "data_type": "STRING", "string_length": 255},
            "string_length must be between",
        ),
        ({"area": "db", "offset": 0, "db_number": 1, "size": 0}, "size must be between"),
        # Parameters that do not apply to the data type are rejected, never ignored.
        ({"area": "output", "offset": 2, "bit": 3}, "bit_index only applies to data_type BOOL"),
        ({"area": "db", "offset": 0, "db_number": 1, "data_type": "BYTE", "bit": 0}, "only applies to data_type BOOL"),
        (
            {"area": "db", "offset": 0, "db_number": 1, "data_type": "REAL", "string_length": 4},
            "only applies to data_type STRING",
        ),
        ({"area": "db", "offset": 0, "db_number": 1, "string_length": 4}, "only applies to data_type STRING"),
        ({"area": "db", "offset": 0, "db_number": 1, "data_type": "STRING", "size": 4}, "use string_length"),
        ({"area": "db", "offset": 0, "db_number": 1, "data_type": "REAL", "size": 2}, "does not match data_type REAL"),
    ],
)
def test_make_address_validation(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        make_address(**kwargs)  # type: ignore[arg-type]


def test_matching_size_is_accepted_for_typed_values() -> None:
    assert make_address("db", 0, db_number=1, data_type="REAL", size=4).size == 4
    assert make_address("output", 0, data_type="BOOL", bit=1, size=1).bit == 1


def test_batch_entry_with_a_bit_but_no_bool_type_is_rejected() -> None:
    # Reviewer's case: {"area": "output", "byte": 2, "bit": 6, "value": "40"} rewrote QB2 to 0x40.
    with pytest.raises(ValueError, match="bit_index only applies to data_type BOOL"):
        address_from_spec({"area": "output", "byte": 2, "bit": 6, "value": "40"})
