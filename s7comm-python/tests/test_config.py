"""Configuration: read-only defaults, runtime parsing, validation and .env lookup."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from s7comm_mcp.cli import find_env_file
from s7comm_mcp.config import ConfigError
from s7comm_mcp.s7_client import S7Client, S7ClientConfig
from s7comm_mcp.server import S7CommMCPServer
from s7comm_mcp.tools import ToolConfig


def test_writes_and_system_commands_are_off_by_default(clean_env: pytest.MonkeyPatch) -> None:
    config = ToolConfig.from_env()
    assert config.writes_enabled is False
    assert config.system_cmds_enabled is False
    assert config.tag_map_path is None


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE"])
def test_writes_can_be_enabled(clean_env: pytest.MonkeyPatch, value: str) -> None:
    clean_env.setenv("S7_WRITES_ENABLED", value)
    assert ToolConfig.from_env().writes_enabled is True


def test_unparsable_boolean_is_a_config_error(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("S7_WRITES_ENABLED", "enable")
    with pytest.raises(ConfigError, match="S7_WRITES_ENABLED"):
        ToolConfig.from_env()


def test_environment_is_read_at_construction_not_import(clean_env: pytest.MonkeyPatch) -> None:
    # The modules are already imported; values set now must still be picked up.
    clean_env.setenv("S7_HOST", "10.1.2.3")
    clean_env.setenv("S7_PORT", "1102")
    clean_env.setenv("S7_SLOT", "1")
    clean_env.setenv("S7_TIMEOUT", "2.5")
    clean_env.setenv("S7_CONNECTION_TYPE", "op")
    clean_env.setenv("S7_WRITES_ENABLED", "true")
    server = S7CommMCPServer()
    assert (server.client.config.host, server.client.config.port, server.client.config.slot) == ("10.1.2.3", 1102, 1)
    assert server.client.config.timeout == 2.5
    assert server.client.config.connection_type_code == 2
    assert server.tool_config.writes_enabled is True


def test_defaults(clean_env: pytest.MonkeyPatch) -> None:
    config = S7ClientConfig.from_env()
    assert (config.host, config.port, config.rack, config.slot) == ("127.0.0.1", 102, 0, 2)
    assert config.connection_type == "PG" and config.connection_type_code == 1
    assert config.timeout_ms == 5000
    assert config.debug is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("S7_PORT", "abc"),
        ("S7_PORT", "70000"),
        ("S7_RACK", "9"),
        ("S7_SLOT", "-1"),
        ("S7_TIMEOUT", "0"),
        ("S7_MAX_RETRIES", "x"),
        ("S7_CONNECTION_TYPE", "FAST"),
        ("S7_DEBUG", "maybe"),
    ],
)
def test_invalid_values_fail_at_startup(clean_env: pytest.MonkeyPatch, name: str, value: str) -> None:
    clean_env.setenv(name, value)
    with pytest.raises(ConfigError, match=name):
        S7ClientConfig.from_env()


def test_s7_debug_enables_debug_logging(clean_env: pytest.MonkeyPatch) -> None:
    import logging

    clean_env.setenv("S7_DEBUG", "true")
    S7CommMCPServer()
    assert logging.getLogger("s7comm_mcp").level == logging.DEBUG
    assert logging.getLogger("snap7").level == logging.DEBUG
    logging.getLogger("s7comm_mcp").setLevel(logging.NOTSET)
    logging.getLogger("snap7").setLevel(logging.NOTSET)


def test_server_starts_without_a_plc(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("S7_PORT", "1")  # nothing listens there
    server = S7CommMCPServer()
    tools = asyncio.run(server._mcp.list_tools())
    assert len(tools) == 20
    assert server.client.connection_status()["connected"] is False


def test_env_file_lookup_walks_up_from_the_package(tmp_path: Path) -> None:
    project = tmp_path / "s7comm-python"
    package = project / "src" / "s7comm_mcp"
    package.mkdir(parents=True)
    (tmp_path / ".env").write_text("S7_HOST=repo-root\n")
    assert find_env_file(package / "cli.py") == tmp_path / ".env"
    (project / ".env").write_text("S7_HOST=project\n")
    assert find_env_file(package / "cli.py") == project / ".env"


def test_cli_reports_config_errors_on_stderr_only(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("S7_PORT", "not-a-port")
    result = subprocess.run(
        [sys.executable, "-m", "s7comm_mcp.cli"],
        input="",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "configuration error" in result.stderr and "S7_PORT" in result.stderr


def test_client_uses_injected_config() -> None:
    config = S7ClientConfig(host="192.0.2.10", port=102)
    assert S7Client(config).connection_status()["host"] == "192.0.2.10"
