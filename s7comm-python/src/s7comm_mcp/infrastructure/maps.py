from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from s7comm_mcp.domain.model import TagMapError


class TagMap:
    """JSON-backed tag registry, re-read whenever the file changes.

    A configured file that is missing or unparsable is reported as an error by
    the tag tools instead of looking like an empty tag map.
    """

    def __init__(self, path: Path | str | None) -> None:
        self.path = Path(path).expanduser() if path else None
        self._tags: dict[str, dict[str, Any]] = {}
        self._mtime: int | None = None
        self.error: str | None = None
        self.refresh()

    def refresh(self) -> None:
        if self.path is None:
            return
        try:
            mtime = self.path.stat().st_mtime_ns
        except OSError as exc:
            self._tags, self._mtime = ({}, None)
            self.error = f"TAG_MAP_FILE {self.path} cannot be read: {exc.strerror or exc}"
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._tags, self.error = ({}, f"TAG_MAP_FILE {self.path} is not valid JSON: {exc}")
            return
        if not isinstance(data, dict):
            self._tags, self.error = ({}, f"TAG_MAP_FILE {self.path} must hold a JSON object of tag name -> address")
            return
        bad = [str(name) for name, spec in data.items() if not isinstance(spec, dict)]
        if bad:
            self._tags, self.error = ({}, f"TAG_MAP_FILE {self.path}: entries must be objects ({', '.join(bad)})")
            return
        self._tags = {str(name): dict(spec) for name, spec in data.items()}
        self.error = None

    def _checked(self) -> dict[str, dict[str, Any]]:
        self.refresh()
        if self.error:
            raise TagMapError(self.error)
        return self._tags

    def get(self, name: str) -> dict[str, Any] | None:
        return self._checked().get(name)

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "area": spec.get("area", "db"),
                "description": spec.get("description"),
                "meta": {k: v for k, v in spec.items() if k not in {"area", "description"}},
            }
            for name, spec in self._checked().items()
        ]

    def status(self) -> dict[str, Any]:
        self.refresh()
        return {"path": str(self.path) if self.path else None, "count": len(self._tags), "error": self.error}

    def __len__(self) -> int:
        return len(self._tags)
