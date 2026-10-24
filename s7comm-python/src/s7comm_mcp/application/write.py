from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import StrictInt

from s7comm_mcp.application.helpers import execute_write, precheck_write
from s7comm_mcp.domain.model import (
    NOT_SENT,
    Envelope,
    PreparedWrite,
    _error_text,
    address_from_spec,
    fail,
    ok,
    prepare_write,
    write_outcome,
)


class WriteOperations:
    async def write_db(
        self,
        db_number: StrictInt,
        start_offset: StrictInt,
        value: Any,
        data_type: str | None = None,
        size: StrictInt | None = None,
        bit_index: StrictInt | None = None,
        string_length: StrictInt | None = None,
    ) -> Envelope:
        """Write a typed value or raw bytes into a data block. Disabled unless S7_WRITES_ENABLED=true."""
        return await self._write(
            "write_db",
            value,
            {"area": "db", "db_number": db_number, "offset": start_offset},
            data_type=data_type,
            size=size,
            bit=bit_index,
            string_length=string_length,
        )

    async def write_output(
        self,
        start_byte: StrictInt,
        value: Any,
        data_type: str | None = None,
        size: StrictInt | None = None,
        bit_index: StrictInt | None = None,
        string_length: StrictInt | None = None,
    ) -> Envelope:
        """Write process-image outputs (Q area). Disabled unless S7_WRITES_ENABLED=true."""
        return await self._write(
            "write_output",
            value,
            {"area": "output", "offset": start_byte},
            data_type=data_type,
            size=size,
            bit=bit_index,
            string_length=string_length,
        )

    async def write_marker(
        self,
        start_byte: StrictInt,
        value: Any,
        data_type: str | None = None,
        size: StrictInt | None = None,
        bit_index: StrictInt | None = None,
        string_length: StrictInt | None = None,
    ) -> Envelope:
        """Write marker memory (M area). Disabled unless S7_WRITES_ENABLED=true."""
        return await self._write(
            "write_marker",
            value,
            {"area": "marker", "offset": start_byte},
            data_type=data_type,
            size=size,
            bit=bit_index,
            string_length=string_length,
        )

    async def set_cpu_state(self, state: str) -> Envelope:
        """Switch the CPU to RUN (hot start) or STOP."""
        refused = self._system_cmds_refused()
        if refused:
            return refused
        if state.strip().upper() not in {"RUN", "STOP"}:
            return fail("state must be RUN or STOP", {"requested_state": state, **NOT_SENT})
        try:
            observed, meta = await self.client.set_cpu_state(state)
        except Exception as exc:
            return fail(_error_text(exc), {"requested_state": state, **write_outcome(exc)})
        return ok({**observed, "requested_state": state.strip().upper()}, {**meta, **write_outcome()})

    async def write_multiple_vars(self, variables: list[dict[str, Any]]) -> Envelope:
        """Write several addresses in one call. Disabled unless S7_WRITES_ENABLED=true."""
        refused = self._writes_refused("write_multiple_vars")
        if refused:
            return refused
        problem = self._batch_size_error(variables)
        if problem:
            return fail(problem, {"written": 0, **NOT_SENT})
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
                results.append({"index": index, "spec": spec, "success": False, "error": _error_text(exc), **NOT_SENT})
        if not any(item["success"] is False for item in results):
            for item, job in zip(results, prepared, strict=True):
                try:
                    await precheck_write(self.client, job)
                except Exception as exc:
                    item.update(success=False, error=_error_text(exc), **NOT_SENT)
        invalid = sum(1 for item in results if item["success"] is False)
        if invalid:
            for item in results:
                if item["success"] is None:
                    item.update(success=False, skipped=True, error="not written: another entry is invalid", **NOT_SENT)
            return fail(
                f"Nothing was written: {invalid} of {len(results)} entries are invalid",
                {"count": len(results), "written": 0, **NOT_SENT},
                data={"results": results},
            )
        written = 0
        failure: str | None = None
        for index, (item, job) in enumerate(zip(results, prepared, strict=True)):
            if failure is not None:
                item.update(success=False, skipped=True, error=f"not written: entry {failure} failed", **NOT_SENT)
                continue
            try:
                entry, _ = await execute_write(self.client, job)
            except Exception as exc:
                item.update(success=False, error=_error_text(exc), **write_outcome(exc))
                failure = str(index)
                continue
            item.update(success=True, entry=entry, **write_outcome())
            written += 1
        meta = {"count": len(results), "written": written}
        if failure is not None:
            failed_item = results[int(failure)]
            what = (
                "got no reply and may have been applied; check before retrying (it was not resent)"
                if failed_item["outcome"] == "unknown"
                else "failed"
            )
            stop = {
                # Once a prior entry succeeded the batch has partially applied,
                # even if the failing entry was not sent or was rejected.
                "outcome": "unknown" if written else failed_item["outcome"],
                "request_sent": True if written > 0 else failed_item["request_sent"],
            }
            return fail(
                (
                    "Entry "
                    f"{failure}"
                    " "
                    f"{what}"
                    "; "
                    f"{written}"
                    " of "
                    f"{len(results)}"
                    " entries were written before it, the rest were "
                    "skipped"
                ),
                {**meta, **stop},
                data={"results": results},
            )
        return ok({"results": results}, {**meta, **write_outcome()})

    async def write_tag(self, name: str, value: Any) -> Envelope:
        """Write a value to a tag from the tag map. Disabled unless S7_WRITES_ENABLED=true."""
        refused = self._writes_refused("write_tag")
        if refused:
            return refused
        try:
            spec = self.resources.tag_map.get(name)
            if spec is None:
                return fail(f"Unknown tag '{name}'", {"tag": name, **NOT_SENT})
            prepared = prepare_write(address_from_spec(spec), value)
        except Exception as exc:
            return fail(_error_text(exc), {"tag": name, **NOT_SENT})
        try:
            entry, meta = await execute_write(self.client, prepared)
        except Exception as exc:
            return fail(_error_text(exc), {"tag": name, **write_outcome(exc)})
        return ok({"tag": name, **entry}, {**meta, "tag": name, **write_outcome()})
