from __future__ import annotations

from typing import Any

from s7comm_mcp.application.helpers import execute_write, read_value
from s7comm_mcp.domain.model import (
    MAX_BATCH,
    NOT_SENT,
    WRITES_DISABLED,
    Envelope,
    _error_text,
    fail,
    make_address,
    ok,
    prepare_write,
    write_outcome,
)


class CommonOperations:
    def __init__(self, resources, cancel_scope):
        self.resources = resources
        self.cancel_scope = cancel_scope
        self.client = self.resources.client

    def _writes_refused(self, tool: str) -> Envelope | None:
        if self.resources.config.writes_enabled:
            return None
        return fail(WRITES_DISABLED, {"tool": tool, **NOT_SENT})

    def _system_cmds_refused(self) -> Envelope | None:
        cfg = self.resources.config
        if cfg.writes_enabled and cfg.system_cmds_enabled:
            return None
        return fail(
            (
                "set_cpu_state is disabled: it needs both "
                "S7_WRITES_ENABLED=true and "
                "S7_SYSTEM_CMDS_ENABLED=true (currently "
                "S7_WRITES_ENABLED="
                f"{str(cfg.writes_enabled).lower()}"
                ", S7_SYSTEM_CMDS_ENABLED="
                f"{str(cfg.system_cmds_enabled).lower()}"
                ")"
            ),
            {"tool": "set_cpu_state", **NOT_SENT},
        )

    async def _read(self, addr_args: dict[str, Any], **kwargs: Any) -> Envelope:
        meta = {k: v for k, v in addr_args.items() if v is not None}
        try:
            addr = make_address(**addr_args, **kwargs)
            data, op_meta = await read_value(self.client, addr)
        except Exception as exc:
            return fail(_error_text(exc), meta)
        return ok(data, {**op_meta, **addr.describe()})

    async def _write(self, tool: str, value: Any, addr_args: dict[str, Any], **kwargs: Any) -> Envelope:
        refused = self._writes_refused(tool)
        if refused:
            return refused
        meta = {k: v for k, v in addr_args.items() if v is not None}
        try:
            prepared = prepare_write(make_address(**addr_args, **kwargs), value)
            data, op_meta = await execute_write(self.client, prepared)
        except Exception as exc:
            return fail(_error_text(exc), {**meta, **write_outcome(exc)})
        return ok(data, {**op_meta, **prepared.address.describe(), **write_outcome()})

    def _batch_size_error(self, variables: Any) -> str | None:
        if not isinstance(variables, list) or not variables:
            return "variables must be a non-empty list of address objects"
        if len(variables) > MAX_BATCH:
            return f"at most {MAX_BATCH} variables per call, got {len(variables)}"
        return None
