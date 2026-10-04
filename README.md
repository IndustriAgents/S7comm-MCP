# S7comm MCP Server

[![CI](https://github.com/IndustriAgents/S7comm-MCP/actions/workflows/ci.yml/badge.svg)](https://github.com/IndustriAgents/S7comm-MCP/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/github/license/IndustriAgents/S7comm-MCP)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple)](https://modelcontextprotocol.io)

A Model Context Protocol (MCP) server for interacting with Siemens S7 PLCs (S7-300/400/1200/1500) over the S7comm protocol, built on [python-snap7](https://github.com/gijzelaerr/python-snap7). The repository provides the Python MCP server plus a mock PLC, so AI agents and MCP-compatible clients can read and write Siemens data blocks and read CPU state and PLC information — and be tried out without touching real hardware. Input, output, marker and SZL access is implemented but does not work yet (see [Known limitations](#known-limitations)).

This server is part of the [IndustriConnect](#part-of-industriconnect) suite of industrial-protocol MCP servers.

## Repository Layout

```text
S7comm-MCP/
├── README.md
├── LICENSE, CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md
├── .github/              # CI, issue/PR templates, IndustriConnect notification
├── s7comm-python/        # Python MCP server (uv managed)
└── s7comm-mock-server/   # Mock Siemens PLC for local testing (uv managed, optional Docker)
```

Inside the IndustriConnect suite this repository is mounted as the `S7comm-Project/` submodule.

The MCP server exposes consistent tool names, schemas, and a `{ success, data, error, meta }` response envelope for easy integration, while the mock server mirrors common PLC patterns for safe local testing.

## Install

Prerequisites: Python 3.11+ and [uv](https://docs.astral.sh/uv/). The python-snap7 wheels bundle the native Snap7 library for macOS, Linux (x86_64, aarch64) and Windows (x64).

```bash
git clone https://github.com/IndustriAgents/S7comm-MCP.git
cd S7comm-MCP
(cd s7comm-python && uv sync)
(cd s7comm-mock-server && uv sync)
```

## Quick Start

1. Start the mock PLC (listens on port 1102 by default):

   ```bash
   cd s7comm-mock-server
   uv run s7comm-mock-server
   ```

2. Point your MCP client at the server. For Claude Desktop, Claude Code or any client that takes the same JSON shape:

   ```json
   {
     "mcpServers": {
       "s7comm": {
         "command": "uv",
         "args": ["--directory", "/absolute/path/to/S7comm-MCP/s7comm-python", "run", "s7comm-mcp"],
         "env": {
           "S7_HOST": "127.0.0.1",
           "S7_PORT": "1102",
           "S7_WRITES_ENABLED": "false"
         }
       }
     }
   }
   ```

   The server connects to the PLC when it starts, so start the mock (or have the PLC reachable) first; otherwise the server exits.

3. Ask the agent to read something, e.g. "read the REAL at DB1 offset 4" (the mock answers `42.0`). [`s7comm-mock-server/TEST_SCENARIOS.md`](s7comm-mock-server/TEST_SCENARIOS.md) lists the mock's memory map.

To try the server without a client, use the [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector):

```bash
npx @modelcontextprotocol/inspector -e S7_HOST=127.0.0.1 -e S7_PORT=1102 -e S7_WRITES_ENABLED=false \
  uv --directory s7comm-python run s7comm-mcp
```

## Features

- S7 data access: data blocks (DB), CPU state, PLC information and system time; inputs, outputs, markers and SZL are wired up but currently broken (see [Known limitations](#known-limitations))
- Typed read/write helpers (BYTE, WORD, DWORD, INT, DINT, REAL, STRING, BOOL)
- Batch reads and writes across several addresses
- Tag map system with `list_tags`, `read_tag`, `write_tag`
- Health tools: `ping`, `get_connection_status`
- Write tools that can be switched off (`S7_WRITES_ENABLED=false`); CPU RUN/STOP off unless `S7_SYSTEM_CMDS_ENABLED=true`
- Configuration via environment variables or a `.env` file
- Mock PLC for confident local testing before touching a real device

### Tools

| Group | Tools |
|---|---|
| Data blocks | `read_db`, `read_db_typed`, `write_db` |
| Inputs, outputs, markers | `read_input`, `read_output`, `write_output`, `read_marker`, `write_marker` |
| Batch | `read_multiple_vars`, `write_multiple_vars` |
| PLC information & control | `read_plc_info`, `read_cpu_state`, `set_cpu_state`, `read_system_time`, `read_szl` |
| Tags | `list_tags`, `read_tag`, `write_tag` |
| Health | `ping`, `get_connection_status` |

The input, output and marker tools and `read_szl` currently return errors; see [Known limitations](#known-limitations).

### Known limitations

Found by running the server against the bundled mock with the locked python-snap7 2.0.2:

- `read_input`, `read_output`, `read_marker`, `write_output` and `write_marker` (and input/output/marker entries in the batch and tag tools) currently return an error: the area code is passed to python-snap7 as a plain integer, which python-snap7 2.x rejects. Data-block access is unaffected.
- After any failed operation — even an ordinary PLC error such as a non-existent DB — the server cannot reconnect and every later call fails with `Failed to connect`. Restart the server to recover. With `S7_MAX_RETRIES` above 0 (the default is 3) the original error — e.g. `CPU : Item not available` for a missing DB — is reported as `Failed to connect`; set `S7_MAX_RETRIES=0` to see it.
- Entries in `read_multiple_vars`, `write_multiple_vars` and the tag map address data blocks with `offset` and inputs/outputs/markers with `byte` (or `start_byte`). A `start_offset` key in a batch or tag entry is silently ignored and the entry reads or writes offset 0.
- `read_szl` returns an error because the SZL result is not converted to JSON.
- `STRING` reads return the S7 string header bytes and padding along with the text.
- `BOOL` writes write the whole byte, clearing the other bits in that byte.
- `S7_TIMEOUT` and `S7_CONNECTION_TYPE` are read but not applied to the connection.

## Components

- **s7comm-python**: Python 3.11+ server built with `mcp[cli]`, `python-snap7`, and `uv`. Provides the `s7comm-mcp` CLI entry point. Configuration is documented in [`s7comm-python/README.md`](s7comm-python/README.md).
- **s7comm-mock-server**: uv-managed project that simulates a Siemens PLC with seeded data blocks, I/O and markers. See [`s7comm-mock-server/README.md`](s7comm-mock-server/README.md).

## Status

Early stage (0.1.0). The server implements the tools above and has been exercised against the bundled mock; there is no automated test suite yet, and it has not been validated across a range of real CPUs. CI installs both projects from their lockfiles and checks that they build and register their tools.

## Security

S7comm has no authentication or encryption: anything that can reach the PLC's port 102 can read and write it, and the write tools are **enabled by default**. Start against the mock, set `S7_WRITES_ENABLED=false` for read-only sessions, keep the server on the control network beside its MCP client, and never point it at a safety system. See [SECURITY.md](SECURITY.md) for the full guidance and how to report a vulnerability.

## Part of IndustriConnect

[IndustriConnect](https://github.com/IndustriAgents/IndustriConnect) is a suite of MCP servers for industrial protocols — Modbus, MQTT/Sparkplug B, OPC UA, BACnet, DNP3, EtherCAT, EtherNet/IP, PROFIBUS, PROFINET and S7comm — that share one layout and one response envelope, plus a web UI (`mcp-manager-ui`) for driving them. This repository is developed on its own and pinned into the suite as the `S7comm-Project` git submodule, so changes to the S7comm server land here.

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for local setup, testing against the mock, and pull-request conventions. By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## References

- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Snap7 / python-snap7](http://snap7.sourceforge.net/)
- [Siemens S7 S7comm Overview](https://wiki.wireshark.org/S7comm)

## License

MIT — see [LICENSE](LICENSE).
