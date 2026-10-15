from __future__ import annotations

from typing import Any as Any

from mcp.server.fastmcp import FastMCP as FastMCP
from mcp.types import ToolAnnotations as ToolAnnotations

from s7comm_mcp.domain.model import logger as logger

READ_ONLY_HINTS = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
WRITE_HINTS = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)


def mcp_request_cancel_scope(server: FastMCP) -> Any | None:
    """The anyio cancel scope of the MCP request being handled, or None.

    mcp's RequestResponder.cancel() sets this scope's ``cancel_called`` the
    moment notifications/cancelled arrives. It uses private mcp<2 internals
    (Server.request_context, session._in_flight, RequestResponder._cancel_scope);
    mcp is pinned below 2, and tests/test_mcp_internals.py fails if they move.
    """
    try:
        context = server._mcp_server.request_context
    except LookupError:
        return None
    in_flight = getattr(context.session, "_in_flight", None)
    responder = in_flight.get(context.request_id) if isinstance(in_flight, dict) else None
    scope = getattr(responder, "_cancel_scope", None)
    if not hasattr(scope, "cancel_called"):
        logger.warning("Could not find the MCP request's cancel scope; is mcp newer than this server supports?")
        return None
    return scope
