## Summary

<!-- What does this PR do, and why? -->

## Component(s) affected

- [ ] MCP server (`s7comm-python`)
- [ ] Mock PLC (`s7comm-mock-server`)
- [ ] Docs / CI only

## Type of change

- [ ] Bug fix
- [ ] New tool
- [ ] Documentation
- [ ] Refactor / chore

## Does this change what can be written to a PLC?

- [ ] No
- [ ] Yes — describe the new capability and how it is gated (`S7_WRITES_ENABLED`, `S7_SYSTEM_CMDS_ENABLED`, …; both default to false):

## Checklist

- [ ] Tools still return the shared `{ success, data, error, meta }` envelope.
- [ ] The mock PLC can exercise the change, and I tested against it.
- [ ] `uv run pytest`, `uv run ruff check . ../s7comm-mock-server` and `uv run ruff format --check . ../s7comm-mock-server` pass in `s7comm-python` (with `uv sync --extra dev`).
- [ ] Nothing was tested against production equipment.
- [ ] `stdout` is still clean — all logging goes to `stderr`.
- [ ] If I changed dependencies, I ran `uv lock` and committed the updated `uv.lock`.
- [ ] I updated the READMEs / `TEST_SCENARIOS.md` where relevant.

## How to test

<!-- The exact commands and tool calls a reviewer can run against the mock. -->
