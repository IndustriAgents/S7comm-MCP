---
name: Bug report
about: Report a problem with the S7comm MCP server or the mock PLC
title: "[Bug] "
labels: bug
assignees: ""
---

**Which component?**
- [ ] MCP server (`s7comm-python`, `s7comm-mcp`)
- [ ] Mock PLC (`s7comm-mock-server`)

**Describe the bug**
What happens, and what you expected instead.

**To reproduce**
The MCP client you used (Claude Desktop / Claude Code / Cursor / Inspector /
IndustriConnect's mcp-manager-ui), the tool you called, and the arguments you
passed.

1.
2.
3.

**Tool output**
```json
paste the { success, data, error, meta } envelope, or the error
```

**What was on the other end?**
- [ ] The mock PLC from this repo
- [ ] A real PLC — CPU family (S7-300 / 400 / 1200 / 1500 / other), order
      code and firmware:

**Connection settings**
`S7_HOST` (redact if needed), `S7_PORT`, `S7_RACK`, `S7_SLOT`, and any
`S7_WRITES_ENABLED` / `S7_SYSTEM_CMDS_ENABLED` / `TAG_MAP_FILE` you set.
Never paste credentials or a whole `.env` file.

**Environment**
- OS:
- Python version:
- python-snap7 version (`uv pip show python-snap7`):
- Commit SHA:

**Additional context**
DB numbers and offsets, data types, stderr logs — whatever would let someone
else reproduce it against the mock.
