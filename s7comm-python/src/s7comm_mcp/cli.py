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
from pathlib import Path

from dotenv import dotenv_values

from .config import PROJECT_NAME, ConfigError, package_version
from .server import S7CommMCPServer

logger = logging.getLogger("s7comm_mcp")

#: Variables that enable writes or commands. Never taken from an implicit .env.
GATE_VARIABLES = ("S7_WRITES_ENABLED", "S7_SYSTEM_CMDS_ENABLED")
#: What an implicit .env may set (besides the gates, which it may not).
OWN_PREFIX = "S7_"
OWN_EXTRAS = ("TAG_MAP_FILE",)
_FALSE_VALUES = {"", "0", "false", "no", "n", "off"}


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


def is_own_setting(name: str) -> bool:
    """A configuration variable of this server (S7_* or TAG_MAP_FILE)."""
    return name.startswith(OWN_PREFIX) or name in OWN_EXTRAS


def load_env_file(path: Path, *, explicit: bool) -> None:
    """Copy settings from ``path`` into ``os.environ`` without overriding it.

    Variables already set in the process environment (e.g. by the MCP client's
    ``env`` block) win. An explicit ``--env-file`` may set anything. From the
    implicit ``.env`` only this server's own non-gate settings are taken:

    - keys are compared case-insensitively (Windows upper-cases environment
      names, so ``s7_writes_enabled`` would become ``S7_WRITES_ENABLED``);
      python-dotenv already strips an ``export`` prefix and quotes;
    - a safety switch that would be enabled is reported on stderr and ignored;
    - any other key (PATH, LD_PRELOAD, DYLD_*, PYTHON*, ...) is reported and
      ignored, since it could change how the process itself behaves.
    """
    for raw_key, value in dotenv_values(path).items():
        if value is None:
            continue
        key = raw_key.strip()
        name = key.upper()
        if explicit:
            os.environ.setdefault(key, value)
            continue
        if name in GATE_VARIABLES:
            if value.strip().lower() not in _FALSE_VALUES:
                logger.warning(
                    "Ignoring %s=%s from %s: safety switches are only read from the process environment "
                    "or a file passed with --env-file",
                    key,
                    value,
                    path,
                )
            continue
        if not is_own_setting(name):
            logger.warning(
                "Ignoring %s from %s: the implicit .env may only set S7_* and TAG_MAP_FILE "
                "(use the process environment or --env-file for anything else)",
                key,
                path,
            )
            continue
        os.environ.setdefault(name, value)
    logger.info("Loaded settings from %s%s", path, "" if explicit else " (own non-gate settings only)")


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
