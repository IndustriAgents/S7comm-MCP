---
name: Feature request
about: Suggest a new tool or an improvement to the S7comm MCP server or mock PLC
title: "[Feature] "
labels: enhancement
assignees: ""
---

**What problem does this solve?**
The use case, from the point of view of someone asking an agent to do something.

**Which component?**
- [ ] MCP server (`s7comm-python`)
- [ ] Mock PLC (`s7comm-mock-server`)

**Proposed solution**
If it is a new tool: its name, arguments, and what it returns. Tool names and
arguments are kept consistent across the
[IndustriConnect](https://github.com/IndustriAgents/IndustriConnect) suite, so
say how it lines up with the equivalent tool in the other protocol servers.

**Does it write to a PLC?**
- [ ] No — read-only
- [ ] Yes — it changes PLC memory, outputs or CPU state

**Can the mock answer it?**
What the mock PLC would need to simulate for this to be testable without real
plant.

**Additional context**
Links to Siemens documentation, the Snap7 API, example CPUs, or related issues.
