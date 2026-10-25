# Architecture and migration (0.3.0)

This repository is an independent protocol MCP server. It follows OPCUA-MCP's
instance-owned composition, explicit MCP contract and application/adapter split.
It has no runtime dependency on another IndustriAgents protocol repository or
on IndustriConnect-MCPs. The umbrella repository can pin this repository as a
submodule and launch its released command.

## Execution path

```mermaid
flowchart LR
  Client[MCP client] -->|stdio| Contract[Protocol contract and dispatcher]
  Contract --> Execution[Instance policy, admission and audit]
  Execution --> Application[Protocol application operations]
  Application --> Adapter[Native protocol adapter]
  Adapter --> Device[Configured endpoint or bus]
```

`server.py` composes one server instance and its resources. `protocol/` owns
registration, schema dispatch, SDK request context access and wire errors.
`application/` owns reads, writes, discovery, diagnostics and metadata operations.
`domain/` holds protocol types, error/outcome vocabulary, structural ports and
policy values. `adapters/` contains the native driver and owns its connection,
retry eligibility, send tracking and cancellation fences. `infrastructure/`
contains audit and execution-context support. Compatibility modules at the old
Python import paths forward to the implementation rather than owning another
connection. Some established config/codec/metadata helpers keep their root
module paths to avoid unnecessary public import breaks.

The supported deployment is local stdio with one caller context and one
configured endpoint/bus per process. Instance ownership is not a claim of
multi-tenant authorization or remote HTTP transport support. Modbus and MQTT
provide `create_server(...)` with explicit per-instance configuration; the
other protocols keep their existing server constructors with injected clients
or masters. The CLI default instance exists only for compatibility.

## Tool contract

`contract/tools.json` is the reviewed contract, including JSON schemas, routing,
legacy names and the result envelope. Its identical packaged resource is
`s7comm-python/src/s7comm_mcp/_contract/tools.json`.
Run `python scripts/verify_contract.py` to reject drift. There is no second
TypeScript implementation: npm carries the same wheel as the PyPI release.

| Advertised tool | Modes | Effect |
|---|---|---|
| `read_values` | `db`, `db_typed`, `input`, `output`, `marker`, `tag`, `batch` | Read/local |
| `write_values` | `db`, `output`, `marker`, `tag`, `batch` | Changes state or consumes events |
| `read_plc_info` | No mode selector | Read/local |
| `read_cpu_state` | No mode selector | Read/local |
| `set_cpu_state` | No mode selector | Changes state or consumes events |
| `read_system_time` | No mode selector | Read/local |
| `read_szl` | No mode selector | Read/local |
| `list_tags` | No mode selector | Read/local |
| `get_connection_status` | No mode selector | Read/local |

A mode selects one protocol operation. Parameters from different modes cannot
be mixed; unknown parameters fail before dispatch. Alias/tag modes resolve
through the existing configured map and preserve its scaling and validation.
Legacy names remain callable but are omitted from `tools/list`. Names retained
in both catalogs accept the old address form without the new selector for
migration. Those legacy forms retain native argument validation and coercion.

| Legacy name | Advertised replacement |
|---|---|
| `read_db` | `read_values` |
| `read_db_typed` | `read_values` |
| `read_input` | `read_values` |
| `read_output` | `read_values` |
| `read_marker` | `read_values` |
| `read_tag` | `read_values` |
| `read_multiple_vars` | `read_values` |
| `write_db` | `write_values` |
| `write_output` | `write_values` |
| `write_marker` | `write_values` |
| `write_tag` | `write_values` |
| `write_multiple_vars` | `write_values` |
| `ping` | `read_cpu_state` |

Every tool returns `{success, data, error, meta}`. On the wire, `success:false`
sets MCP `isError:true`; structuredContent and the JSON text describe the same
result. Writes preserve `meta.outcome` and `meta.request_sent` from the native
adapter. A timeout/cancellation after possible dispatch is uncertain, never
converted to not_sent. There is no new generic retry loop. In particular,
retrying an unknown control outcome can apply a physical action twice.

## Deployment policy and audit

Set `S7COMM_POLICY_FILE` to an explicit JSON policy file. Unknown fields
and invalid types fail at server construction. Without a policy file the native
protocol write gates continue to apply; writes are disabled by default.

```json
{
  "read_only": true,
  "allowed_tools": null,
  "allowed_operations": null,
  "max_in_flight": 64,
  "max_request_bytes": 1048576,
  "audit_path": null
}
```

`allowed_tools` restricts canonical tool names; `allowed_operations` restricts
the concrete handler names in the contract's routes. Null means unrestricted;
an empty array denies everything. Legacy names map to the same canonical
policy, so they cannot bypass it. `read_only` also denies non-read-only
operations such as scans or event-consuming polls. This is tool/operation
policy, not resolved-address or engineering-value authorization. Existing
protocol maps, value limits and write/system gates remain authoritative for
those checks. No new OPC UA-style namespace or physical interlock semantics
are implied for these different protocols.

Admission rejects excess calls immediately, with one additional slot reserved
for a local read-only diagnostic. It does not create an unbounded wait queue.
The native adapter still owns ordering and pending native I/O. Cancellation
releases the MCP admission slot; a cancelled worker thread is not assumed to
have stopped, and native send fences remain responsible for that case.

When `audit_path` is set, a write intent is appended and fsynced before dispatch.
Failure to persist that intent refuses dispatch. The final observed outcome is
appended with the same call ID; a missing result after process failure means
unknown, not success. Failure to persist a result is logged and marked
`meta.audit_error`, without resending the operation or changing its outcome.
Audit records omit payloads and credentials. Manage file retention externally;
this append-only log is not a tamper-proof external audit service.

Policy documents are read only from regular files, with a 64 KiB read bound and
at most 16 JSON nesting levels. Duplicate fields are rejected. The size bound
still applies if a file grows during the read. Audit destinations must also be
regular files; devices and pipes are refused before any audit record is written.
A FIFO cannot block policy loading or audit admission while waiting for a peer.

## Protocol capability limits

Live S7 communication uses python-snap7. Absolute DB access requires suitable PLC access settings and non-optimized blocks where applicable. CPU state changes require both write and system-command gates. Multi-variable writes may partially apply and are not transactions.

## Packages

```sh
uvx --from industriagents-s7comm-mcp==0.3.0 s7comm-mcp
npx -y industriagents-s7comm-mcp@0.3.0
```

The distribution is deliberately prefixed: several generic protocol names on
PyPI belong to other projects. Python import names and command names remain
`s7comm_mcp` and `s7comm-mcp`. npm requires Node 20+ and uv; uv provisions a
compatible Python on first run. npm bundles a version-checked wheel and passes
stdio, signals, environment and CLI arguments through to it. Network access is
needed on first use to resolve Python dependencies. Native drivers/permissions
are still required for live operation. The mock backend is not selected merely
because a native dependency or device is unavailable.

## Verification and release

The regression suite retains native-send counting, reply-loss, cancellation,
configuration-gate and stdio checks. New architecture tests exercise all
consolidated routes, unknown/mixed arguments, legacy-policy bypass, independent
admission state, durable intent failure and MCP error envelopes. npm tests
check the bundled version, argument boundaries, exit status and missing-uv
errors. See [release.md](release.md) for the registry workflow.

## Request and failure bounds

Every call is bounded before selector forwarding, JSON encoding, schema
validation, policy admission or device I/O. Contract limits are 32 nesting
levels, 10,000 entries per container and 50,000 total values; the deployment
policy additionally bounds serialized request bytes. Non-finite numbers,
non-JSON values, invalid Unicode and non-string object keys are rejected.
These checks bound work after the MCP SDK has decoded the transport frame.

Consolidated selector modes validate their complete contract schemas. Legacy
names and direct routes retain their established native validation and coercion,
including their error details; unknown top-level keys are always refused.
MCP input-model validation runs before the handler. A validation exception
raised inside a handler is an execution failure and cannot prove a control was
not sent. Unexpected execution failures preserve an uncertain outcome; native
send tracking remains authoritative when available. Neither case adds retries.

An audit-result failure adds `meta.audit_error` to the same envelope used for
both text content and structured content, including text-only native results.
Cancellation releases admission capacity and retains an unknown audit outcome
when no result was observed.

### S7 control outcomes

Native failures after dispatch are unknown unless a recognized Snap7 single-PDU
refusal establishes rejection. An unexpected Python exception after sending
cannot be treated as a pre-send failure. Application preparation errors remain
`not_sent`; unclassified failures from a control port are `unknown` with
`request_sent: null`. Batch results retain this uncertainty and stop after the
first failure. If earlier entries succeeded, the aggregate outcome is unknown
even when the failing entry was not sent; each entry retains its own outcome. CPU command spelling remains case-insensitive for compatibility.

## Complete batch prechecks

Before the first batch write, every target read must return the entire
requested range. A truncated raw, BOOL or STRING preflight read refuses the
whole batch with `not_sent`, even if a valid STRING header was present. Empty
or non-list legacy batches also report `request_sent:false` explicitly. These
checks establish that no batch write began; they do not make multi-variable
writes transactional or replace the existing partial-application reporting.

## Protocol assessment and capability reporting

See [protocol-assessment.md](protocol-assessment.md) for authoritative sources,
the capability/gap matrix, implementation decisions and hardware test boundaries.
`get_connection_status` returns typed `data.capabilities` without device I/O.
`scope: backend_support` and `device_verified: false` distinguish implemented
software from verified device support. `enabled` reports native configuration
gates only; policy and device permissions still apply.

Native interruption, ownership handoff and client destruction share an ownership
fence. Raw and STRING reads require the complete requested range before success.

Edit named `resultShapes` in `contract/tools.json`, run `python3 scripts/sync_contract.py` to regenerate advertised data schemas and the packaged resource, then run `python3 scripts/verify_contract.py` to check drift.
