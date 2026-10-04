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
├── s7comm-python/          # the MCP server: pyproject.toml + src/s7comm_mcp/ (uv project)
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
2. **Writes are gated.** Tools that change PLC memory, outputs or CPU state are
   separate from the read tools and can be switched off by configuration. Today
   the write tools are on unless `S7_WRITES_ENABLED=false`, and `set_cpu_state`
   is off unless `S7_SYSTEM_CMDS_ENABLED=true`. New write paths must honour the
   same switches.
3. **The mock can answer it.** If you add a tool, the mock PLC has to be able
   to serve it, or nobody can test it without a plant.

## Getting set up

You need Python 3.11+ and `uv`. The python-snap7 wheels bundle the Snap7 native
library for macOS, Linux (x86_64, aarch64) and Windows (x64), so there is
nothing else to install on those platforms.

Start the mock first — it is the thing the server talks to, and the server
connects to the PLC at startup, so it exits if nothing is listening:

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

The server speaks MCP over stdio, so on its own it just waits for a client.

## Testing a change

There is no automated test suite yet; tests are a welcome contribution. CI runs
cheap checks that need no PLC — install from the lockfile, byte-compile, import
the package and register its tools, and run the mock's `--help`. The core of it,
to run locally before opening a pull request:

```bash
(cd s7comm-python && uv sync --locked && uv run --no-sync python -m compileall -q src)
(cd s7comm-mock-server && uv sync --locked && uv run --no-sync s7comm-mock-server --help)
```

The full list of steps is in [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

Then drive the server the way a user would — through the
[MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector) or a real
client — against the mock:

```bash
npx @modelcontextprotocol/inspector -e S7_HOST=127.0.0.1 -e S7_PORT=1102 -e S7_WRITES_ENABLED=false \
  uv --directory s7comm-python run s7comm-mcp
```

Drop `-e S7_WRITES_ENABLED=false` when the change you are testing is a write
tool. Call the tool you changed and check the envelope, not just the value.
[`s7comm-mock-server/TEST_SCENARIOS.md`](s7comm-mock-server/TEST_SCENARIOS.md)
lists the mock's memory map and the values to expect, and marks the scenarios
that fail today.

**Do not test against production equipment.** If a change genuinely cannot be
verified against the mock, say so in the pull request and describe the bench
you used (CPU, firmware, PUT/GET settings).

## House rules for the code

- MCP speaks over **stdio**, so `stdout` belongs to the protocol. All logging
  goes to `stderr`. A stray `print()` corrupts the session.
- Fail at startup, not at the first tool call, when configuration is unusable.
- An error is a returned `{ success: false, error }`, not an exception that
  kills the server. The model needs to be told what went wrong.
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
