from __future__ import annotations

from typing import Any

from pydantic import StrictInt

from s7comm_mcp.application.helpers import read_value
from s7comm_mcp.domain.model import Envelope, _error_text, address_from_spec, fail, int_arg, ok


class ReadOperations:
    async def read_db(self, db_number: StrictInt, start_offset: StrictInt, size: StrictInt) -> Envelope:
        """Read raw bytes from a Siemens data block (DB)."""
        return await self._read({"area": "db", "db_number": db_number, "offset": start_offset}, size=size)

    async def read_db_typed(
        self,
        db_number: StrictInt,
        start_offset: StrictInt,
        data_type: str,
        size: StrictInt | None = None,
        bit_index: StrictInt | None = None,
        string_length: StrictInt | None = None,
    ) -> Envelope:
        """Read one typed value from a data block and decode it."""
        return await self._read(
            {"area": "db", "db_number": db_number, "offset": start_offset},
            data_type=data_type,
            size=size,
            bit=bit_index,
            string_length=string_length,
        )

    async def read_input(self, start_byte: StrictInt, size: StrictInt) -> Envelope:
        """Read process-image inputs (I / PE area) as raw bytes."""
        return await self._read({"area": "input", "offset": start_byte}, size=size)

    async def read_output(self, start_byte: StrictInt, size: StrictInt) -> Envelope:
        """Read process-image outputs (Q / PA area) as raw bytes."""
        return await self._read({"area": "output", "offset": start_byte}, size=size)

    async def read_marker(self, start_byte: StrictInt, size: StrictInt) -> Envelope:
        """Read marker (bit memory, M / MK area) bytes."""
        return await self._read({"area": "marker", "offset": start_byte}, size=size)

    async def read_plc_info(self) -> Envelope:
        """Read the CPU's identity: module type and name, serial number, order code and firmware version."""
        try:
            data, meta = await self.client.read_plc_info()
        except Exception as exc:
            return fail(_error_text(exc))
        return ok(data, meta)

    async def read_cpu_state(self) -> Envelope:
        """Read the CPU operating mode."""
        try:
            data, meta = await self.client.read_cpu_state()
        except Exception as exc:
            return fail(_error_text(exc))
        return ok(data, meta)

    async def read_system_time(self) -> Envelope:
        """Read the PLC's real-time clock as an ISO 8601 timestamp (PLC local time, no time zone)."""
        try:
            timestamp, meta = await self.client.read_system_time()
        except Exception as exc:
            return fail(_error_text(exc))
        return ok({"system_time": timestamp}, meta)

    async def read_szl(self, szl_id: StrictInt, szl_index: StrictInt = 0) -> Envelope:
        """Read a system status list (SZL/SSL) partial list."""
        meta = {"szl_id": szl_id, "szl_index": szl_index}
        try:
            int_arg(szl_id, "szl_id", 0, 65535)
            int_arg(szl_index, "szl_index", 0, 65535)
            data, op_meta = await self.client.read_szl(szl_id, szl_index)
        except Exception as exc:
            return fail(_error_text(exc), meta)
        return ok(data, {**op_meta, **meta})

    async def read_multiple_vars(self, variables: list[dict[str, Any]]) -> Envelope:
        """Read several addresses in one call."""
        problem = self._batch_size_error(variables)
        if problem:
            return fail(problem)
        results: list[dict[str, Any]] = []
        for index, spec in enumerate(variables):
            try:
                addr = address_from_spec(spec)
                data, _ = await read_value(self.client, addr)
            except Exception as exc:
                results.append({"index": index, "spec": spec, "success": False, "error": _error_text(exc)})
                continue
            results.append({"index": index, "spec": spec, "success": True, "address": addr.describe(), "data": data})
        failed = sum(1 for item in results if not item["success"])
        meta = {"count": len(results), "failed": failed}
        if failed:
            return fail(f"{failed} of {len(results)} entries could not be read", meta, data={"results": results})
        return ok({"results": results}, meta)

    async def read_tag(self, name: str) -> Envelope:
        """Read a tag from the tag map by name, using its area, offset and data_type."""
        try:
            spec = self.resources.tag_map.get(name)
            if spec is None:
                return fail(f"Unknown tag '{name}'", {"tag": name})
            addr = address_from_spec(spec)
            data, meta = await read_value(self.client, addr)
        except Exception as exc:
            return fail(_error_text(exc), {"tag": name})
        return ok({"tag": name, **data}, {**meta, **addr.describe(), "tag": name})
