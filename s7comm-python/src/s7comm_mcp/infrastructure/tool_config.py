from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from s7comm_mcp.config import env_bool, env_str


@dataclass(slots=True)
class ToolConfig:
    """Safety switches and the tag map location. Read-only by default."""

    writes_enabled: bool = False
    system_cmds_enabled: bool = False
    tag_map_path: Path | None = None

    @classmethod
    def from_env(cls) -> ToolConfig:
        tag_path = env_str("TAG_MAP_FILE", "")
        return cls(
            writes_enabled=env_bool("S7_WRITES_ENABLED", False),
            system_cmds_enabled=env_bool("S7_SYSTEM_CMDS_ENABLED", False),
            tag_map_path=Path(tag_path).expanduser().resolve() if tag_path else None,
        )
