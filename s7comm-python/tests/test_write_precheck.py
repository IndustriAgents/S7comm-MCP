"""A batch cannot write after a truncated preflight read."""

import asyncio
from types import SimpleNamespace

import pytest

from s7comm_mcp.application.operations import Operations


@pytest.mark.parametrize("kind", ["raw", "string", "bit"])
def test_truncated_precheck_refuses_entire_batch(kind):
    class Client:
        def __init__(self):
            self.writes = []

        async def read_area(self, area, offset, size, db_number):
            return bytes([4, 0]) if kind == "string" else bytes(size - 1), {}

        async def write_area(self, *args):
            self.writes.append(args)
            return {}

        async def write_bit(self, *args):
            self.writes.append(args)
            return {}

    client = Client()
    operation = Operations(SimpleNamespace(client=client, config=SimpleNamespace(writes_enabled=True)), None)
    entry = {"area": "db", "db_number": 1, "offset": 0, "value": "0102"}
    if kind == "string":
        entry.update(value="AB", data_type="STRING", string_length=4)
    elif kind == "bit":
        entry.update(value=True, data_type="BOOL", bit_index=0)
    result = asyncio.run(operation.write_multiple_vars([entry]))
    assert client.writes == []
    assert result["success"] is False
    assert result["meta"]["outcome"] == "not_sent"
    assert "expected" in result["data"]["results"][0]["error"]


@pytest.mark.parametrize("entries", [[], "wrong-type"])
def test_invalid_batch_shape_reports_not_sent(entries):
    operation = Operations(SimpleNamespace(client=None, config=SimpleNamespace(writes_enabled=True)), None)
    result = asyncio.run(operation.write_multiple_vars(entries))
    assert result["success"] is False
    assert result["meta"]["outcome"] == "not_sent"
    assert result["meta"]["request_sent"] is False
