# S7comm protocol assessment and capability decisions

Reviewed 2026-10-25 against this repository, OPCUA-MCP's feature/execution design,
and the installed python-snap7 2.0.2 source. The dependency is intentionally held
below major version 3 because native bit access and CPU-status checking use the
2.x interface; current 3.x documentation is not treated as a 2.x API promise.

## Evidence and supported scope

[Siemens S7-1200 System Manual V4.7, December 2024, section 12.9](https://cache.industry.siemens.com/dl/files/302/109977302/att_1307432/v1/s71200_system_manual_en-US.pdf)
describes PUT/GET access prerequisites and standard versus optimized DB access.
The MCP exposes configured absolute memory access, not arbitrary optimized symbolic
access. It does not change PLC protection or project settings automatically.

[Siemens PUT/GET application example V3.0, November 2020](https://cache.industry.siemens.com/dl/files/115/82212115/att_1039292/v2/82212115_S7_communication_S7-1500_S7-1200_en.pdf)
provides block-layout context. Device family, firmware, permitted services and
layout remain deployment facts supplied by the integrator.

The native implementation reviewed is the installed `snap7/client.py` from the
locked python-snap7 2.0.2 distribution; its upstream
[source location](https://github.com/gijzelaerr/python-snap7/blob/2.0.2/snap7/client.py)
was verified through the GitHub API after browser rate limiting. The
[current official API page](https://python-snap7.readthedocs.io/en/latest/API/client.html)
was consulted only to identify version drift, not to justify upgrading. Native
calls and return-code handling are verified against the actual installed 2.x API.

## Capability and gap matrix

| Workflow / tools | Existing capability | Gap addressed / acceptance evidence | Remaining boundary |
|---|---|---|---|
| Inspect: `get_connection_status` | Offline connection/configuration report | Typed canonical-tool capability map, native gate state and prerequisites; `test_capabilities.py`, artifact smoke | Does not probe permissions or prove PLC reachability |
| Read: `read_values` | DB/input/output/marker, typed values, aliases and batches | Raw reads and STRING header/storage must match the requested length; `test_read_integrity.py` | No fake source quality/timestamp; multi-read results are not atomic |
| Metadata/diagnostics: `read_plc_info`, `read_cpu_state`, `read_system_time`, `read_szl`, `list_tags` | Selected native services and configured tag metadata | Existing codecs, native return-code checks and suites retained | PLC service availability varies; tag aliases are a local map |
| Write: `write_values` | Type/range checks, bit writes, complete batch prechecks and no replay | Session ownership fenced against late interruption; `test_session_ownership.py` and existing write-safety tests | Partial application remains possible; address layout must be supplied |
| CPU command: `set_cpu_state` | Requires write and system gates; polls observed CPU state | Native operation/cancellation ownership preserved and tested | A timeout may mean the state is still changing; no resend |
| S7comm Plus, optimized symbolic access, encryption, native subscriptions | Not implemented | Explicit capability limitations | Requires a different supported stack/feature design and authorized device tests |

## Architecture and migration

The existing native-free application/domain modules and injected client port stay
in place. The capability report is a pure domain function; it creates no session.
The status schema change is additive and existing tool names and aliases remain.
`enabled` reflects native configuration gates, with MCP policy and PLC permission
still checked separately. `simulated: false` means the native Snap7 backend was
selected, not proof that the peer is a physical PLC.

An ownership lock now fences native-client interruption and handoff between worker
calls. Reset/destruction uses the same fence. This closes the interval where an
old timed-out call could disconnect a later request reusing the same native client.
It does not add a control retry or claim that cancellation stopped a physical action.

## Verification boundary

Tests include scripted native API behavior, local Snap7 server/proxy integration,
reply loss/cancellation, MCP stdio, and installed wheel/npm artifacts. No physical
PLC was provided or changed. Hardware validation requires the target CPU/firmware,
block layouts, access configuration and an explicitly authorized test environment;
local Snap7 tests do not certify every Siemens CPU or security configuration.

All canonical tools now declare structural result shapes in `contract/tools.json`,
including typed outcome/completeness metadata and nullable failure data. Native
values retain explicit JSON value flexibility. CI validates live canonical MCP
responses against those schemas and checks text/structured result agreement;
legacy aliases retain their compatibility payload shapes.
