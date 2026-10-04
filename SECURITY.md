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

- **Writes are disabled by default.** `write_db`, `write_output`,
  `write_marker`, `write_multiple_vars` and `write_tag` refuse unless
  `S7_WRITES_ENABLED=true`. Enable it only for sessions that need to write.
  A value the server does not recognise as true or false stops it at startup.
- **CPU RUN/STOP needs both switches.** `set_cpu_state` refuses unless
  `S7_WRITES_ENABLED=true` and `S7_SYSTEM_CMDS_ENABLED=true`.
- **Inputs are read-only.** No tool writes the process-image inputs (I/PE).
- **A write reaches the PLC at most once.** Writes and RUN/STOP are never
  resent automatically. If the link fails after the request went out, the
  tool returns `success: false` with `meta.outcome: "unknown"` and says the
  write may have been applied: check the value before retrying.
- **There is no per-address allowlist.** With writes enabled, any DB, output or
  marker the PLC lets the connection write is writable by the model. Values are
  not range-checked beyond their data type. A BOOL write changes only its own
  bit, a STRING write requires the PLC's STRING header to match, and a batch
  write checks every entry on the PLC before writing any of them.
- **Whatever the PLC permits is what the server can do.** On S7-1200/1500,
  PUT/GET access and its protection level are configured in the PLC project;
  use them to limit what this connection can reach.

## Running it safely

- **Start against the mock.** `s7comm-mock-server` exists so an agent can be
  exercised end to end without touching real plant. Do that first, every time.
- **Keep it read-only until you mean otherwise.** Write tools move physical
  equipment. There is no undo, and a language model will call a tool it has
  been given.
- **Safety switches come only from you.** `S7_WRITES_ENABLED` and
  `S7_SYSTEM_CMDS_ENABLED` are honoured only from the process environment
  (e.g. the MCP client's `env` block) or a file passed with `--env-file`. The
  implicit `s7comm-python/.env` may set connection settings, but a value in it
  that would enable a switch is ignored with a warning on stderr (whatever the
  case of the name, with or without `export`). The implicit file may set only
  `S7_*` and `TAG_MAP_FILE`; any other key, such as `PATH`, `LD_PRELOAD` or
  `PYTHONPATH`, is ignored with a warning. The server never reads a `.env`
  from its working directory, which for a directly launched server is the
  MCP client's project and may not be trusted.
- **Do not expose the server beyond the host running the client.** It is a
  stdio process meant to run beside the MCP client, not a network service. The
  mock PLC, on the other hand, listens on all interfaces by default; start it
  with `--host 127.0.0.1` unless other machines need to reach it.
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
