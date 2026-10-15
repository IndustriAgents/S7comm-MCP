"""Environment parsing shared by the client and tool configuration.

Nothing here runs at import time: the ``from_env`` constructors that use these
helpers are called by :class:`~s7comm_mcp.server.S7CommMCPServer` after
:func:`s7comm_mcp.cli.main` has loaded the ``.env`` file.
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version

PROJECT_NAME = "industriagents-s7comm-mcp"

_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off", ""}


def package_version() -> str:
    """This package's installed version (reported as serverInfo.version)."""
    try:
        return version(PROJECT_NAME)
    except PackageNotFoundError:  # pragma: no cover - running from an uninstalled tree
        return "0+unknown"


class ConfigError(ValueError):
    """Raised when an environment variable holds an unusable value."""


def env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise ConfigError(f"{name}={value!r} is not a boolean (use true or false)")


def env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value.strip(), 0)
    except ValueError:
        raise ConfigError(f"{name}={value!r} is not an integer") from None
    if not minimum <= parsed <= maximum:
        raise ConfigError(f"{name}={parsed} is out of range ({minimum}..{maximum})")
    return parsed


def env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value.strip())
    except ValueError:
        raise ConfigError(f"{name}={value!r} is not a number") from None
    if not minimum <= parsed <= maximum:
        raise ConfigError(f"{name}={parsed} is out of range ({minimum}..{maximum})")
    return parsed
