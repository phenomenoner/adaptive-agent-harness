# Install AAR for Codex App

This page is the Codex installation entrypoint for the **`0.6.0a1` release contract**. The in-tree
contract is [`profiles/release-status-v1.json`](../profiles/release-status-v1.json). This source does
not establish that the target tag, GitHub prerelease, or
`adaptive-agent-runtime-v0.6.0a1-release-receipt.json` exists; publication and exact source, wheel,
CI, install, host, review, tag, and asset-readback evidence require external readback. Earlier release
pages and receipts are historical authorities for their exact versions, not current install guidance.

After GitHub readback confirms the target tag exists, the pinned-source installation path is:

```powershell
uv tool install --force "git+https://github.com/phenomenoner/adaptive-agent-harness.git@v0.6.0a1"
aar-codex-setup
```

Maintainers who already have an immutable wheel may instead use:

```powershell
uv tool install --force D:\path\to\adaptive_agent_runtime-0.6.0a1-py3-none-any.whl
aar-codex-setup
```

For an upgrade, stop the exact Codex-owned runtime first and close any Codex App task already using
AAR before replacing the uv tool:

```powershell
aar-codex-setup --stop-runtime
```

## Setup authority contract

The subprocess Codex adapter reports **`NO_ATOMIC_AUTHORITY`**. It reads the current marketplace,
plugin, and MCP state and returns an ordered manual/provider-authority-required plan before the
first forward mutation. The host/operator must apply that plan through the authoritative Codex
configuration owner and rerun setup. The adapter does not perform automatic install, rollback, or
best-effort compensation. Do not treat a CLI command, visible value, post-command readback, or
equal plugin version as a transaction revision or CAS token.

If setup reports `manual_authority_required`, preserve the returned plan and current state. Apply
only the exact requested steps through the provider that owns Codex configuration, then rerun the
read-only setup inspection. A conflict or uncertain effect is a stop-and-reconcile condition; do
not repeat a mutation blindly. If a future provider adapter exposes idempotency, reuse the original
key only for a byte-identical request.

The manual receipt sets `restart_required_after_manual_apply: true` whenever its ordered plan is
non-empty. Preserve that receipt across the operator-authorized steps: after the plan is applied and
readback is current, restart Codex Desktop even if the subsequent already-configured receipt has
`restart_required: false`. No persistent installer ledger is needed for this handoff.

The release's supervisor endpoint, credential, and shutdown-request paths use generation-unique
names; `discovery.json` is a stable pointer carrying the publication ID and advances atomically.
Normal lifecycle startup, stale-owner handling, shutdown consumption, and terminal cleanup are
deliberately **non-destructive**. Retained generation-specific control artifacts are forensic state;
never delete a successor by pathname or visible bytes. The existing database-scoped
process lock, rather than a durable lease or reservation record, admits the sole active runtime and is
released automatically on process exit. Any future offline garbage collector is outside this
release.

## Fresh-task verification

The command prints a JSON receipt. A passing setup receipt must contain the exact declared launcher,
an attached supervisor, package/profile/skill versions, runtime and dispatcher generations, and a
current discovery digest. If production startup fails after a desired configuration was retained,
the receipt is not setup success; recover the exact owner and rerun setup.

When the receipt reports `restart_required: true`, restart Codex Desktop, open a **fresh task**, load
the deferred capability tool if needed, and call native `aar_capabilities` once. Verify package
`0.6.0a1`, operation skill `0.11.0`, the current Codex profile cachebuster, attached-supervisor mode, and
the current runtime generation. A stale task catalog, config text, CLI probe, or same-task transport
error is not runtime proof.

If setup or the launcher reports that native process identity is unavailable, do not delete the
runtime-home control files or force a second owner. Retry after native observation recovers.

Useful bounded variants:

```powershell
aar-codex-setup --preflight-only
aar-codex-setup --skip-preflight
aar-codex-setup --stop-runtime
aar-codex-setup --help
```

`--skip-preflight` is intended only for a repeated configuration-only inspection after the same
wheel has passed its preflight. It does not grant provider mutation authority.

## Maintainer verification is separate

Release maintainers additionally run Ruff, focused regressions, the full repository suite, Python
3.11–3.14 compatibility, exact-wheel readback, fresh Codex scenarios, and the caller-delegated
provider drill. The 20-row provider-ready host matrix is the frozen claim budget; post-freeze results
belong in `adaptive-agent-runtime-v0.6.0a1-release-receipt.json`. Local installation does not establish
official Plugin Directory deployment or publication.

The setup command does not grant provider credentials, external effects, activation, publication,
or final delivery. AAR computes and proposes; the host authorizes and delivers.
