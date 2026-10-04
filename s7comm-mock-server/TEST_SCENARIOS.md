# S7comm Mock Server - Test Scenarios

This document lists test scenarios for checking the S7comm MCP server against the mock PLC, with the arguments each tool takes and the result to expect.

> **Not every scenario passes today.** The expected results below were checked against the server and the mock in this repository with the locked python-snap7 2.0.2. Steps marked **Known failure** describe what the server should do once the bug is fixed, followed by what it returns today; the causes are listed under [Known limitations](../README.md#known-limitations) in the main README.
>
> **A failed S7 operation breaks the session.** After any call whose S7 request fails — the known failures in scenarios 4, 5, 6, 7 and 8, or the PLC errors in scenario 9 — the server cannot reconnect and every later call fails with `Failed to connect` until the server is restarted. Run those steps last, or restart the server (or the MCP Inspector session) after each one.

| Scenario | Status today |
|---|---|
| 1. Basic connection & system info | Works |
| 2. Reading telemetry data | Works |
| 3. Reading alarms & status | 3.1 works; 3.2 **known failure** (STRING header bytes and padding returned) |
| 4. Reading inputs & outputs | **Known failure** |
| 5. Writing values | 5.1–5.2 work; 5.3–5.4 **known failure** |
| 6. Monitoring dynamic values | 6.1–6.4 work; 6.5 **known failure** |
| 7. Batch operations | 7.1 works; 7.2 **known failure** for the output entry (the DB entry is written) |
| 8. Reading markers | **Known failure** |
| 9. Error handling | Errors are returned; 9.2 and 9.3 also break the session |
| 10. Tag system | Works with a tag map |

## Prerequisites

1. Start the mock server:
   ```bash
   cd s7comm-mock-server
   uv sync
   uv run s7comm-mock-server
   ```

2. Configure the MCP client to connect to the mock server:
   ```bash
   export S7_HOST=127.0.0.1
   export S7_PORT=1102
   export S7_RACK=0
   export S7_SLOT=2
   ```

   Scenarios 5, 7.2 and 10.3 write to the PLC. Write tools are refused when `S7_WRITES_ENABLED=false` (as in the README's client config and Inspector command); leave it unset or set it to `true` for those steps. Writes change the mock's memory until it is restarted.

## Memory Map

### DB1 (Telemetry Data Block)
- **Offset 0**: Motor Speed (REAL) - Updates with random variation around base speed
- **Offset 4**: Motor Torque (REAL) - Static value: 42.0
- **Offset 8**: Flow Rate (REAL) - Static value: 22.5
- **Offset 12**: Batch Counter (DINT) - Static value: 12345678
- **Offset 16**: Temperature (REAL) - Updates with random variation around base temp

### DB2 (Alarms & Configuration)
- **Offset 0, Bit 0**: Alarm bit (BOOL) - Set to True
- **Offset 2**: Status string (S7 `STRING[30]`) - "OK". In memory this is a max-length byte (30), a length byte (2), then the text padded with spaces.

### Inputs (PE)
- **Byte 0, Bit 0**: Sensor 1 - Toggles periodically
- **Byte 0, Bit 1**: Sensor 2 - Toggles periodically

### Outputs (PA)
- **Byte 0, Bit 0**: Pump command (BOOL) - Writable, initial: False

### Markers (M)
- **Byte 0, Bit 0**: System marker - Set to True

The mock serves inputs, outputs and markers, but the server cannot read or write them yet (see scenario 4).

---

## Test Scenario 1: Basic Connection & System Info

**Objective**: Verify connection to mock server and read system information.

### Steps:
1. **Check connection status**
   ```json
   { "tool": "get_connection_status", "parameters": {} }
   ```
   Expected: `"connected": true` with the host, port, rack and slot in use

2. **Read PLC information**
   ```json
   { "tool": "read_plc_info", "parameters": {} }
   ```
   Expected: The Snap7 server's identity, e.g. `"module_type": "CPU 315-2 PN/DP"`, `"as_name": "SNAP7-SERVER"` and an order code

3. **Read CPU state**
   ```json
   { "tool": "read_cpu_state", "parameters": {} }
   ```
   Expected: `S7CpuStatusRun`

4. **Read system time**
   ```json
   { "tool": "read_system_time", "parameters": {} }
   ```
   Expected: An ISO 8601 timestamp (the mock reports its host's clock)

5. **Ping test**
   ```json
   { "tool": "ping", "parameters": {} }
   ```
   Expected: Success with the connection details, `writes_enabled`, `system_cmds_enabled` and `tag_count`

---

## Test Scenario 2: Reading Telemetry Data

**Objective**: Read all telemetry values from DB1.

### Steps:
1. **Read Motor Speed** (REAL at DB1, offset 0)
   ```json
   { "tool": "read_db_typed", "parameters": { "db_number": 1, "start_offset": 0, "data_type": "REAL" } }
   ```
   Expected: Value around 1450.0 (varies ±10 due to random updates)

2. **Read Motor Torque** (REAL at DB1, offset 4)
   ```json
   { "tool": "read_db_typed", "parameters": { "db_number": 1, "start_offset": 4, "data_type": "REAL" } }
   ```
   Expected: `42.0`

3. **Read Flow Rate** (REAL at DB1, offset 8)
   ```json
   { "tool": "read_db_typed", "parameters": { "db_number": 1, "start_offset": 8, "data_type": "REAL" } }
   ```
   Expected: `22.5`

4. **Read Batch Counter** (DINT at DB1, offset 12)
   ```json
   { "tool": "read_db_typed", "parameters": { "db_number": 1, "start_offset": 12, "data_type": "DINT" } }
   ```
   Expected: `12345678`

5. **Read Temperature** (REAL at DB1, offset 16)
   ```json
   { "tool": "read_db_typed", "parameters": { "db_number": 1, "start_offset": 16, "data_type": "REAL" } }
   ```
   Expected: Value around 68.0 (varies ±2.5 due to random updates)

6. **Read raw bytes** (DB1, first 20 bytes)
   ```json
   { "tool": "read_db", "parameters": { "db_number": 1, "start_offset": 0, "size": 20 } }
   ```
   Expected: `byte_count: 20` and hex bytes containing all the above values (e.g. `42280000` for 42.0 at bytes 4–7, `00bc614e` for 12345678 at bytes 12–15)

---

## Test Scenario 3: Reading Alarms & Status

**Objective**: Read alarm bits and status strings from DB2.

### Steps:
1. **Read Alarm Bit** (BOOL at DB2, byte 0, bit 0)
   ```json
   { "tool": "read_db_typed", "parameters": { "db_number": 2, "start_offset": 0, "data_type": "BOOL", "bit_index": 0 } }
   ```
   Expected: `true`

2. **Read Status String** (STRING at DB2, offset 2) — **Known failure**
   ```json
   { "tool": "read_db_typed", "parameters": { "db_number": 2, "start_offset": 2, "data_type": "STRING", "string_length": 30 } }
   ```
   Expected: `"OK"`

   Today: `"\u001e\u0002OK"` followed by 26 spaces. The server returns the S7 string header bytes and padding along with the text. This does not break the session.

---

## Test Scenario 4: Reading Inputs & Outputs — Known failure

**Objective**: Read process inputs and outputs.

Today every step returns `success: false` and breaks the session (see the note at the top). With the default `S7_MAX_RETRIES` the error reads `read_area(...) failed after 4 attempts: Failed to connect ...`; with `S7_MAX_RETRIES=0` it shows the cause, python-snap7 2.x rejecting the integer area code.

The I/O tools return whole bytes as hex; read individual bits from the returned byte. (`read_db_typed` only reads data blocks: it requires `db_number` and `start_offset` and has no `area`, `byte` or `bit` arguments.)

### Steps:
1. **Read Inputs** (PE area, byte 0)
   ```json
   { "tool": "read_input", "parameters": { "start_byte": 0, "size": 1 } }
   ```
   Expected once fixed: Byte value with bits 0 and 1 toggling

2. **Read Outputs** (PA area, byte 0)
   ```json
   { "tool": "read_output", "parameters": { "start_byte": 0, "size": 1 } }
   ```
   Expected once fixed: Byte value with bit 0 (pump command) False initially

---

## Test Scenario 5: Writing Values

**Objective**: Write values to a data block and to outputs and verify the changes.

Run 5.1 and 5.2 first: 5.3 fails today and breaks the session.

### Steps:
1. **Write to DB1** (Update Flow Rate to 25.0)
   ```json
   { "tool": "write_db", "parameters": { "db_number": 1, "start_offset": 8, "value": 25.0, "data_type": "REAL" } }
   ```
   Expected: Success, `written_bytes: 4`

2. **Verify Flow Rate** (Read back)
   ```json
   { "tool": "read_db_typed", "parameters": { "db_number": 1, "start_offset": 8, "data_type": "REAL" } }
   ```
   Expected: `25.0` (the mock keeps it until restarted)

3. **Write Pump Command** (Set output bit 0 to True) — **Known failure**
   ```json
   { "tool": "write_output", "parameters": { "start_byte": 0, "value": true, "data_type": "BOOL", "bit_index": 0 } }
   ```
   Expected once fixed: Success. Note that a `BOOL` write writes the whole byte, clearing the other bits in it.

   Today: `success: false` (same cause as scenario 4), and the session is broken afterwards.

4. **Verify Pump Command** (Read back) — **Known failure**
   ```json
   { "tool": "read_output", "parameters": { "start_byte": 0, "size": 1 } }
   ```
   Expected once fixed: Bit 0 is True

---

## Test Scenario 6: Monitoring Dynamic Values

**Objective**: Observe values that change over time.

### Steps:
1. **Read Motor Speed** (initial)
   ```json
   { "tool": "read_db_typed", "parameters": { "db_number": 1, "start_offset": 0, "data_type": "REAL" } }
   ```
   Note the value

2. **Wait 2-3 seconds** (mock server updates every 1 second by default)

3. **Read Motor Speed** (again)
   ```json
   { "tool": "read_db_typed", "parameters": { "db_number": 1, "start_offset": 0, "data_type": "REAL" } }
   ```
   Expected: Value should differ from step 1 (within ±10 range)

4. **Read Temperature** (multiple times)
   - Read temperature 3-4 times with 1-2 second intervals
   - Expected: Values vary around 68.0 ± 2.5

5. **Read Input Bits** (monitor toggling) — **Known failure**
   - Call `read_input` (`start_byte` 0, `size` 1) several times and watch bits 0 and 1
   - Expected once fixed: The bits toggle periodically. Today the call fails as in scenario 4.

---

## Test Scenario 7: Batch Operations

**Objective**: Read and write multiple values in a single request.

Batch entries address data blocks with `offset` and inputs/outputs/markers with `byte` (or `start_byte`). A `start_offset` key in a batch entry is silently ignored and the entry reads or writes offset 0.

### Steps:
1. **Batch Read Multiple Variables**
   ```json
   {
     "tool": "read_multiple_vars",
     "parameters": {
       "variables": [
         { "area": "db", "db_number": 1, "offset": 0, "data_type": "REAL" },
         { "area": "db", "db_number": 1, "offset": 4, "data_type": "REAL" },
         { "area": "db", "db_number": 1, "offset": 8, "data_type": "REAL" },
         { "area": "db", "db_number": 1, "offset": 16, "data_type": "REAL" }
       ]
     }
   }
   ```
   Expected: Four results with `success: true`: Motor Speed (~1450), Torque (42.0), Flow (22.5, or the value written in scenario 5) and Temperature (~68)

2. **Batch Write Multiple Variables** — **Known failure** for the output entry
   ```json
   {
     "tool": "write_multiple_vars",
     "parameters": {
       "variables": [
         { "area": "db", "db_number": 1, "offset": 8, "value": 30.0, "data_type": "REAL" },
         { "area": "output", "start_byte": 0, "value": true, "data_type": "BOOL", "bit_index": 0 }
       ]
     }
   }
   ```
   Expected once fixed: Success for both writes

   Today: The DB entry is written (`success: true`, Flow Rate becomes 30.0); the output entry returns `success: false` (same cause as scenario 4) and the session is broken afterwards. Drop the output entry to run this step without breaking the session.

---

## Test Scenario 8: Reading Markers — Known failure

**Objective**: Read marker memory area.

### Steps:
1. **Read Markers** (M area, byte 0)
   ```json
   { "tool": "read_marker", "parameters": { "start_byte": 0, "size": 1 } }
   ```
   Expected once fixed: Byte value with bit 0 (system marker) set to True

   Today: `success: false` (same cause as scenario 4), and the session is broken afterwards.

---

## Test Scenario 9: Error Handling

**Objective**: Verify proper error handling for invalid requests.

Run 9.1 first. 9.2 and 9.3 return the expected error, but the session is broken afterwards, so restart the server between them.

### Steps:
1. **Read with invalid data type**
   ```json
   { "tool": "read_db_typed", "parameters": { "db_number": 1, "start_offset": 0, "data_type": "INVALID_TYPE" } }
   ```
   Expected: `success: false`, `"Unsupported data_type 'INVALID_TYPE'"`. The request never reaches the PLC, so the session is unaffected.

2. **Read from non-existent DB**
   ```json
   { "tool": "read_db", "parameters": { "db_number": 999, "start_offset": 0, "size": 10 } }
   ```
   Expected: `success: false`. With the default `S7_MAX_RETRIES` the error reads `db_read(db=999,...) failed after 4 attempts: Failed to connect ...`; with `S7_MAX_RETRIES=0` it reads `... failed after 1 attempts: b'CPU : Item not available'`.

3. **Read with invalid offset** (beyond DB size)
   ```json
   { "tool": "read_db", "parameters": { "db_number": 1, "start_offset": 1000, "size": 10 } }
   ```
   Expected: `success: false`. With `S7_MAX_RETRIES=0` the error reads `... b'CPU : Address out of range'`; otherwise `Failed to connect`, as in 9.2.

---

## Test Scenario 10: Tag System

**Objective**: Test tag-based access with a tag map.

Without a tag map, `list_tags` returns an empty list and `read_tag` returns `Unknown tag 'MotorSpeed'`. To run this scenario, save the following as `tags.json` and start the server with `TAG_MAP_FILE` pointing at it. Tag entries use the same keys as batch entries (`offset` for data blocks, `byte` for I/O).

```json
{
  "MotorSpeed": { "area": "db", "db_number": 1, "offset": 0, "data_type": "REAL", "description": "Motor speed" },
  "AlarmActive": { "area": "db", "db_number": 2, "offset": 0, "data_type": "BOOL", "bit": 0 }
}
```

### Steps:
1. **List Tags**
   ```json
   { "tool": "list_tags", "parameters": {} }
   ```
   Expected: Returns the two tags with their area, description and address

2. **Read Tag**
   ```json
   { "tool": "read_tag", "parameters": { "name": "MotorSpeed" } }
   ```
   Expected: Value around 1450.0 (DB1 offset 0)

3. **Write Tag**
   ```json
   { "tool": "write_tag", "parameters": { "name": "MotorSpeed", "value": 1500.0 } }
   ```
   Expected: Success, `written_bytes: 4`. The mock rewrites Motor Speed on every update, so a read-back shows 1500.0 only until the next update.

---

## Expected Behavior Summary

- **Static Values**: Motor Torque (42.0), Flow Rate (22.5), Batch Counter (12345678) remain constant unless written
- **Dynamic Values**: Motor Speed and Temperature update every 1 second with random variation
- **Toggling Bits**: Input bits 0 and 1 toggle periodically (not readable through the server yet)
- **Writable Areas**: DB values can be written and read back; output writes fail today
- **System Info**: CPU state is `S7CpuStatusRun`, system time is the mock host's clock

## Notes

- The mock server updates values every 1 second by default (configurable via `MOCK_S7_UPDATE_INTERVAL`)
- Motor speed varies around the base value (default 1450.0) with ±10 range
- Temperature varies around the base value (default 68.0) with ±2.5 range
- Input bits toggle based on a phase counter that increments every update cycle
