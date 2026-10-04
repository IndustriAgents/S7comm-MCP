# S7comm MCP Server

[![CI](https://github.com/IndustriAgents/S7comm-MCP/actions/workflows/ci.yml/badge.svg)](https://github.com/IndustriAgents/S7comm-MCP/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/github/license/IndustriAgents/S7comm-MCP)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple)](https://modelcontextprotocol.io)

A Model Context Protocol (MCP) server for interacting with Siemens S7 PLCs (S7-300/400/1200/1500) over the S7comm protocol, built on [python-snap7](https://github.com/gijzelaerr/python-snap7). The repository provides the Python MCP server plus a mock PLC, so AI agents and MCP-compatible clients can read data blocks, inputs, outputs and markers, read CPU state, PLC information and system status lists, and (when explicitly enabled) write to the PLC. Everything can be tried out against the mock without touching real hardware.

The server is **read-only by default**: the write tools refuse until `S7_WRITES_ENABLED=true` is set. S7-1200/1500 CPUs need the right slot, PUT/GET access and non-optimized data blocks; see [Connecting to a real PLC](s7comm-python/README.md#connecting-to-a-real-plc).

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

Prerequisites: Python 3.11+ and [uv](https://docs.astral.sh/uv/). The python-snap7 wheels bundle the native Snap7 library for macOS, glibc Linux (x86_64, aarch64) and Windows (x64); elsewhere, install libsnap7 yourself (see [`s7comm-python/README.md`](s7comm-python/README.md#prerequisites)).

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
           "S7_PORT": "1102"
         }
       }
     }
   }
   ```

   This configuration is read-only (the default). Add `"S7_WRITES_ENABLED": "true"` to the `env` block only if the agent should be able to write. The server starts without a reachable PLC and connects on the first tool call that needs it; `ping` tells you whether the PLC answers.

3. Ask the agent to read something, e.g. "read the REAL at DB1 offset 4" (the mock answers `42.0`). [`s7comm-mock-server/TEST_SCENARIOS.md`](s7comm-mock-server/TEST_SCENARIOS.md) lists the mock's memory map.

To try the server without a client, use the [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector):

```bash
npx @modelcontextprotocol/inspector -e S7_HOST=127.0.0.1 -e S7_PORT=1102 \
  uv --directory s7comm-python run s7comm-mcp
```

## Features

- S7 data access: data blocks (DB), inputs, outputs, markers, CPU state, PLC identity (order code, firmware), system time and SZL lists
- Typed read/write helpers (BOOL, BYTE, WORD, DWORD, INT, DINT, REAL, STRING). A `BOOL` write changes exactly one bit (S7 BIT transport size); a `STRING` is the S7 string with its 2-byte length header
- Batch reads and writes across several addresses
- Tag map system with `list_tags`, `read_tag`, `write_tag`
- Health tools: `ping` (round trip to the PLC), `get_connection_status`
- Read-only by default: write tools refuse unless `S7_WRITES_ENABLED=true`; CPU RUN/STOP also needs `S7_SYSTEM_CMDS_ENABLED=true`; inputs are never written
- Writes reach the PLC at most once: a lost reply is reported (`meta.outcome: "unknown"`, "may have been applied; check before retrying"), never resent
- Bad arguments, PLC errors and connection problems come back as `success: false` envelopes; the server reconnects after a dropped link
- Configuration via environment variables, `s7comm-python/.env` or `--env-file` (see [`s7comm-python/README.md`](s7comm-python/README.md#configuration)); the safety switches are never taken from an implicitly found `.env`
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

The write tools (`write_db`, `write_output`, `write_marker`, `write_multiple_vars`, `write_tag`) are listed but refuse unless `S7_WRITES_ENABLED=true`; `set_cpu_state` refuses unless both `S7_WRITES_ENABLED=true` and `S7_SYSTEM_CMDS_ENABLED=true`. Every write reports `meta.outcome` (`written`, `rejected`, `unknown` or `not_sent`) and `meta.request_sent`.

### Known limitations

- Absolute addressing only. Data blocks with optimized block access (the TIA Portal default on S7-1200/1500) cannot be read or written by offset, and symbolic names are not supported; use a tag map for readable names.
- Data types: BOOL, BYTE, WORD, DWORD, INT, DINT, REAL and STRING. There is no WSTRING, CHAR, LREAL, DATE/TIME, S5TIME, timer or counter support. STRING text is single-byte (Latin-1).
- `read_multiple_vars` issues one request per entry, so the values are not one consistent snapshot. `write_multiple_vars` checks every entry (on the PLC too) before writing any, but it is not atomic on the PLC: if a write is still refused (e.g. by the protection level) or its reply is lost, it stops there, and earlier entries stay written.
- `set_cpu_state` uses a hot start for RUN. Whether RUN/STOP, SZL lists and CPU information are available depends on the CPU family, firmware and protection level.
- Each connection attempt first opens and closes a plain TCP connection to bound the connect time by `S7_TIMEOUT` (Snap7 leaves the TCP connect to the operating system).
- Tested against the bundled Snap7-based mock (an emulated CPU 315-2 PN/DP), not yet against a range of real CPUs.

## Components

- **s7comm-python**: Python 3.11+ server built with `mcp[cli]`, `python-snap7`, and `uv`. Provides the `s7comm-mcp` CLI entry point. Configuration is documented in [`s7comm-python/README.md`](s7comm-python/README.md).
- **s7comm-mock-server**: uv-managed project that simulates a Siemens PLC with seeded data blocks, I/O and markers. See [`s7comm-mock-server/README.md`](s7comm-mock-server/README.md).

## Status

Early stage (0.1.0). Every tool above works against the bundled mock PLC. A pytest suite under `s7comm-python/tests` covers the encoding and validation logic, the client's reconnect and error handling, every tool against the mock (including single-bit writes that leave the other bits alone), and an end-to-end run of the real server over stdio with the MCP client SDK. CI installs both projects from their lockfiles, runs `ruff` and the tests, and smoke-tests the mock. It has not been validated across a range of real CPUs.

## Security

S7comm has no authentication or encryption: anything that can reach the PLC's port 102 can read and write it. This server is **read-only by default**; the write tools refuse until `S7_WRITES_ENABLED=true`. Start against the mock, enable writes only for sessions that need them, keep the server on the control network beside its MCP client, and never point it at a safety system. See [SECURITY.md](SECURITY.md) for the full guidance and how to report a vulnerability.

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
