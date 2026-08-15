# Host Compatibility

This page describes the **unreleased `v0.4.0a6` candidate**. Its machine-readable authority is
[`profiles/release-status-v1.json`](profiles/release-status-v1.json). The preceding `v0.4.0a5`
candidate is **blocked, unreleased, and historical**.

## Compatibility matrix

| Host | Candidate surface | Evidence status | Boundary |
|---|---|---|---|
| Python 3.11–3.14 | package and local APIs | supported-Python CI `PENDING` | package metadata is not host proof |
| Generic MCP | local stdio, `aar.mcp-tools.v7`, 30 tools | local matrix `PENDING` | catalog visibility is not callable-runtime proof |
| Codex Desktop | generated plugin, `aar-codex-mcp`, `aar-codex-setup`, `aar-operations` 0.9.6 | fresh restarted host/Luna drill `PENDING` | Codex owns approvals, credentials, and tool policy |
| Hermes | generated profile and `aar-mcp` | host receipt `PENDING` | Hermes owns MCP Sampling and physical provider calls |
| ChatGPT/Codex public plugin | remote OAuth profile, 11 curated tools | deployment and review `PENDING` | not official Plugin Directory publication |

The repair candidate contains **63** required lifecycle rows. Windows full-suite, supported-Python
CI, exact-wheel, fresh restarted host/Luna drill, and independent review are all `PENDING` in the
central status file; this page does not fabricate counts from the blocked historical candidate.

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
candidate.

## Installation and fresh-task verification

Install the exact wheel or pinned candidate source, then run:

```powershell
aar-codex-setup
```

If the receipt reports `restart_required: true`, restart Codex Desktop, open a new task, load the
deferred capability tool if needed, and make one native `aar_capabilities` call. Require an attached
supervisor, the expected package/profile/skill versions, the current runtime generation, and the
candidate capability digest. A same-task catalog does not prove that the new plugin was picked up.

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

Official Plugin Directory deployment, reviewer access, OpenAI review/approval/publication, and
provider-signed attestation are **not completed**. GitHub release and local Codex installation are
separate later gates. No compatibility row on this page should be read as production deployment or
directory availability.
