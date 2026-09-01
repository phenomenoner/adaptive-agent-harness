# Host Compatibility

This page is the host-facing part of the **`v0.6.0a2` release contract**. Its in-tree authority is
[`profiles/release-status-v1.json`](profiles/release-status-v1.json). This source does not establish
that the target tag, GitHub prerelease, or `adaptive-agent-runtime-v0.6.0a2-release-receipt.json`
exists; publication and exact post-freeze evidence require external readback. Earlier release pages
are historical and are not current install guidance.

## Compatibility matrix

| Host | Release surface | Evidence authority | Boundary |
|---|---|---|---|
| Python 3.11–3.14 | package and local APIs | exact-ref CI and wheel fields in the external receipt | package metadata alone is not host proof |
| Generic MCP | local stdio, `aar.mcp-tools.v8`, 38 tools | generated-byte and real SDK list/serialization evidence | catalog visibility is not callable-runtime proof |
| Codex Desktop | generated plugin, `aar-codex-mcp`, `aar-codex-setup`, `aar-operations` 0.11.0 | fresh native and caller-work fields in the external receipt | Codex owns approvals, credentials, and tool policy |
| Hermes | generated profile; optional `aar-hermes-mcp` and `aar-hermes-authority` | exact clean-install, private-grant, native capability, and caller-provider receipts | Hermes is one optional host adapter, not AAR core or provider authority |
| ChatGPT/Codex public plugin | separate remote OAuth profile, 11 curated tools | submission material only | not official Plugin Directory publication |

The v8 surface appends eight workbench/caller-work tools to the frozen 30-tool v7 compatibility
projection. The 20-row `v0.6.0a2` acceptance matrix is an in-tree frozen claim budget. Commit, tree,
wheel, sdist, CI, clean install, activation, native host, true provider, independent review, tag, and
downloaded-asset facts remain separate fields in
`adaptive-agent-runtime-v0.6.0a2-release-receipt.json`.

## Standalone Hermes contract

`aar-hermes-mcp` is a narrow optional adapter. It checks one exact release package, runtime owner,
supervisor protocol, generation, capability digest, activation intent, and route catalog before
attaching stdio. It never converts install, startup, Ready, attach, capability discovery, reference
context, or an ordinary MCP request into mutation authority.

The trusted-local `aar-hermes-authority issue` and `revoke` commands authenticate through the
owner-only private supervisor channel. Issued grants are explicit, current-generation, bounded by
installed policy and the supervisor clock, and held only in memory. Restart invalidates them. The CLI
does not print the attachment credential, create a public MCP method, or write a durable grant store.

A caller-owned model route uses:

```text
claim -> mark-send-started -> host physical provider call -> exact response/usage receipt commit
```

AAR stores the durable ticket and receipt but never receives the host's provider credential. A lost
terminal response after send is `indeterminate`; neither the adapter nor the caller may blindly send
the request again.

## Codex setup contract

The generated Codex plugin uses `aar-codex-mcp` and a durable supervisor with an ephemeral frontend.
`aar-codex-setup` continues to report **`NO_ATOMIC_AUTHORITY`** for the subprocess Codex configuration
route. It reads current state and returns an ordered manual/provider-authority-required plan before a
forward mutation. The operator applies that plan through the authoritative Codex configuration owner
and reruns setup. There is no automatic install, rollback, or best-effort compensation.

A future provider adapter may enable automatic configuration only if it supplies an opaque revision
and atomic expected-revision CAS contract, including conflict and indeterminate-path tests.

## Installation and fresh-session verification

Install the exact wheel or pinned release source. For Codex Desktop, run:

```powershell
aar-codex-setup
```

Restart Codex Desktop and open a new task when the current receipt reports
`restart_required: true`, or when a preserved non-empty manual-plan receipt reports
`restart_required_after_manual_apply: true`. A later no-op receipt with
`restart_required: false` does not erase that handoff. From the fresh task, make one native
`aar_capabilities` call and verify package `0.6.0a2`, operation skill `0.11.0`, attached-supervisor
mode, current runtime generation, and capability digest.

For the standalone provider-ready path, derive the public install-candidate receipt from the exact
released wheel, issue the activation intent from explicit host-owned inputs, and install only into a
fresh absent root. Start through `aar-hermes-mcp`, read both `aar_capabilities` and
`aar_rlm_workbench_capabilities`, then explicitly issue only the session grants needed for the bounded
mutation. Do not preserve, adopt, promote, archive, remove, or mutate a predecessor runtime root.

Developer-only generated-byte checks are:

```powershell
uv run aar-mcp-assets verify --root .
uv run aar-host-assets verify --root .
```

They do not prove a fresh host, private authority, physical provider call, release installation, or
external publication.

## Runtime ownership and retention

Supervisor endpoint, credential, and shutdown-request paths are generation-unique;
`discovery.json` is an atomically advanced stable pointer carrying the publication ID. Normal
lifecycle shutdown is non-destructive and retains generation-specific control artifacts as forensic
state. The database-scoped process lock admits the sole active runtime owner and is released on
process exit. Do not delete a successor or predecessor based only on a pathname, visible bytes, PID,
or stale classification.

## External publication boundary

This release contract does not establish publication of the target tag, GitHub prerelease, or
receipt. It also does not establish PyPI publication, general production deployment, official
Plugin Directory availability, reviewer access, OpenAI review or approval, or provider-signed
attestation. No compatibility row on this page should be read as that external authority.
