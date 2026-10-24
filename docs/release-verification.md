# 0.3.0 local release verification

Verified on 2026-10-24. Package: `industriagents-s7comm-mcp`.

This records local build verification after the OPCUA architecture review and
boundary hardening. It does not record a registry publication. The current
package version is retained; no tag, push or publication was performed.

## Checks

- Full Python regression run: 354 passed.
- The full suite includes request depth/size bounds before dispatch, legacy
  unknown-argument rejection, cancellation admission release, text/structured
  audit consistency, and unexpected post-dispatch control failures.
- Ruff lint and formatting checks passed for source and tests.
- `uv lock --check` and contract/package version parity passed.
- Three npm launcher tests passed.
- Installed wheel and installed npm tarball initialized over stdio outside the
  checkout, reported the manifest version 0.3.0 and listed 9 canonical tools.
  Schemas matched the reviewed contract and invalid modes returned MCP errors.
  Smoke tests select exact manifest-version filenames, so stale archives cannot
  accidentally satisfy a later version's checks.
- `twine check --strict` passed for the wheel and source distribution.
- Workspace artifact inspection verified the current Python sources and
  contract inside the wheel and identical wheel bytes in the npm tarball.
- `git diff --check` passed.

Skipped tests: None.

These are software and mock/loopback checks, not physical-device certification.
See [protocol capability limits and architecture differences](architecture.md).

## Second architecture review

- Truncated raw, BOOL and STRING preflight reads now refuse the complete batch
  before any write; each regression previously observed an actual write call.
- Empty and malformed legacy batches explicitly report `not_sent` and
  `request_sent:false`.
- Shared policy and audit checks reject FIFOs/special files without blocking,
  oversized/deep policy documents and duplicate keys.
- All verification commands, rebuilt wheel/source/npm artifacts, installed
  stdio smokes and strict package metadata checks passed again after these fixes.

## Artifact SHA-256

Artifacts are local build outputs in `dist/` and `npm/`, excluded from Git.
Rebuilding can change archive hashes; these identify the verified files.

| Artifact | SHA-256 |
|---|---|
| `industriagents_s7comm_mcp-0.3.0-py3-none-any.whl` | `e7007f3e8ad44bf3fa18463216cc7318f16ba865438684a123941ab9f9c5de5a` |
| `industriagents_s7comm_mcp-0.3.0.tar.gz` | `b78776cc5b47c4f4417446145d4ad0d95660e03a4231047df0b88ccee16e5305` |
| `industriagents-s7comm-mcp-0.3.0.tgz` | `032016bc21bef21fb98b46fa5a8d6ac0bf7cbc0eb6dd6111dd2a140836f6b373` |

See [release instructions](release.md) for independent registry publication.
