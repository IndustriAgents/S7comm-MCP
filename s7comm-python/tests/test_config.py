"""Configuration: read-only defaults, runtime parsing, validation and .env lookup."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from s7comm_mcp.cli import find_env_file, load_env_file
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


def make_project(root: Path, name: str = "s7comm-mcp") -> Path:
    project = root / "s7comm-python"
    (project / "src" / "s7comm_mcp").mkdir(parents=True)
    (project / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n')
    return project


def test_implicit_env_file_is_the_project_one(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    cli_py = project / "src" / "s7comm_mcp" / "cli.py"
    (tmp_path / ".env").write_text("S7_HOST=repo-root\n")  # above the project: never used
    assert find_env_file(cli_py) is None
    (project / ".env").write_text("S7_HOST=project\n")
    assert find_env_file(cli_py) == project / ".env"


def test_no_implicit_env_file_inside_another_project(tmp_path: Path) -> None:
    # An installed package inside, say, the MCP client's project venv.
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "someone-elses-app"\n')
    (tmp_path / ".env").write_text("S7_HOST=untrusted\n")
    site = tmp_path / ".venv" / "lib" / "python3.11" / "site-packages" / "s7comm_mcp"
    site.mkdir(parents=True)
    assert find_env_file(site / "cli.py") is None


def test_implicit_env_file_cannot_enable_writes(
    tmp_path: Path, clean_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("S7_HOST=10.0.0.9\nS7_WRITES_ENABLED=true\nS7_SYSTEM_CMDS_ENABLED=1\n")
    with caplog.at_level("WARNING", logger="s7comm_mcp"):
        load_env_file(env_file, explicit=False)
    assert os.environ["S7_HOST"] == "10.0.0.9"  # connection settings still apply
    assert "S7_WRITES_ENABLED" not in os.environ and "S7_SYSTEM_CMDS_ENABLED" not in os.environ
    assert "Ignoring S7_WRITES_ENABLED=true" in caplog.text
    assert "Ignoring S7_SYSTEM_CMDS_ENABLED=1" in caplog.text
    tool_config = ToolConfig.from_env()
    assert (tool_config.writes_enabled, tool_config.system_cmds_enabled) == (False, False)


@pytest.mark.parametrize(
    "line",
    [
        "s7_writes_enabled=true",  # Windows would upper-case this into the real switch
        "S7_Writes_Enabled=1",
        "export S7_WRITES_ENABLED=true",
        "export s7_writes_enabled='yes'",
        'S7_WRITES_ENABLED="on"',
        " S7_SYSTEM_CMDS_ENABLED = true",
    ],
)
def test_gate_filter_ignores_case_export_and_quotes(
    tmp_path: Path, clean_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, line: str
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(line + "\n")
    with caplog.at_level("WARNING", logger="s7comm_mcp"):
        load_env_file(env_file, explicit=False)
    assert not any(k.upper() in ("S7_WRITES_ENABLED", "S7_SYSTEM_CMDS_ENABLED") for k in os.environ)
    assert "safety switches are only read" in caplog.text
    config = ToolConfig.from_env()
    assert (config.writes_enabled, config.system_cmds_enabled) == (False, False)


@pytest.mark.parametrize(
    "key", ["PATH", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "PYTHONPATH", "PYTHONSTARTUP", "FASTMCP_LOG_LEVEL", "HOME"]
)
def test_implicit_env_file_only_sets_own_variables(
    tmp_path: Path, clean_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, key: str
) -> None:
    clean_env.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"{key}=/tmp/evil\nS7_HOST=10.0.0.7\ntag_map_file=/srv/tags.json\n")
    with caplog.at_level("WARNING", logger="s7comm_mcp"):
        load_env_file(env_file, explicit=False)
    assert key not in os.environ
    assert f"Ignoring {key} from" in caplog.text
    assert os.environ["S7_HOST"] == "10.0.0.7" and os.environ["TAG_MAP_FILE"] == "/srv/tags.json"


def test_explicit_env_file_may_set_any_variable(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    clean_env.delenv("S7COMM_TEST_EXTRA", raising=False)
    env_file = tmp_path / "plant.env"
    env_file.write_text("S7COMM_TEST_EXTRA=1\nS7_HOST=10.0.0.8\n")
    load_env_file(env_file, explicit=True)
    assert os.environ["S7COMM_TEST_EXTRA"] == "1" and os.environ["S7_HOST"] == "10.0.0.8"
    clean_env.delenv("S7COMM_TEST_EXTRA")


def test_false_gates_in_an_implicit_env_file_are_dropped_quietly(
    tmp_path: Path, clean_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("S7_WRITES_ENABLED=false\n")
    with caplog.at_level("WARNING", logger="s7comm_mcp"):
        load_env_file(env_file, explicit=False)
    assert "S7_WRITES_ENABLED" not in os.environ and "Ignoring" not in caplog.text


def test_explicit_env_file_may_enable_writes(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / "plant.env"
    env_file.write_text("S7_WRITES_ENABLED=true\nS7_SYSTEM_CMDS_ENABLED=true\n")
    load_env_file(env_file, explicit=True)
    assert ToolConfig.from_env().writes_enabled is True
    assert ToolConfig.from_env().system_cmds_enabled is True


def test_process_environment_wins_over_env_files(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("S7_HOST", "from-client-config")
    clean_env.setenv("S7_WRITES_ENABLED", "true")
    env_file = tmp_path / ".env"
    env_file.write_text("S7_HOST=from-file\nS7_WRITES_ENABLED=false\n")
    load_env_file(env_file, explicit=True)
    assert os.environ["S7_HOST"] == "from-client-config"
    assert ToolConfig.from_env().writes_enabled is True


def test_cli_end_to_end_gate_handling(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    """The real entry point: an implicit .env cannot enable writes, --env-file can."""
    script = (
        "import sys, s7comm_mcp.cli as cli, s7comm_mcp.server as server\n"
        "from pathlib import Path\n"
        "cli.find_env_file = lambda start=None: Path(sys.argv[1]) if sys.argv[1] != '-' else None\n"
        "class Probe:\n"
        "    def __init__(self):\n"
        "        from s7comm_mcp.tools import ToolConfig\n"
        "        print('writes=%s' % ToolConfig.from_env().writes_enabled, file=sys.stderr)\n"
        "    def run(self): pass\n"
        "cli.S7CommMCPServer = Probe\n"
        "cli.main(sys.argv[2:])\n"
    )
    env_file = tmp_path / ".env"
    env_file.write_text("S7_WRITES_ENABLED=true\n")

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", script, *args], capture_output=True, text=True, timeout=30, input=""
        )

    implicit = run(str(env_file))
    assert "writes=False" in implicit.stderr and "Ignoring S7_WRITES_ENABLED=true" in implicit.stderr
    assert implicit.stdout == ""
    explicit = run("-", "--env-file", str(env_file))
    assert "writes=True" in explicit.stderr and explicit.stdout == ""
    missing = run("-", "--env-file", str(tmp_path / "nope.env"))
    assert missing.returncode == 2 and "does not exist" in missing.stderr


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


def test_env_files_in_the_working_directory_and_its_parents_are_not_read(tmp_path: Path) -> None:
    """Rule 3c, end to end: the real server started from an untrusted project directory."""
    import json

    from support import SERVER_ROOT

    assert not (SERVER_ROOT / ".env").exists(), "this test needs no s7comm-python/.env"
    project = tmp_path / "client-project"
    workdir = project / "sub"
    workdir.mkdir(parents=True)
    for directory in (project, workdir):
        (directory / ".env").write_text("S7_HOST=10.6.6.6\nS7_WRITES_ENABLED=true\nTAG_MAP_FILE=/tmp/evil.json\n")
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_connection_status", "arguments": {}},
        },
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_tags", "arguments": {}}},
    ]
    env = {"PATH": "/usr/bin:/bin", "PYTHONUNBUFFERED": "1"}
    process = subprocess.Popen(
        [sys.executable, "-m", "s7comm_mcp.cli"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=workdir,
        env=env,
    )
    responses: dict[int, dict[str, object]] = {}
    try:
        assert process.stdin and process.stdout
        for request in requests:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            while "id" in request and request["id"] not in responses:
                message = json.loads(process.stdout.readline())
                if "id" in message:
                    responses[message["id"]] = message
        _, stderr = process.communicate(timeout=30)  # stdin closes only after every answer arrived
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    status = responses[2]["result"]["structuredContent"]["data"]
    assert status["host"] == "127.0.0.1"  # not 10.6.6.6 from either .env
    tags = responses[3]["result"]["structuredContent"]
    assert tags["success"] is True and tags["meta"]["tag_map_file"] is None
    assert "Loaded settings" not in stderr


def test_an_installed_package_has_no_implicit_env_file(tmp_path: Path) -> None:
    # site-packages with no pyproject.toml anywhere above: nothing is read.
    site = tmp_path / "lib" / "python3.11" / "site-packages" / "s7comm_mcp"
    site.mkdir(parents=True)
    (tmp_path / ".env").write_text("S7_HOST=untrusted\n")
    (site.parent / ".env").write_text("S7_HOST=untrusted\n")
    assert find_env_file(site / "cli.py") is None


def test_an_installed_copy_inside_a_checkout_reads_no_env_file(tmp_path: Path) -> None:
    # Review round 3: a venv inside s7comm-python/ (or any dir whose pyproject is
    # named s7comm-mcp) used to make the installed copy read that .env.
    project = make_project(tmp_path)
    (project / ".env").write_text("S7_HOST=checkout\n")
    for packages in ("site-packages", "dist-packages"):
        installed = project / ".venv" / "lib" / "python3.11" / packages / "s7comm_mcp"
        installed.mkdir(parents=True)
        assert find_env_file(installed / "cli.py") is None


def test_s7_request_deadline_is_configurable(clean_env: pytest.MonkeyPatch) -> None:
    assert S7ClientConfig.from_env().request_deadline == 45.0
    clean_env.setenv("S7_REQUEST_DEADLINE", "20")
    assert S7ClientConfig.from_env().request_deadline == 20.0
    clean_env.setenv("S7_REQUEST_DEADLINE", "0")
    with pytest.raises(ConfigError, match="S7_REQUEST_DEADLINE"):
        S7ClientConfig.from_env()


def test_exit_guard_ends_a_process_with_a_stuck_worker() -> None:
    script = (
        "import threading, time\n"
        "from s7comm_mcp.cli import _exit_even_if_a_worker_is_stuck\n"
        "threading.Thread(target=time.sleep, args=(60,), name='stuck-snap7-call').start()\n"
        "_exit_even_if_a_worker_is_stuck(0.5)\n"
    )
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0 and time.monotonic() - started < 10
    assert "stuck-snap7-call" in result.stderr
