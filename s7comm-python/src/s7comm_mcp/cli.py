"""Command-line entry point for the S7comm MCP server.

stdout carries the MCP JSON-RPC stream, so everything else (logs, warnings,
configuration errors) goes to stderr.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from .config import ConfigError
from .server import S7CommMCPServer

logger = logging.getLogger("s7comm_mcp")


def find_env_file(start: Path | None = None) -> Path | None:
    """Return the ``.env`` the server loads, or ``None``.

    The search starts in the directory holding the ``s7comm_mcp`` package and
    walks up through its parents; the first ``.env`` found wins. For a ``uv
    sync`` checkout that is ``s7comm-python/src/s7comm_mcp``, then
    ``s7comm-python/src``, ``s7comm-python``, the repository root and so on, so
    ``s7comm-python/.env`` is the place for it. It does not depend on the
    working directory the MCP client starts the server in.
    """
    directory = (start or Path(__file__)).resolve()
    if directory.is_file():
        directory = directory.parent
    for candidate_dir in (directory, *directory.parents):
        candidate = candidate_dir / ".env"
        if candidate.is_file():
            return candidate
    return None


def main() -> None:
    """Entrypoint used by the ``s7comm-mcp`` console script."""
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    env_file = find_env_file()
    if env_file is not None:
        # Variables already set in the environment (e.g. by the MCP client's
        # "env" block) take precedence over the file.
        load_dotenv(env_file, override=False)
        logger.info("Loaded settings from %s", env_file)

    # Configuration is read here, after load_dotenv, never at import time.
    try:
        server = S7CommMCPServer()
    except ConfigError as exc:
        print(f"s7comm-mcp: configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    try:
        server.run()
    except KeyboardInterrupt:
        # Allow graceful shutdown when the user presses Ctrl+C.
        pass


if __name__ == "__main__":
    main()
