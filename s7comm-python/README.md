# s7comm-python

Python implementation of the Siemens S7 MCP server built with `uv`, `mcp[cli]`, and `python-snap7`. The CLI entry point is `s7comm-mcp`. See the [repository README](../README.md) for an overview, the tool list and known limitations.

## Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- Snap7 native library: bundled in the python-snap7 wheels for macOS, glibc Linux (x86_64, aarch64) and Windows x64, so there is nothing to install there. Elsewhere (e.g. musl/Alpine, 32-bit ARM) build or install libsnap7 yourself.

## Setup

From the repository root, with the [mock PLC](../s7comm-mock-server/README.md) running (`uv run s7comm-mock-server` in `s7comm-mock-server/`, port 1102):

```bash
cd s7comm-python
uv sync                                          # installs dependencies into .venv
S7_HOST=127.0.0.1 S7_PORT=1102 uv run s7comm-mcp   # MCP over stdio, against the mock PLC
```

The server speaks MCP over stdio, so on its own it just waits for a client; the [repository README](../README.md#quick-start) has the client configuration. It starts without a reachable PLC: the connection is opened by the first tool call that needs it, and `ping` reports whether the PLC answers.

## Configuration

Settings come from the process environment (for example the `env` block of an MCP client configuration), from a `.env` file, or from a file passed with `--env-file`. Copy [`.env.example`](.env.example) to start one.

- **Implicit `.env`.** In a source checkout the server loads `s7comm-python/.env`: it walks up from the `s7comm_mcp` package to the nearest `pyproject.toml` and uses the `.env` beside it only if that is this project's own `pyproject.toml`. It never reads the working directory, the repository root or anything above, and an installed (non-checkout) server has no implicit `.env`.
- **`--env-file PATH`.** Loads that file instead, e.g. `"args": [..., "run", "s7comm-mcp", "--env-file", "/abs/path/plant.env"]` in a client configuration.
- **Precedence.** Variables already set in the environment win over either file. The file that was loaded is logged on stderr.
- **Safety switches.** `S7_WRITES_ENABLED` and `S7_SYSTEM_CMDS_ENABLED` are honoured only from the process environment or an `--env-file`. An implicit `.env` may set connection settings, but a value in it that would enable a switch is ignored with a warning on stderr naming the variable. A server started from an untrusted project directory therefore cannot be talked into writing by a file it happens to find.

```
S7_HOST=192.168.0.1
S7_PORT=102
S7_RACK=0
S7_SLOT=2
S7_CONNECTION_TYPE=PG
S7_TIMEOUT=5
S7_MAX_RETRIES=3
S7_RETRY_BACKOFF_BASE=0.5
TAG_MAP_FILE=/absolute/path/to/tags.json
```

| Variable | Default | Meaning |
|---|---|---|
| `S7_HOST` | `127.0.0.1` | PLC address |
| `S7_PORT` | `102` | ISO-on-TCP port (the mock uses `1102`) |
| `S7_RACK` / `S7_SLOT` | `0` / `2` | CPU rack and slot (see [Connecting to a real PLC](#connecting-to-a-real-plc)) |
| `S7_CONNECTION_TYPE` | `PG` | Snap7 connection resource: `PG`, `OP` or `S7BASIC` |
| `S7_TIMEOUT` | `5` | Seconds. Bounds the TCP connect, and is Snap7's ping, send and receive timeout |
| `S7_MAX_RETRIES` | `3` | Reconnect-and-retry attempts after a link error (TCP/ISO/timeout). Reads are retried. Writes and RUN/STOP are never resent: only connecting and a short read that checks the link before the request are retried. Errors the CPU reports, such as `CPU : Item not available`, are returned at once |
| `S7_RETRY_BACKOFF_BASE` | `0.5` | Seconds; doubles on each retry |
| `S7_DEBUG` | `false` | `true` logs Snap7 and server debug output to stderr |
| `S7_WRITES_ENABLED` | `false` | `true` lets the write tools change PLC memory and outputs; otherwise they refuse. Not read from an implicit `.env` |
| `S7_SYSTEM_CMDS_ENABLED` | `false` | `true` allows `set_cpu_state` (RUN/STOP), together with `S7_WRITES_ENABLED=true`. Not read from an implicit `.env` |
| `TAG_MAP_FILE` | unset | Path to a JSON tag map (below) |

Invalid values (a port that is not a number, `S7_WRITES_ENABLED=enable`, an unknown connection type) stop the server at startup with exit code 2 and a message on stderr. stdout carries only MCP JSON-RPC.

## Data types

`read_db_typed`, `write_db`, `write_output`, `write_marker` and the batch and tag tools take `data_type` = `BOOL`, `BYTE`, `WORD`, `DWORD`, `INT`, `DINT`, `REAL` or `STRING`. Values are big-endian, as the PLC stores them, and range-checked before anything is sent. Types are strict: addresses, offsets, sizes, bit indexes, byte values and integer values must be JSON integers (never booleans, strings or `4.0`), and REAL values must be numbers.

- `BOOL` uses `bit_index` 0-7 (reads default to bit 0; writes require it). A write changes only that bit, using the S7 BIT transport size, so the other seven bits of the byte are never rewritten. Values must be JSON booleans (`true`/`false`); `1`, `"true"` and the like are refused.
- `STRING` is the S7 `STRING[N]`: a max-length byte, a current-length byte, then the characters (Latin-1). Reads return only the text; `string_length` (the `N`) is optional and, if given, must match the header in the PLC. Writes require `string_length`, check it against the header, refuse text longer than `N`, and write the header plus the text.
- Without `data_type`, writes take raw bytes: a hex string (`"0a ff"`) or a list of values 0-255.
- `bit_index` is accepted only with `BOOL` and `string_length` only with `STRING`; a `size` must match the type. Anything else is refused rather than ignored, so a stray `bit_index` can never turn into a whole-byte write.
- Process-image inputs (`input`) can be read but never written.

### What a write reports

Every write (`write_db`, `write_output`, `write_marker`, `write_tag`, each `write_multiple_vars` entry) and `set_cpu_state` returns `meta.outcome` and `meta.request_sent`:

| `outcome` | `request_sent` | Meaning |
|---|---|---|
| `written` | true | The PLC confirmed it |
| `rejected` | true | The PLC answered with an error; nothing was applied |
| `unknown` | true | Sent, but the reply was lost (or RUN/STOP was accepted but the mode was not reached in time). It may have been applied: check before retrying. It is never resent automatically |
| `not_sent` | false | Stopped before the request went out: writes disabled, invalid arguments, a failed check, or an unreachable PLC |

`write_multiple_vars` first checks every entry locally and then reads every target from the PLC (including STRING headers); if any check fails, nothing is written. The entries are then written in order, and the batch stops at the first `rejected` or `unknown` entry. Its `meta.outcome` is that entry's outcome.

## Tag map

`TAG_MAP_FILE` points at a JSON object mapping tag names to addresses. Use an absolute path in MCP client configurations: a relative path is resolved against the directory the server is started in (`s7comm-python/` when started with `uv --directory s7comm-python run s7comm-mcp`).

```json
{
  "MotorSpeed": { "area": "db", "db_number": 1, "offset": 0, "data_type": "REAL", "description": "Motor speed" },
  "AlarmActive": { "area": "db", "db_number": 2, "offset": 0, "data_type": "BOOL", "bit": 0 },
  "Status": { "area": "db", "db_number": 2, "offset": 2, "data_type": "STRING", "string_length": 30 },
  "PumpCommand": { "area": "output", "byte": 0, "data_type": "BOOL", "bit": 0 }
}
```

Tag and batch entries use the same keys: `area` (`db`, `input`, `output` or `marker`; default `db`), `db_number` (data blocks only), the byte offset as `offset`, `start_offset`, `byte` or `start_byte` (required), `bit` or `bit_index` (BOOL only), `data_type`, `string_length` (STRING only), and `size` for raw reads. Numbers must be JSON integers. An entry without an offset is rejected rather than read at offset 0, and `input` entries can be read but not written.

The file is re-read when it changes. If `TAG_MAP_FILE` is set but the file is missing or is not valid JSON, `list_tags`, `read_tag` and `write_tag` return `success: false` with the reason instead of an empty list.

## Connecting to a real PLC

Try everything against the mock first. Then, for a real CPU:

- **Rack and slot.** S7-300: rack 0, slot 2 (the defaults). S7-1200/1500: rack 0, slot 1, or 0 in some configurations (`S7_SLOT=1`). S7-400: the rack and slot of the CPU in the hardware configuration.
- **PUT/GET (S7-1200/1500).** In TIA Portal, open the CPU properties, go to *Protection & Security > Connection mechanisms* and enable *Permit access with PUT/GET communication from remote partner*. The CPU's protection level must also allow what you do: writes need write access.
- **Non-optimized data blocks (S7-1200/1500).** Absolute offsets only work on DBs without optimized block access: in each DB's properties under *Attributes*, clear *Optimized block access*, then compile and download.
- **Writes.** Keep `S7_WRITES_ENABLED=false` until the session needs to write, and read [SECURITY.md](../SECURITY.md) first.

With the wrong slot, or PUT/GET disabled, the connection or the first request fails and the tool returns `success: false` with the Snap7 error.

## Tests

```bash
uv sync --extra dev
uv run pytest                                   # unit tests, mock PLC tests, MCP over stdio
uv run ruff check . ../s7comm-mock-server
uv run ruff format --check . ../s7comm-mock-server
```

The integration tests start the mock PLC from `../s7comm-mock-server/s7_mock_server.py` on a free loopback port and stop it again; no PLC or second virtualenv is needed.

## Project Layout

```
s7comm-python/
├── README.md
├── pyproject.toml
├── uv.lock
├── .env.example
├── .python-version
├── .gitignore
├── src/s7comm_mcp
│   ├── __init__.py
│   ├── cli.py          # entry point: .env, logging to stderr, config errors
│   ├── config.py       # environment parsing and validation
│   ├── server.py       # FastMCP wiring, argument-error envelopes
│   ├── tools.py        # the MCP tools, encoding and validation
│   └── s7_client.py    # python-snap7 wrapper: connect, retry, bit writes
└── tests/              # pytest suite (see Tests)
```

## Status

All tools return the `{ success, data, error, meta }` envelope and work against the bundled mock. The [known limitations](../README.md#known-limitations) list what is not supported; the server has not yet been validated across a range of real CPUs.
