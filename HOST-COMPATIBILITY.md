# Host Compatibility

This page is the host-facing part of the **`v0.4.0a6` release snapshot**. Its immutable in-tree
contract is [`profiles/release-status-v1.json`](profiles/release-status-v1.json); exact post-freeze
evidence is bound by `adaptive-agent-runtime-v0.4.0a6-release-receipt.json`. The preceding
`v0.4.0a5` candidate is **blocked, unreleased, and historical**.

## Compatibility matrix

| Host | Release surface | Evidence authority | Boundary |
|---|---|---|---|
| Python 3.11–3.14 | package and local APIs | exact-ref CI field in external receipt | package metadata is not host proof |
| Generic MCP | local stdio, `aar.mcp-tools.v7`, 30 tools | completed local matrix plus exact host receipt | catalog visibility is not callable-runtime proof |
| Codex Desktop | generated plugin, `aar-codex-mcp`, `aar-codex-setup`, `aar-operations` 0.9.6 | fresh native and caller-delegated RLM fields in external receipt | Codex owns approvals, credentials, and tool policy |
| Hermes | generated profile and `aar-mcp` | generated-byte verification only in this snapshot | Hermes owns MCP Sampling and physical provider calls |
| ChatGPT/Codex public plugin | remote OAuth profile, 11 curated tools | submission material only | not official Plugin Directory publication |

The release contains **63** required lifecycle rows and completed Windows behavioral evidence.
Commit, tree, exact wheel, supported-Python CI, local install, fresh restarted native/RLM drill,
review, tag, and downloaded-asset readback remain separate hash-bound fields in
`adaptive-agent-runtime-v0.4.0a6-release-receipt.json`.

## Local Codex setup contract

The Codex plugin uses `aar-codex-mcp` as the declared stdio adapter and a durable supervisor with an
ephemeral frontend. A fresh task must call native `aar_capabilities` after restart. A configured
server, CLI probe, catalog row, or same-task transport response is not runtime evidence.

`aar-codex-setup` currently has **`NO_ATOMIC_AUTHORITY`** for the subprocess Codex route. It reads
state and returns an ordered manual/provider-authority-required plan before any forward mutation.
The host/operator must apply that plan through the authoritative Codex configuration owner and then
rerun setup. There is no automatic install, rollback, or best-effort compensation. A real provider
adapter may restore automatic mutation only when it supplies an opaque revision and an atomic
expected-revision **CAS** contract, with conflict and indeterminate-path tests.

Supervisor endpoint, credential, and shutdown-request paths use generation-unique names;
`discovery.json` is a stable pointer carrying the publication ID and advances atomically. Normal
lifecycle cleanup and shutdown-request consumption are **non-destructive** and retain
generation-specific control artifacts as forensic state. The existing database-scoped process lock
admits the sole active runtime owner before host construction and is released automatically on process exit. Do
not delete a successor based on a pathname, visible
bytes, or a stale owner classification. An explicit offline garbage collector is outside this
release.

## Installation and fresh-task verification

Install the exact wheel or pinned release source, then run:

```powershell
aar-codex-setup
```

Restart Codex Desktop, open a new task, load the deferred capability tool if needed, and make one
native `aar_capabilities` call when the current receipt reports `restart_required: true` **or** a
preserved non-empty manual-plan receipt reports
`restart_required_after_manual_apply: true`. A later already-configured result with
`restart_required: false` does not erase the preserved restart handoff. Require an attached
supervisor, the expected package/profile/skill versions, current runtime generation, and release
capability digest. A same-task catalog does not prove that the new plugin was picked up.

For a developer-only local check, the generated host assets can be verified from the repository root:

```powershell
uv run aar-mcp-assets verify --root .
uv run aar-host-assets verify --root .
```

These checks prove generated-byte consistency only. They do not prove a fresh host, provider
authority, deployment, or official Plugin Directory publication.

## RLM model authority

The caller selects one model and optional reasoning effort per caller-delegated RLM job. The host
executes the physical provider call; AAR stores the ticket, bounded result, and receipt without
credentials. The developer broker and the public caller-delegated profile are distinct surfaces.
The fixed Hermes MCP Sampling route is compatibility-only and does not become public authority.

## External publication boundary

This GitHub release snapshot does not establish official Plugin Directory deployment, reviewer
access, OpenAI review/approval/publication, or provider-signed attestation. Those require separate
external authority. No compatibility row on this page should be read as production deployment or
directory availability.
