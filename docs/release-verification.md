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
| `industriagents_s7comm_mcp-0.3.0-py3-none-any.whl` | `4c9a6ca0c47ac8ce92d13814d06960e448d54f4ba8f1a52258304fe4b3817116` |
| `industriagents_s7comm_mcp-0.3.0.tar.gz` | `b825c92869e2a79f6f5ad86ee66ab309f833d5dedb952c2199e2d1d8e89b14a3` |
| `industriagents-s7comm-mcp-0.3.0.tgz` | `96a74d3902d2ccc33f89134ec46a30af083e8f92a8c04bfb8e9fd74f1ca8379a` |

See [release instructions](release.md) for independent registry publication.
