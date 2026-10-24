"""FastMCP server wiring for the S7comm implementation."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.stdio import stdio_server
from mcp.types import ContentBlock
from pydantic import ValidationError

from .config import package_version
from .domain.model import WRITE_TOOLS
from .s7_client import S7Client
from .tools import TagMap, ToolConfig, ToolResources, fail, register_tools

logger = logging.getLogger(__name__)

#: Errors from writing an answer to a client that has disconnected.
_UNDELIVERABLE = (anyio.ClosedResourceError, anyio.BrokenResourceError, BrokenPipeError, ConnectionResetError)


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

    #: Called when stdin reaches EOF (the MCP client is gone).
    on_client_gone: Callable[[], None] | None = None
    #: True once stdin has reached EOF.
    client_gone: bool = False

    async def run_stdio_async(self) -> None:
        """Like FastMCP's, but notices the client disconnecting.

        On stdin EOF the low-level server stops reading yet lets in-flight tool
        calls finish. on_client_gone runs first, before the SDK even sees the
        end of input, so a write still waiting to connect, retry or get the lock
        is cancelled instead of being sent to a PLC nobody is watching.
        """
        self.client_gone = False
        try:
            async with stdio_server() as (read_stream, write_stream):
                relay_send, relay_receive = anyio.create_memory_object_stream[Any](0)
                async with anyio.create_task_group() as tg:
                    tg.start_soon(self._relay_until_eof, read_stream, relay_send)
                    await self._mcp_server.run(
                        relay_receive, write_stream, self._mcp_server.create_initialization_options()
                    )
                    tg.cancel_scope.cancel()
        except BaseExceptionGroup as group:
            # Answers to calls that were still running cannot be delivered once
            # the client is gone; that is a normal end, not a crash.
            _, rest = group.split(_UNDELIVERABLE)
            if not self.client_gone or rest is not None:
                raise
            logger.info("MCP client disconnected; dropped the answers of calls still in flight")

    async def _relay_until_eof(self, source: Any, sink: Any) -> None:
        try:
            async with source:
                async for message in source:
                    await sink.send(message)
        finally:
            self.client_gone = True
            if self.on_client_gone is not None:
                self.on_client_gone()  # cancel pending PLC requests first ...
            await sink.aclose()  # ... then let the SDK see the end of input

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Sequence[ContentBlock] | dict[str, Any]:
        tool = self._tool_manager.get_tool(name)
        if tool is not None:
            try:
                tool.fn_metadata.arg_model.model_validate(tool.fn_metadata.pre_parse_json(arguments))
            except ValidationError as exc:
                meta = {"tool": name}
                if name in WRITE_TOOLS:
                    meta.update(outcome="not_sent", request_sent=False)
                return tool.fn_metadata.convert_result(fail(_format_validation_error(exc), meta))
        # Any failure from a handler, even ValidationError, may follow dispatch.
        return await super().call_tool(name, arguments)


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
        self._mcp.on_client_gone = self.client.begin_shutdown
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
