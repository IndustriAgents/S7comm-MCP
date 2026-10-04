# s7comm-mock-server

Lightweight Siemens S7 mock built with `python-snap7`. It registers a handful of data blocks, inputs, outputs, and marker bytes so you can test the [S7comm MCP server](../s7comm-python/README.md) locally without real hardware. The Snap7 server it runs on emulates a CPU 315-2 PN/DP.

## Usage

From the repository root:

```bash
cd s7comm-mock-server
uv sync
uv run s7comm-mock-server                     # all interfaces, port 1102
uv run s7comm-mock-server --host 127.0.0.1    # this machine only
```

Or with Docker. The image installs the versions pinned in `uv.lock` with a pinned uv and runs as an unprivileged user. `run-docker.sh` publishes port 1102 on `127.0.0.1` only; edit its `-p` option to expose it to other hosts:

```bash
cd s7comm-mock-server
bash run-docker.sh
```

By default the mock listens on port `1102` on all interfaces (port 102, the real S7 port, is privileged on most systems). Use `--host 127.0.0.1` to keep it local. It simulates:

- DB1: Motor telemetry (REAL values, a DINT batch counter)
- DB2: Alarms and configuration bits, a status STRING
- DB3: A 1024-byte bulk area, larger than one PDU (for multi-PDU writes)
- Inputs: Sensors toggling periodically
- Outputs: Actuator commands (read/write)
- Markers: A system marker bit

Point the MCP server at it with `S7_HOST=127.0.0.1 S7_PORT=1102`. The full memory map and the values to expect are in [TEST_SCENARIOS.md](TEST_SCENARIOS.md).

CLI flags (or the matching environment variables) set the address, update interval and seeded values:

| Flag | Environment variable | Default |
|---|---|---|
| `--host` | `MOCK_S7_HOST` | `0.0.0.0` (all interfaces; IPv4 only) |
| `--port` | `MOCK_S7_PORT` | `1102` |
| `--update-interval` | `MOCK_S7_UPDATE_INTERVAL` | `1.0` seconds |
| `--motor-speed` | `MOCK_S7_MOTOR_SPEED` | `1450.0` |
| `--temp-start` | `MOCK_S7_TEMP_START` | `68.0` |
| `--exit-on-stdin-eof` | | off: when set, the mock exits once its stdin closes (the test suite uses this so a killed test run leaves no mock behind) |

Run `uv run s7comm-mock-server --help` for the full list. The rack and slot are not configurable: the Snap7 server accepts any.

The mock stops cleanly on Ctrl+C or SIGTERM (e.g. `docker stop`). Its status lines are flushed immediately, so they also appear when output is redirected to a file or a pipe.

## Status

The script seeds a deterministic memory map and periodically mutates select values to mimic live plant data. The MCP server's test suite (`s7comm-python/tests`) starts this mock to run its integration tests. Extend `s7_mock_server.py` to add scenarios, random jitter, command handling, or to consume JSON fixtures for more complex integration tests.
