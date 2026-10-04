"""FastMCP server wiring for the S7comm implementation."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ContentBlock
from pydantic import ValidationError

from .config import package_version
from .s7_client import S7Client
from .tools import TagMap, ToolConfig, ToolResources, fail, register_tools

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AppContext:
    client: S7Client


def _format_validation_error(exc: ValidationError) -> str:
    problems = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ())) or "arguments"
        problems.append(f"{location}: {error.get('msg', 'invalid value')}")
    return "Invalid arguments: " + "; ".join(problems)


class EnvelopeFastMCP(FastMCP):
    """FastMCP that answers argument-validation errors with the tool envelope.

    FastMCP validates arguments against the tool's schema before the tool runs
    and reports a mismatch (say, a string for ``db_number``) as a bare
    ``isError`` result. This keeps the ``{success, data, error, meta}`` contract
    for those calls too.
    """

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Sequence[ContentBlock] | dict[str, Any]:
        try:
            return await super().call_tool(name, arguments)
        except ToolError as exc:
            tool = self._tool_manager.get_tool(name)
            if tool is None:
                raise
            cause = exc.__cause__
            if isinstance(cause, ValidationError):
                message = _format_validation_error(cause)
            else:
                logger.error("tool %s raised", name, exc_info=cause or exc)
                message = f"Internal error in {name}: {cause or exc}"
            return tool.fn_metadata.convert_result(fail(message, {"tool": name}))


class S7CommMCPServer:
    """Container that wires the Snap7 client lifecycle into FastMCP.

    Configuration is read from the environment when this class is instantiated
    (the CLI loads ``.env`` first). The PLC connection is opened lazily by the
    first tool call that needs it, so the server starts without a reachable PLC.
    """

    def __init__(
        self,
        client: S7Client | None = None,
        tool_config: ToolConfig | None = None,
    ) -> None:
        self.client = client or S7Client()
        self.tool_config = tool_config or ToolConfig.from_env()
        if self.client.config.debug:
            for name in ("s7comm_mcp", "snap7"):
                logging.getLogger(name).setLevel(logging.DEBUG)
        if self.tool_config.system_cmds_enabled and not self.tool_config.writes_enabled:
            logger.warning(
                "S7_SYSTEM_CMDS_ENABLED=true has no effect while S7_WRITES_ENABLED is false: "
                "set_cpu_state needs both and stays disabled"
            )
        tag_map = TagMap(self.tool_config.tag_map_path)
        if tag_map.error:
            logger.warning("%s", tag_map.error)
        self.resources = ToolResources(
            client=self.client,
            config=self.tool_config,
            tag_map=tag_map,
        )
        self._mcp = EnvelopeFastMCP(
            name="S7comm MCP Server",
            dependencies=["python-snap7"],
            lifespan=self._lifespan,
        )
        # FastMCP has no version argument; without this, serverInfo.version
        # would be the mcp SDK's version instead of this server's.
        self._mcp._mcp_server.version = package_version()
        register_tools(self._mcp, self.resources)

    def run(self) -> None:
        """Start the MCP server on stdio."""
        cfg = self.client.config
        logger.info(
            "S7comm MCP server for %s:%s (rack %s, slot %s); writes %s, system commands %s",
            cfg.host,
            cfg.port,
            cfg.rack,
            cfg.slot,
            "ENABLED" if self.tool_config.writes_enabled else "disabled",
            "ENABLED" if self.tool_config.writes_enabled and self.tool_config.system_cmds_enabled else "disabled",
        )
        self._mcp.run()

    # -----------------------------
    # Lifespan
    # -----------------------------

    @asynccontextmanager
    async def _lifespan(self, server: FastMCP) -> AsyncIterator[AppContext]:
        try:
            yield AppContext(client=self.client)
        finally:
            await self.client.close()
