"""A successful read must cover the complete requested memory range."""

import asyncio
from types import SimpleNamespace

import pytest

from s7comm_mcp.application.operations import Operations


@pytest.mark.parametrize(
    "payload,kwargs",
    [
        (b"\x01", {"size": 2}),
        (b"\x01\x02\x03", {"size": 2}),
        (b"", {"data_type": "STRING"}),
        (b"\x04", {"data_type": "STRING"}),
        (b"\x04\x00\x00", {"data_type": "STRING"}),
        (b"\x04\x00", {"data_type": "STRING", "string_length": 4}),
        (b"\x04\x01A", {"data_type": "STRING", "string_length": 4}),
    ],
)
def test_short_or_overlong_read_is_not_success(payload, kwargs):
    class Client:
        async def read_area(self, *args):
            return payload, {}

    operations = Operations(SimpleNamespace(client=Client()), None)
    result = asyncio.run(operations._read({"area": "db", "db_number": 1, "offset": 0}, **kwargs))
    assert result["success"] is False
    assert result["data"] is None
    assert "expected" in result["error"]


def test_complete_string_storage_decodes_without_exposing_padding_as_text():
    class Client:
        async def read_area(self, area, offset, size, db):
            return b"\x04\x01A\x00\x00\x00"[:size], {}

    operations = Operations(SimpleNamespace(client=Client()), None)
    result = asyncio.run(operations.read_db_typed(1, 0, "STRING"))
    assert result["success"] is True
    assert result["data"]["value"] == "A"
