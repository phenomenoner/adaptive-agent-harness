# Technical Status

**Release-contract target:** `v0.6.0a1`
**Package:** `adaptive-agent-runtime==0.6.0a1`
**MCP surface:** `aar.mcp-tools.v8`, 38 tools, including the frozen 30-tool v7 compatibility projection
**Operation skill:** `aar-operations` `0.11.0`

The machine-readable release contract for this page and the other current public surfaces is
[`profiles/release-status-v1.json`](profiles/release-status-v1.json). Exact commit, tree, wheel,
sdist, CI, clean-install, activation, native-host, provider, review, tag, and downloaded-asset evidence
is established after source freeze only by external readback and
`adaptive-agent-runtime-v0.6.0a1-release-receipt.json`, rather than being embedded in the objects it
hashes. This source snapshot does not establish that the target tag, GitHub prerelease, or receipt
exists. `v0.6.0a0`, `v0.5.0a0`, and `v0.4.0a6` are predecessor
release snapshots; their release notes and receipts are historical authorities for those versions,
not current install guidance.

Adaptive Agent Harness (AAR) is a standalone, host-neutral runtime for bounded agent operations. It
provides durable operation state, programmable workspaces, brokered RLM jobs, immutable adaptive
assets, and receipt-backed caller work without becoming a provider credential store or a full agent
host.

> AAR computes and proposes. The host authorizes and delivers.

## Release-contract behavior

`v0.6.0a1` keeps the public 38-tool MCP v8 names and schemas unchanged while adding an optional
standalone Hermes adapter and explicit trusted-local session-grant authority:

- `aar-hermes-mcp` starts or reuses one exact provider-ready supervisor, verifies package, process,
  protocol, generation, capability, activation, and route bindings, then attaches stdio;
- `aar-hermes-authority issue` and `revoke` use the authenticated owner-only private supervisor
  channel; they do not add a public MCP method or durable grant store;
- every provider-ready public mutation requires a current, explicitly issued, memory-only,
  generation-bound session grant before its first durable write;
- install, startup, Ready, attach, capability reads, reference context, and ordinary requests never
  issue a grant automatically;
- restart clears all in-memory grants while preserving durable activation and operation state;
- `host-caller-driver-v1` uses the durable caller protocol: claim, mark-send-started, a physical call
  owned by the host, and an exact response/usage receipt commit;
- a sent request without an authenticated terminal receipt is `indeterminate` and must not be blindly
  retried;
- AAR receives no provider credential and performs no host-owned physical provider request.

The public operator path is credential-free. It derives an exact
`aar.install-candidate-receipt.v1` from a verified wheel and source identity, then issues a
self-digested, target-bound `aar.host-activation-intent.v1` from explicit host-owned inputs. Neither
command installs, activates, migrates, or mutates an existing runtime root. This release contract is
**clean-install-only**: install into a fresh absent root and retain predecessor roots unchanged.

## Current capabilities

### Contract and transport

- strict Pydantic contracts with generated JSON Schemas;
- deterministic canonical JSON and content digests;
- direct Python APIs and local-stdio MCP;
- 38 MCP v8 tools with exact generated/list/serialization parity;
- frozen 30-tool MCP v7 compatibility bytes;
- identities, generations, revisions, deadlines, grants, budgets, and idempotency keys carried in
  structured requests and receipts.

### Durable operations and programmable workspaces

- accepted intent is durable before dispatch;
- attempts, events, cancellation, receipts, and recovery survive frontend loss;
- stale generations and late writers fail closed;
- uncertain state-changing outcomes remain `indeterminate` until reconciled;
- plain-Python and supervised IPython workspaces use generation and revision checks;
- JSON-subset checkpoints and content-addressed artifacts preserve explicit portability boundaries.

### Brokered RLM and caller work

- bounded persisted RLM jobs with cumulative deadline, model-call, token, child, and artifact budgets;
- typed model, child, artifact, evidence, and external-effect broker contracts;
- exact pre-spend claim and send-start linearization points;
- requested-versus-effective route receipts, provider-reported usage, retry ordinals, and fallback
  chains;
- proposal-only external effects and host-owned materialization.

## Host compatibility summary

| Host surface | Release surface | Boundary |
|---|---|---|
| Direct Python | contract models, supervisor/frontend APIs, reference host | embedding host preserves identity and receipt semantics |
| Generic MCP | local stdio, 38-tool v8 surface | transport visibility is not runtime or authority proof |
| Codex Desktop | generated plugin/profile, `aar-codex-mcp`, setup helper | Codex owns approvals, credentials, and configuration authority |
| Hermes | generated profile plus optional `aar-hermes-mcp` and private authority CLI | Hermes is one host adapter; AAR core and caller work remain host-neutral |
| ChatGPT/Codex public plugin | separate OAuth profile with 11 curated tools | submission material is not Plugin Directory publication |

See [Host Compatibility](HOST-COMPATIBILITY.md) for install and readback details.

## Verification authority

The 20-row provider-ready host matrix is frozen in
[`docs/sdd/aar-hermes-provider-ready-host-v1/ACCEPTANCE.md`](docs/sdd/aar-hermes-provider-ready-host-v1/ACCEPTANCE.md).
The in-tree status records the frozen claim budget and exact public surface; it does not claim that
post-freeze CI, independent review, release, persistent install, or live cutover occurred. Those facts
must appear in `adaptive-agent-runtime-v0.6.0a1-release-receipt.json` and be checked against the exact
tag and downloaded assets.

A local unit result, generated profile, package scan, temporary installation, or host catalog row
cannot establish the whole release receipt. Each claim keeps its own evidence altitude.

## Publication and authority limits

This source release contract does not establish publication of the target tag, GitHub prerelease,
or receipt. It also does not establish PyPI publication, general production deployment, official
Plugin Directory availability, reviewer access, OpenAI review or approval, or provider-signed
attestation. Those require separate external authority. The subprocess Codex configuration route
continues to report `NO_ATOMIC_AUTHORITY` and returns a manual plan before any forward mutation.

## Known limitations

- public alpha interfaces may change before a stable release;
- programmable Python/IPython execution is not a security sandbox;
- package installation does not create an operating-system service or configure a host;
- unresolved post-send provider outcomes may remain `indeterminate`;
- exactly-once provider execution is claimed only when an authoritative receipt proves it;
- no managed AHC adapter is included;
- generated profiles and capability catalogs never grant mutation authority.

See [Release notes](docs/RELEASE-v0.6.0a1.md), [Architecture](ARCHITECTURE.md),
[Testing](docs/TESTING.md), and the [Changelog](CHANGELOG.md).
