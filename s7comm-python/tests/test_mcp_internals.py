"""The private mcp<2 internals the cancel-race protection relies on.

S7comm's tools read the MCP request's cancel scope (Server.request_context,
session._in_flight[request_id]._cancel_scope) so a write is never sent once
the client has cancelled. These tests fail if an mcp release moves them.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import anyio
import pytest
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CancelledNotification, CancelledNotificationParams, ClientNotification

from s7comm_mcp.s7_client import S7Client, S7ClientConfig
from s7comm_mcp.server import S7CommMCPServer
from s7comm_mcp.tools import ToolConfig


@pytest.fixture
def recorded_scopes(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    scopes: list[Any] = []
    real_tool_call = S7Client.tool_call

    @contextmanager
    def recording(self: S7Client, cancel_scope: Any | None = None) -> Any:
        scopes.append(cancel_scope)
        with real_tool_call(self, cancel_scope):
            yield

    monkeypatch.setattr(S7Client, "tool_call", recording)
    return scopes


async def test_a_tool_call_gets_the_mcp_request_cancel_scope(recorded_scopes: list[Any]) -> None:
    server = S7CommMCPServer(client=S7Client(S7ClientConfig(port=1)), tool_config=ToolConfig())
    async with create_connected_server_and_client_session(server._mcp._mcp_server) as session:
        result = await session.call_tool("get_connection_status", {})
    assert result.structuredContent and result.structuredContent["success"] is True
    assert len(recorded_scopes) == 1
    assert isinstance(recorded_scopes[0], anyio.CancelScope), "mcp internals moved: no request cancel scope"


async def test_notifications_cancelled_sets_cancel_called_on_that_scope(
    recorded_scopes: list[Any], silent_tcp_server: int
) -> None:
    # ping against a PLC that never answers keeps the tool call busy.
    client = S7Client(S7ClientConfig(port=silent_tcp_server, timeout=3.0, max_retries=0, request_deadline=10))
    server = S7CommMCPServer(client=client, tool_config=ToolConfig())
    answers: list[str] = []

    async def call_ping(session: Any) -> None:
        try:
            await session.call_tool("ping", {})
        except McpError as exc:  # the server answers the cancel with "Request cancelled"
            answers.append(str(exc))

    async with create_connected_server_and_client_session(server._mcp._mcp_server) as session:
        request_id = session._request_id  # the id the next request will get
        async with anyio.create_task_group() as tg:
            tg.start_soon(call_ping, session)
            with anyio.fail_after(5):
                while not recorded_scopes:
                    await anyio.sleep(0.01)
            scope = recorded_scopes[0]
            assert isinstance(scope, anyio.CancelScope) and scope.cancel_called is False
            await session.send_notification(
                ClientNotification(
                    CancelledNotification(
                        method="notifications/cancelled", params=CancelledNotificationParams(requestId=request_id)
                    )
                )
            )
            with anyio.fail_after(5):
                while not scope.cancel_called:
                    await anyio.sleep(0.01)
    await client.close()
    assert answers == ["Request cancelled"]
