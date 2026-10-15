from __future__ import annotations

from dataclasses import dataclass

from s7comm_mcp.domain.ports import S7ClientPort
from s7comm_mcp.infrastructure.maps import TagMap
from s7comm_mcp.infrastructure.tool_config import ToolConfig


@dataclass(slots=True)
class ToolResources:
    client: S7ClientPort
    config: ToolConfig
    tag_map: TagMap
