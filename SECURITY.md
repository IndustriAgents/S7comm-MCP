# Security Policy

## What this server is, in security terms

The S7comm MCP server is a bridge between a language model and a Siemens PLC.
It speaks MCP over stdio to its client and S7comm (ISO-on-TCP, port 102) to the
PLC, which means the trust boundary sits *inside* the server: whatever the
model decides to call, the PLC is asked to do.

S7comm was designed for an isolated control network and carries no
authentication or encryption of its own. Anyone who can reach the PLC's port
102 can read and write its memory. The server adds no transport security on
top, so treat it as equipment on the control network, not as an application on
the office network.

## What the server gates, and what it does not

- **Writes are enabled by default.** `write_db`, `write_output`,
  `write_marker`, `write_multiple_vars` and `write_tag` are refused only when
  `S7_WRITES_ENABLED=false`. Set that for any session that only needs to read.
- **CPU RUN/STOP is disabled by default.** `set_cpu_state` is refused unless
  `S7_SYSTEM_CMDS_ENABLED=true`.
- **There is no per-address allowlist.** With writes enabled, any DB, output or
  marker the PLC lets the connection write is writable by the model. Values are
  not range-checked beyond their data type.
- **Whatever the PLC permits is what the server can do.** On S7-1200/1500,
  PUT/GET access and its protection level are configured in the PLC project;
  use them to limit what this connection can reach.

## Running it safely

- **Start against the mock.** `s7comm-mock-server` exists so an agent can be
  exercised end to end without touching real plant. Do that first, every time.
- **Keep it read-only until you mean otherwise.** Write tools move physical
  equipment. There is no undo, and a language model will call a tool it has
  been given.
- **Do not expose the server beyond the host running the client.** It is a
  stdio process meant to run beside the MCP client, not a network service. The
  mock PLC, on the other hand, listens on all interfaces; run it on a trusted
  host.
- **Segment the network.** The server should sit where a PLC engineering
  workstation would sit, behind whatever separates your control network from
  everything else.
- **Never point it at a safety system.** Safety-instrumented functions and
  F-CPUs' safety programs are out of scope for anything in this repository.

## Reporting a vulnerability

Please report privately, through
[GitHub private vulnerability reporting](https://github.com/IndustriAgents/S7comm-MCP/security/advisories/new),
or by email to hi@industriagents.com.

Please do not open a public issue for a vulnerability.

Include the component affected (MCP server or mock PLC), the version or commit,
what an attacker would gain, and a reproduction if you have one. We will
acknowledge within a week and keep you updated as we work on a fix.

## Scope

In scope: the MCP server (`s7comm-python`) and the mock PLC
(`s7comm-mock-server`) in this repository. This server is also shipped inside
the [IndustriConnect](https://github.com/IndustriAgents/IndustriConnect) suite
as the `S7comm-Project` submodule; report S7comm issues here either way.

Out of scope: vulnerabilities in the S7comm protocol itself (the lack of
authentication is a property of S7comm), in third-party libraries such as Snap7
and python-snap7 — report those upstream — and in Siemens PLC firmware.
