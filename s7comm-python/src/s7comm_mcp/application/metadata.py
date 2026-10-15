from __future__ import annotations

from s7comm_mcp.domain.model import Envelope, TagMapError, fail, ok


class MetadataOperations:
    async def list_tags(self) -> Envelope:
        """List the tags defined in the TAG_MAP_FILE JSON file (empty if TAG_MAP_FILE is not set)."""
        try:
            tags = self.resources.tag_map.list()
        except TagMapError as exc:
            return fail(str(exc))
        return ok(
            {"tags": tags}, {"tag_map_file": str(self.resources.tag_map.path) if self.resources.tag_map.path else None}
        )
