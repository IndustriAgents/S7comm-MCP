# 0.3.0 local release verification

Verified on 2026-10-15. Package: `industriagents-s7comm-mcp`.

This records local build verification, not registry publication. Check the
GitHub Actions runs and registry version pages for publication status. Registry
authentication/trusted publishers must be configured before release; GitHub
repository access alone does not authorize registry publication.

## Checks

- Full Python regression run: 318 passed.
- Final architecture/contract tests: 15 passed (a separate run, not additional unique tests).
- Ruff lint and formatting checks passed for source and tests.
- `uv lock --check` and contract/package version parity passed.
- Three npm launcher tests passed.
- Installed wheel and installed npm tarball both initialized over stdio outside
  the checkout, reported version 0.3.0 and listed 9 canonical tools.
  Their schemas matched the contract; invalid mode calls returned MCP errors.
- `twine check --strict` passed for the wheel and source distribution.
- The npm tarball contains exactly the same wheel bytes as the PyPI artifact.
- `git diff --check` passed.

No tests were skipped in the full regression run.

These are software and mock/loopback checks, not physical-device certification.
See [protocol capability limits](architecture.md#protocol-capability-limits).

## Artifact SHA-256

Artifacts are local build outputs in `dist/` and `npm/`, excluded from Git.
Rebuilding can change archive hashes; these identify the verified files.

| Artifact | SHA-256 |
|---|---|
| `industriagents_s7comm_mcp-0.3.0-py3-none-any.whl` | `b7fe1665de054501a60e4cb8f58ad8dc87ee80292888aae89d450f5d9272533e` |
| `industriagents_s7comm_mcp-0.3.0.tar.gz` | `3ef4b626e0aab1899e22fe9d0913bd5fa703dd65d59cbbba3429d4842673e8bf` |
| `industriagents-s7comm-mcp-0.3.0.tgz` | `fcff9b04ddf60b5df37442ec479631f379137e4cc9e22343c48f0e78ce8d85a2` |

See [release instructions](release.md) for independent registry publication.
