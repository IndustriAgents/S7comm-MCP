# s7comm-python

Python implementation of the Siemens S7 MCP server built with `uv`, `mcp[cli]`, and `python-snap7`. The CLI entry point is `s7comm-mcp`. See the [repository README](../README.md) for an overview, the tool list and known limitations.

## Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- Snap7 native library: bundled in the python-snap7 wheels for macOS, Linux (x86_64, aarch64) and Windows (x64). Only on other platforms do you need to install it yourself (`brew install snap7` on macOS, `apt install libsnap7-dev` on Debian/Ubuntu, download DLLs for Windows).

## Setup

From the repository root:

```bash
cd s7comm-python
uv sync                 # installs dependencies into .venv
uv run s7comm-mcp       # starts the MCP server over stdio
```

The server connects to the PLC when it starts and exits if the PLC (or the [mock PLC](../s7comm-mock-server/README.md)) is not reachable.

Configure the connection via environment variables or a `.env` file:

```
S7_HOST=192.168.0.1
S7_PORT=102
S7_RACK=0
S7_SLOT=2
S7_CONNECTION_TYPE=PG
S7_TIMEOUT=5
S7_MAX_RETRIES=3
S7_RETRY_BACKOFF_BASE=0.5
S7_WRITES_ENABLED=false
S7_SYSTEM_CMDS_ENABLED=false
TAG_MAP_FILE=./tags.json
```

| Variable | Default | Meaning |
|---|---|---|
| `S7_HOST` | `127.0.0.1` | PLC address |
| `S7_PORT` | `102` | ISO-on-TCP port (the mock uses `1102`) |
| `S7_RACK` / `S7_SLOT` | `0` / `2` | CPU rack and slot |
| `S7_MAX_RETRIES` | `3` | Retries per connect and per operation; `0` reports the original PLC error instead of `Failed to connect` (see [known limitations](../README.md#known-limitations)) |
| `S7_RETRY_BACKOFF_BASE` | `0.5` | Seconds; doubles on each retry |
| `S7_WRITES_ENABLED` | `true` | Set `false` to refuse every write tool |
| `S7_SYSTEM_CMDS_ENABLED` | `false` | Set `true` to allow `set_cpu_state` (RUN/STOP) |
| `TAG_MAP_FILE` | unset | Path to a JSON tag map (below) |
| `S7_CONNECTION_TYPE`, `S7_TIMEOUT` | `PG`, `5` | Read but not currently applied to the connection |

### Tag map

`TAG_MAP_FILE` points at a JSON object mapping tag names to addresses. Data-block entries use `db_number` and `offset`; input/output/marker entries use `byte`. A `start_offset` key is ignored, so such an entry reads offset 0:

```json
{
  "MotorSpeed": { "area": "db", "db_number": 1, "offset": 0, "data_type": "REAL", "description": "Motor speed" },
  "AlarmActive": { "area": "db", "db_number": 2, "offset": 0, "data_type": "BOOL", "bit": 0 }
}
```

The file is re-read when it changes.

## Project Layout

```
s7comm-python/
├── README.md
├── pyproject.toml
├── uv.lock
├── .python-version
├── .gitignore
└── src/s7comm_mcp
    ├── __init__.py
    ├── cli.py
    ├── server.py
    ├── tools.py
    └── s7_client.py
```

## Status

The server implements connection management, configuration parsing, retry helpers, and the MCP tools listed in the [repository README](../README.md#tools), all returning the `{ success, data, error, meta }` envelope. There is no automated test suite yet; the [known limitations](../README.md#known-limitations) list what does not work today.
