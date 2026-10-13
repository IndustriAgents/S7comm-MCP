# S7comm Mock Server - Test Scenarios

This document lists test scenarios for checking the S7comm MCP server against the mock PLC, with the arguments each tool takes and the result to expect. Every tool returns the `{ success, data, error, meta }` envelope; the expected results below are the interesting part of `data` (or `error`).

The automated suite follows these scenarios: `s7comm-python/tests/test_mcp_stdio.py` starts the mock and the real server and drives it over stdio with the MCP client SDK, and `s7comm-python/tests/test_tools_mock.py` covers each tool in more detail. Run them with `cd s7comm-python && uv sync --extra dev && uv run pytest`.

| Scenario | Needs |
|---|---|
| 1-4, 6, 7.1, 8, 9, 10.1-10.2 | Read-only defaults |
| 5, 7.2, 10.3 | `S7_WRITES_ENABLED=true` |
| 10 | A tag map (`TAG_MAP_FILE`) |
| 11 | `S7_WRITES_ENABLED=true` and `S7_SYSTEM_CMDS_ENABLED=true` |

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

   The server is read-only by default: write tools return `success: false` with `Write operations are disabled. Set S7_WRITES_ENABLED=true ...`. Scenarios 5, 7.2 and 10.3 write to the PLC, so start the server with `S7_WRITES_ENABLED=true` for those steps. Writes change the mock's memory until it is restarted.

## Memory Map

### DB1 (Telemetry Data Block, 256 bytes)
- **Offset 0**: Motor Speed (REAL) - Updates with random variation around base speed
- **Offset 4**: Motor Torque (REAL) - Static value: 42.0
- **Offset 8**: Flow Rate (REAL) - Static value: 22.5
- **Offset 12**: Batch Counter (DINT) - Static value: 12345678
- **Offset 16**: Temperature (REAL) - Updates with random variation around base temp

### DB2 (Alarms & Configuration, 128 bytes)
- **Offset 0, Bit 0**: Alarm bit (BOOL) - Set to True
- **Offset 2**: Status string (S7 `STRING[30]`) - "OK". In memory this is a max-length byte (30), a length byte (2), then the text padded with spaces.

### DB3 (Bulk Area, 1024 bytes)
- All zero at start. Larger than one S7 PDU, so writes of more than 452 bytes go out as several requests; used to test multi-PDU writes.

### Inputs (PE, 8 bytes)
- **Byte 0, Bit 0**: Sensor 1 - Toggles periodically
- **Byte 0, Bit 1**: Sensor 2 - Toggles periodically

### Outputs (PA, 8 bytes)
- **Byte 0, Bit 0**: Pump command (BOOL) - Writable, initial: False

### Markers (M, 8 bytes)
- **Byte 0, Bit 0**: System marker - Set to True

---

## Test Scenario 1: Basic Connection & System Info

**Objective**: Verify connection to mock server and read system information.

### Steps:
1. **Read PLC information**
   ```json
   { "tool": "read_plc_info", "parameters": {} }
   ```
   Expected: The emulated CPU's identity: `"module_type": "CPU 315-2 PN/DP"`, `"as_name": "SNAP7-SERVER"`, `"order_code": "6ES7 315-2EH14-0AB0"` and `"version": "V3.2.6"`

2. **Check connection status**
   ```json
   { "tool": "get_connection_status", "parameters": {} }
   ```
   Expected: `"connected": true` with the host, port, rack and slot in use. (This tool does not contact the PLC. Before the first call that needs the PLC it reports `"connected": false`, because the server connects on first use.)

3. **Read CPU state**
   ```json
   { "tool": "read_cpu_state", "parameters": {} }
   ```
   Expected: `"state": "S7CpuStatusRun"`, `"mode": "RUN"`

4. **Read system time**
   ```json
   { "tool": "read_system_time", "parameters": {} }
   ```
   Expected: An ISO 8601 timestamp (the mock reports its host's clock, without a time zone)

5. **Ping test**
   ```json
   { "tool": "ping", "parameters": {} }
   ```
   Expected: `"reachable": true` and the CPU state, connection details, `writes_enabled`, `system_cmds_enabled` and tag map status

6. **Read an SZL list** (module identification, SZL ID 0x0011 = 17)
   ```json
   { "tool": "read_szl", "parameters": { "szl_id": 17, "szl_index": 0 } }
   ```
   Expected: `"record_length": 28`, `"record_count": 4` and four records as hex; the first contains the order code `6ES7 315-2EH14-0AB0`

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

2. **Read Status String** (STRING at DB2, offset 2)
   ```json
   { "tool": "read_db_typed", "parameters": { "db_number": 2, "start_offset": 2, "data_type": "STRING", "string_length": 30 } }
   ```
   Expected: `"value": "OK"`, `"length": 2`, `"max_length": 30`. `string_length` may be left out; the server then reads the max length from the header. A `string_length` that does not match the header (e.g. 10) is an error, not a guess.

---

## Test Scenario 4: Reading Inputs & Outputs

**Objective**: Read process inputs and outputs.

The I/O tools return whole bytes as hex; read individual bits from the returned byte, or use `read_multiple_vars` with `data_type: "BOOL"` (scenario 7).

### Steps:
1. **Read Inputs** (PE area, byte 0)
   ```json
   { "tool": "read_input", "parameters": { "start_byte": 0, "size": 1 } }
   ```
   Expected: One byte with bits 0 and 1 toggling

2. **Read Outputs** (PA area, byte 0)
   ```json
   { "tool": "read_output", "parameters": { "start_byte": 0, "size": 1 } }
   ```
   Expected: `"raw_bytes_hex": "00"` initially (bit 0, the pump command, False)

---

## Test Scenario 5: Writing Values (S7_WRITES_ENABLED=true)

**Objective**: Write values to a data block and to outputs and verify the changes.

### Steps:
1. **Write to DB1** (Update Flow Rate to 25.0)
   ```json
   { "tool": "write_db", "parameters": { "db_number": 1, "start_offset": 8, "value": 25.0, "data_type": "REAL" } }
   ```
   Expected: Success, `written_bytes: 4`, `meta.outcome: "written"`, `meta.request_sent: true`. With writes disabled (the default): `success: false`, the error names `S7_WRITES_ENABLED`, and `meta.outcome` is `"not_sent"`.

2. **Verify Flow Rate** (Read back)
   ```json
   { "tool": "read_db_typed", "parameters": { "db_number": 1, "start_offset": 8, "data_type": "REAL" } }
   ```
   Expected: `25.0` (the mock keeps it until restarted)

3. **Write Pump Command** (Set output bit 0 to True)
   ```json
   { "tool": "write_output", "parameters": { "start_byte": 0, "value": true, "data_type": "BOOL", "bit_index": 0 } }
   ```
   Expected: Success, `written_bits: 1`. Only Q0.0 changes; the other seven bits of the byte keep their values. A BOOL write without `bit_index`, or with a value that is not a JSON boolean, is refused, and so is a `bit_index` on any type other than BOOL.

4. **Verify Pump Command** (Read back)
   ```json
   { "tool": "read_output", "parameters": { "start_byte": 0, "size": 1 } }
   ```
   Expected: Bit 0 is set (`"01"` if the byte was `00`)

5. **Write the Status String**
   ```json
   { "tool": "write_db", "parameters": { "db_number": 2, "start_offset": 2, "value": "RUNNING", "data_type": "STRING", "string_length": 30 } }
   ```
   Expected: Success, `written_bytes: 9` (2 header bytes and 7 characters). Reading scenario 3.2 again returns `"RUNNING"`. Text longer than 30 characters, or a `string_length` other than 30, is refused and nothing is written.

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

5. **Read Input Bits** (monitor toggling)
   - Call `read_input` (`start_byte` 0, `size` 1) several times and watch bits 0 and 1
   - Expected: The bits toggle periodically

---

## Test Scenario 7: Batch Operations

**Objective**: Read and write multiple values in a single request.

Batch entries take `area` (`db`, `input`, `output`, `marker`; default `db`), `db_number` for data blocks, and the byte offset as `offset`, `start_offset`, `byte` or `start_byte`. An entry without an offset is rejected. `success` is true only when every entry succeeded; `data.results` holds one result per entry either way.

### Steps:
1. **Batch Read Multiple Variables**
   ```json
   {
     "tool": "read_multiple_vars",
     "parameters": {
       "variables": [
         { "area": "db", "db_number": 1, "offset": 0, "data_type": "REAL" },
         { "area": "db", "db_number": 1, "offset": 4, "data_type": "REAL" },
         { "area": "db", "db_number": 1, "start_offset": 8, "data_type": "REAL" },
         { "area": "db", "db_number": 1, "offset": 16, "data_type": "REAL" },
         { "area": "marker", "byte": 0, "bit": 0, "data_type": "BOOL" }
       ]
     }
   }
   ```
   Expected: Five results with `success: true`: Motor Speed (~1450), Torque (42.0), Flow (22.5, or the value written in scenario 5), Temperature (~68) and the system marker (`true`)

2. **Batch Write Multiple Variables** (S7_WRITES_ENABLED=true)
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
   Expected: Success for both writes (each entry has `outcome: "written"`); Flow Rate becomes 30.0 and Q0.0 is set without touching Q0.1-Q0.7. Every entry is checked before anything is written, locally and by reading its target from the PLC: if any entry is invalid (say a BYTE value of 300, DB 999, a mismatched STRING header or an `input` area), nothing is written. If the PLC still refuses a write, or its reply is lost, the entries after it are skipped and reported as such.

---

## Test Scenario 8: Reading Markers

**Objective**: Read marker memory area.

### Steps:
1. **Read Markers** (M area, byte 0)
   ```json
   { "tool": "read_marker", "parameters": { "start_byte": 0, "size": 1 } }
   ```
   Expected: `"raw_bytes_hex": "01"`, bit 0 (system marker) set

---

## Test Scenario 9: Error Handling

**Objective**: Verify proper error handling for invalid requests. Every step returns `success: false` with a message, and the session keeps working afterwards.

### Steps:
1. **Read with invalid data type**
   ```json
   { "tool": "read_db_typed", "parameters": { "db_number": 1, "start_offset": 0, "data_type": "INVALID_TYPE" } }
   ```
   Expected: `"Unsupported data_type 'INVALID_TYPE'. Supported: ..."`. The request never reaches the PLC.

2. **Read from non-existent DB**
   ```json
   { "tool": "read_db", "parameters": { "db_number": 999, "start_offset": 0, "size": 10 } }
   ```
   Expected: `"read_area(area=db,db=999,start=0,size=10) failed: CPU : Item not available"`. The PLC's answer is reported as is, without reconnecting or retrying.

3. **Read with invalid offset** (beyond DB size)
   ```json
   { "tool": "read_db", "parameters": { "db_number": 1, "start_offset": 1000, "size": 10 } }
   ```
   Expected: `"... failed: CPU : Address out of range"`

4. **Arguments of the wrong type**
   ```json
   { "tool": "read_db", "parameters": { "db_number": "DB1", "start_offset": 0, "size": 1 } }
   ```
   Expected: `"Invalid arguments: db_number: Input should be a valid integer, ..."`

5. **Read again** (scenario 2.2)
   Expected: `42.0`. Failed calls do not break the session.

---

## Test Scenario 10: Tag System

**Objective**: Test tag-based access with a tag map.

Without a tag map, `list_tags` returns an empty list and `read_tag` returns `Unknown tag 'MotorSpeed'`. To run this scenario, save the following as `tags.json` and start the server with `TAG_MAP_FILE` set to its absolute path. Tag entries use the same keys as batch entries. If `TAG_MAP_FILE` points at a missing or invalid file, the tag tools return an error saying so.

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

3. **Write Tag** (S7_WRITES_ENABLED=true)
   ```json
   { "tool": "write_tag", "parameters": { "name": "MotorSpeed", "value": 1500.0 } }
   ```
   Expected: Success, `written_bytes: 4`. The mock rewrites Motor Speed on every update, so a read-back shows 1500.0 only until the next update.

---

## Test Scenario 11: CPU Control (S7_WRITES_ENABLED=true and S7_SYSTEM_CMDS_ENABLED=true)

**Objective**: Stop and restart the emulated CPU.

### Steps:
1. **Stop the CPU**
   ```json
   { "tool": "set_cpu_state", "parameters": { "state": "STOP" } }
   ```
   Expected: `"mode": "STOP"`, `meta.outcome: "written"`. The tool succeeds only once the PLC reports the requested mode. Unless both `S7_WRITES_ENABLED=true` and `S7_SYSTEM_CMDS_ENABLED=true` are set, it refuses (`meta.outcome: "not_sent"`).

2. **Start it again**
   ```json
   { "tool": "set_cpu_state", "parameters": { "state": "RUN" } }
   ```
   Expected: `"mode": "RUN"`

---

## Expected Behavior Summary

- **Static Values**: Motor Torque (42.0), Flow Rate (22.5), Batch Counter (12345678) remain constant unless written
- **Dynamic Values**: Motor Speed and Temperature update every 1 second with random variation
- **Toggling Bits**: Input bits 0 and 1 toggle periodically
- **Writable Areas**: DB, output and marker values can be written and read back once writes are enabled; BOOL writes change one bit
- **System Info**: CPU state is `S7CpuStatusRun`, firmware `V3.2.6`, system time is the mock host's clock

## Notes

- The mock server updates values every 1 second by default (configurable via `MOCK_S7_UPDATE_INTERVAL`)
- Motor speed varies around the base value (default 1450.0) with ±10 range
- Temperature varies around the base value (default 68.0) with ±2.5 range
- Input bits toggle based on a phase counter that increments every update cycle
- The mock rewrites DB1 offsets 0 and 16, input bits 0-1 and marker bit M0.0 on every update, so writes there do not stick
