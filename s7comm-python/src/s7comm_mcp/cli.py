"""Command-line entry point for the S7comm MCP server.

stdout carries the MCP JSON-RPC stream, so everything else (logs, warnings,
configuration errors) goes to stderr.

Settings come from the process environment, from an explicit ``--env-file``,
and from an implicit ``.env`` in the ``s7comm-python`` project directory. The
safety switches (:data:`GATE_VARIABLES`) are only honoured from the first two:
an implicit ``.env`` may set connection settings but cannot enable writes.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from dotenv import dotenv_values

from .config import ConfigError
from .server import S7CommMCPServer

logger = logging.getLogger("s7comm_mcp")

#: Variables that enable writes or commands. Never taken from an implicit .env.
GATE_VARIABLES = ("S7_WRITES_ENABLED", "S7_SYSTEM_CMDS_ENABLED")
_FALSE_VALUES = {"", "0", "false", "no", "n", "off"}
PROJECT_NAME = "s7comm-mcp"


def package_version() -> str:
    try:
        return version(PROJECT_NAME)
    except PackageNotFoundError:  # pragma: no cover - running from an uninstalled tree
        return "0+unknown"


def find_env_file(start: Path | None = None) -> Path | None:
    """Return the implicit ``.env``: the one in the ``s7comm-python`` project directory.

    Walks up from the ``s7comm_mcp`` package to the nearest ``pyproject.toml``.
    If that file belongs to this project (a source checkout, ``uv sync``), its
    directory's ``.env`` is used. Any other project, or none (an installed
    package), means there is no implicit ``.env``; use ``--env-file``. The
    search never looks at the working directory or above the project.
    """
    directory = (start or Path(__file__)).resolve()
    if directory.is_file():
        directory = directory.parent
    for candidate_dir in (directory, *directory.parents):
        pyproject = candidate_dir / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            name = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {}).get("name")
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            return None
        env_file = candidate_dir / ".env"
        return env_file if name == PROJECT_NAME and env_file.is_file() else None
    return None


def load_env_file(path: Path, *, explicit: bool) -> None:
    """Copy settings from ``path`` into ``os.environ`` without overriding it.

    Variables already set in the process environment (e.g. by the MCP client's
    ``env`` block) win. From an implicit file, the safety switches are dropped:
    a value that would enable one is reported on stderr and ignored.
    """
    for key, value in dotenv_values(path).items():
        if value is None:
            continue
        if not explicit and key in GATE_VARIABLES:
            if value.strip().lower() not in _FALSE_VALUES:
                logger.warning(
                    "Ignoring %s=%s from %s: safety switches are only read from the process environment "
                    "or a file passed with --env-file",
                    key,
                    value,
                    path,
                )
            continue
        os.environ.setdefault(key, value)
    logger.info("Loaded settings from %s%s", path, "" if explicit else " (safety switches excluded)")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="s7comm-mcp",
        description="MCP server for Siemens S7 PLCs over S7comm (MCP over stdio).",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="settings file to load instead of s7comm-python/.env; unlike the implicit file it may also "
        "set S7_WRITES_ENABLED and S7_SYSTEM_CMDS_ENABLED",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Entrypoint used by the ``s7comm-mcp`` console script."""
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.env_file is not None:
        if not args.env_file.is_file():
            print(f"s7comm-mcp: --env-file {args.env_file} does not exist", file=sys.stderr)
            raise SystemExit(2)
        load_env_file(args.env_file, explicit=True)
    else:
        implicit = find_env_file()
        if implicit is not None:
            load_env_file(implicit, explicit=False)

    # Configuration is read here, after the env files, never at import time.
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
