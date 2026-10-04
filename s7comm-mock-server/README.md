# s7comm-mock-server

Lightweight Siemens S7 mock built with `python-snap7`. It registers a handful of data blocks, inputs, outputs, and marker bytes so you can test the [S7comm MCP server](../s7comm-python/README.md) locally without real hardware.

## Usage

From the repository root:

```bash
cd s7comm-mock-server
uv sync
uv run s7comm-mock-server
```

Or with Docker (the image installs the versions pinned in `uv.lock` and publishes port 1102):

```bash
cd s7comm-mock-server
bash run-docker.sh
```

By default the mock listens on port `1102` on all interfaces (port 102, the real S7 port, is privileged on most systems) and simulates:

- DB1: Motor telemetry (REAL values, a DINT batch counter)
- DB2: Alarms and configuration bits, a status STRING
- Inputs: Sensors toggling periodically
- Outputs: Actuator commands (read/write)
- Markers: A system marker bit

Point the MCP server at it with `S7_HOST=127.0.0.1 S7_PORT=1102`. The full memory map and the values to expect are in [TEST_SCENARIOS.md](TEST_SCENARIOS.md).

CLI flags (or the matching environment variables) tweak the port, update interval and seeded values:

| Flag | Environment variable | Default |
|---|---|---|
| `--port` | `MOCK_S7_PORT` | `1102` |
| `--update-interval` | `MOCK_S7_UPDATE_INTERVAL` | `1.0` seconds |
| `--motor-speed` | `MOCK_S7_MOTOR_SPEED` | `1450.0` |
| `--temp-start` | `MOCK_S7_TEMP_START` | `68.0` |
| `--host` | `MOCK_S7_HOST` | `0.0.0.0` (accepted, but not currently applied: the server always binds all interfaces) |

Run `uv run s7comm-mock-server --help` for the full list.

## Status

The script currently seeds a deterministic memory map and periodically mutates select values to mimic live plant data. Extend `s7_mock_server.py` to add scenarios, random jitter, command handling, or to consume JSON fixtures for more complex integration tests.
