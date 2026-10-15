from __future__ import annotations

from s7comm_mcp.domain.model import Envelope, _error_text, fail, ok


class DiagnosticsOperations:
    async def ping(self) -> Envelope:
        """Check that the PLC answers: connects if needed and reads the CPU state."""
        settings = {
            "writes_enabled": self.resources.config.writes_enabled,
            "system_cmds_enabled": self.resources.config.system_cmds_enabled,
            "cpu_control_enabled": self.resources.config.writes_enabled and self.resources.config.system_cmds_enabled,
            "tag_map": self.resources.tag_map.status(),
        }
        try:
            observed, meta = await self.client.read_cpu_state()
        except Exception as exc:
            return fail(_error_text(exc), {"connection": self.client.connection_status(), **settings})
        return ok(
            {
                "reachable": True,
                "cpu_state": observed["state"],
                "connection": self.client.connection_status(),
                **settings,
            },
            meta,
        )

    async def get_connection_status(self) -> Envelope:
        """Report the configured PLC address and whether a connection is currently open."""
        return ok(
            {
                **self.client.connection_status(),
                "writes_enabled": self.resources.config.writes_enabled,
                "system_cmds_enabled": self.resources.config.system_cmds_enabled,
                "tag_map": self.resources.tag_map.status(),
            }
        )
