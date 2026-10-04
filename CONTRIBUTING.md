# Contributing to S7comm MCP

Thanks for taking an interest. This repository is the S7comm MCP server — a
bridge between MCP clients and Siemens S7 PLCs, built on python-snap7 — plus a
mock PLC so nothing has to be rehearsed on live plant.

It is also one of the ten protocol servers of the
[IndustriConnect](https://github.com/IndustriAgents/IndustriConnect) suite,
which pins it as the `S7comm-Project` git submodule. **Changes land here, in
IndustriAgents/S7comm-MCP**, not in IndustriConnect. Inside the suite,
`S7comm-Project/` is only a pointer to a commit of this repository; a push to
`main` here notifies IndustriConnect, whose sync workflow then opens a pull
request that moves the pointer forward.

## Repository layout

```text
S7comm-MCP/
├── s7comm-python/          # the MCP server: pyproject.toml + src/s7comm_mcp/ + tests/ (uv project)
├── s7comm-mock-server/     # a simulated S7 PLC on python-snap7 (uv project)
└── README.md
```

Both are independent [`uv`](https://docs.astral.sh/uv/) projects with their own
`pyproject.toml` and committed `uv.lock`. Work inside the one you are changing.

## What holds the suite together

The value of IndustriConnect is that all ten servers behave the same way, so a
change that breaks one of these needs a reason in the pull request:

1. **One response envelope.** Every tool returns `{ success, data, error, meta }`.
   A client that can read one server's output can read all ten.
2. **Read-only by default.** Tools that change PLC memory, outputs or CPU state
   move physical equipment. They are separated from the read tools, and a
   server should not expose them unless it has been configured to. Here the
   write tools refuse unless `S7_WRITES_ENABLED=true`, and `set_cpu_state`
   refuses unless `S7_SYSTEM_CMDS_ENABLED=true`; both default to false. New
   write paths must honour the same switches.
3. **The mock can answer it.** If you add a tool, the mock PLC has to be able
   to serve it, or nobody can test it without a plant.

## Getting set up

You need Python 3.11+ and `uv`. The python-snap7 wheels bundle the Snap7 native
library for macOS, glibc Linux (x86_64, aarch64) and Windows (x64), so there
is nothing else to install on those platforms.

Start the mock first — it is the thing the server talks to:

```bash
cd s7comm-mock-server
uv sync
uv run s7comm-mock-server            # listens on port 1102; --port to change it
```

Then, in a second terminal, run the server against it:

```bash
cd s7comm-python
uv sync
S7_HOST=127.0.0.1 S7_PORT=1102 uv run s7comm-mcp
```

The server speaks MCP over stdio, so on its own it just waits for a client. It
starts without a reachable PLC and connects on the first tool call that needs
it.

## Testing a change

The server has a pytest suite in `s7comm-python/tests`: unit tests for the
encoding, validation and client logic, tests of every tool against the mock
PLC, and an end-to-end test that starts the mock and the real `s7comm-mcp`
process and drives it over stdio with the MCP client SDK, following
[`TEST_SCENARIOS.md`](s7comm-mock-server/TEST_SCENARIOS.md). The tests start
and stop the mock themselves on a free loopback port. Ruff lints and formats
both projects, using the version locked by the server. Before opening a pull
request:

```bash
cd s7comm-python
uv sync --locked --extra dev
uv run --no-sync ruff check . ../s7comm-mock-server
uv run --no-sync ruff format --check . ../s7comm-mock-server
uv run --no-sync pytest
(cd ../s7comm-mock-server && uv sync --locked && uv run --no-sync s7comm-mock-server --help)
```

The full list of steps is in [`.github/workflows/ci.yml`](.github/workflows/ci.yml).
A change to a tool comes with a test that exercises it against the mock.

Then drive the server the way a user would — through the
[MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector) or a real
client — against the mock:

```bash
npx @modelcontextprotocol/inspector -e S7_HOST=127.0.0.1 -e S7_PORT=1102 \
  uv --directory s7comm-python run s7comm-mcp
```

Add `-e S7_WRITES_ENABLED=true` when the change you are testing is a write
tool. Call the tool you changed and check the envelope, not just the value.
[`s7comm-mock-server/TEST_SCENARIOS.md`](s7comm-mock-server/TEST_SCENARIOS.md)
lists the mock's memory map and the values to expect.

**Do not test against production equipment.** If a change genuinely cannot be
verified against the mock, say so in the pull request and describe the bench
you used (CPU, firmware, PUT/GET settings).

## House rules for the code

- MCP speaks over **stdio**, so `stdout` belongs to the protocol. All logging
  goes to `stderr`. A stray `print()` corrupts the session.
- Fail at startup, not at the first tool call, when configuration is unusable.
  Read configuration when the server is built (after `.env` is loaded), never
  at import time.
- An error is a returned `{ success: false, error }`, not an exception that
  kills the server. The model needs to be told what went wrong. Never report
  success for something the PLC did not confirm, and never return a guessed
  value.
- Every tool has a docstring: it is the description clients show in
  `tools/list`.
- Keep tool names and argument names aligned with the other IndustriConnect
  servers.
- If you change dependencies, run `uv lock` in that project and commit the
  updated `uv.lock`; CI installs with `--locked` and fails on a stale lockfile.

## Pull requests

Branch off `main` and keep each pull request to one concern. Say which
component is affected, how you tested, and whether the change touches anything
that can write to a PLC. If a change was AI-assisted, keep the
`Co-Authored-By:` trailer.

## Code of Conduct

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
Security issues go through [SECURITY.md](SECURITY.md), not public issues.
