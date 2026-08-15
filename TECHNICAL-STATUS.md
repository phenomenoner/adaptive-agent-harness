# Technical Status

**Release:** `v0.4.0a5` public-alpha candidate
**Package:** `adaptive-agent-runtime==0.4.0a5`
**MCP surfaces:** local `aar.mcp-tools.v7` with 30 tools; remote public profile with 11 tools
**Operation skill:** `aar-operations` `0.9.5`

Adaptive Agent Harness (AAR) is an executable, contract-first runtime for bounded agent operations. It provides durable operation state, programmable workspaces, brokered RLM jobs, immutable adaptive assets, and receipt-backed model routing through a host-owned provider gateway.

The governing boundary is:

> AAR computes and proposes. The host authorizes and delivers.

AAR does not own provider credentials, external-effect authorization, activation, deployment, or final delivery.

## Current capabilities

### Contract and transport

- strict Pydantic contracts with generated JSON Schemas;
- deterministic canonical JSON and content digests;
- valid and invalid conformance fixtures;
- direct Python APIs and a local-stdio MCP server;
- structured tool results with explicit identities, generations, revisions, deadlines, grants, budgets, and idempotency keys;
- a canonical host-neutral operation skill copied byte-for-byte into generated host profiles.

### Durable operation lifecycle

- accepted intent is persisted before execution;
- attempts, leases, events, cancellations, receipts, and recovery decisions survive frontend loss;
- stale generations and late writers fail closed;
- uncertain state-changing outcomes remain `indeterminate` until reconciled;
- one durable supervisor owns the runtime while replaceable MCP frontends attach through an owner-private authenticated transport;
- process identity checks defend against stale discovery and PID reuse.

### Programmable workspaces

- plain-Python and supervised IPython backends;
- create, attach, execute, inspect, interrupt, checkpoint, restore, health, reconcile, and close;
- portable JSON-subset checkpoint manifests with explicit exclusions;
- new-generation restore instead of pretending to resurrect arbitrary process memory;
- packaged IPython, NumPy, and pandas support in the default analysis environment.

AAR is not a Python security sandbox. Hostile or multi-tenant execution requires a separate isolation boundary.

### Brokered RLM and adaptive assets

- bounded persisted RLM jobs with step traces and cumulative budgets;
- typed model, subagent, effect, artifact, and evidence broker contracts;
- retained child handles and explicit result retrieval;
- authoritative receipt reuse after recovery;
- immutable content-addressed assets and dependency-closed import/export;
- explicit selection, disclosure, use, outcome, and attribution events;
- proposal-only external effects and host-owned materialization.

### Receipt-backed model routing

`v0.3.0a2` adds an owner-controlled production-model boundary:

- credential-free, digest-bound route catalogs and profiles;
- immutable admission bindings;
- durable model execution state;
- route and provider-usage receipts;
- explicit retry, fallback, timeout, cancellation, and uncertainty semantics;
- a bounded MCP Sampling gateway in which the MCP client owns the physical provider call;
- an explicit strict profile for `openai-codex / gpt-5.6-luna / max`;
- rejection of missing, malformed, drifted, retried, or fallback receipts under that profile;
- benchmark manifest and usage-ledger fragments for paired evaluation contracts.

See [Receipt-backed model routing and fair evaluation](docs/MODEL-ROUTING-AND-EVALUATION.md).

### Public plugin and caller-delegated RLM

`v0.4.0a0` adds a separate OAuth-authenticated Streamable HTTP profile for ChatGPT and Codex:

- six tenant-private structured-workspace tools and five caller-delegated RLM tools;
- one host-selected executor, model, and optional reasoning effort per job;
- exact pre-spend claim tickets and compare-and-set bounded result commits;
- durable idempotency, cancellation, restart, LRU reopen, and terminal-key conflict behavior;
- digest-only bounded command markers and explicit workspace, request, artifact, job, and value quotas;
- a deterministic plugin tree, scoped skill ZIP, reviewer cases, brand assets, and container profile.

AAR does not receive provider credentials or execute the public model call. The authenticated main
agent or host performs it and reports only observed route, usage, output, and optional receipt data.
See [Public plugin and submission boundary](docs/PUBLIC-PLUGIN.md) and
[Caller-delegated RLM design](docs/PUBLIC-RLM-PRODUCT-BOUNDARY.md).

## Host support

| Host surface | Current public support | Boundary |
|---|---|---|
| Direct Python | contract models, reference host, supervisor/frontend APIs | embedding host must preserve AAR identity and receipt semantics |
| Generic MCP | local stdio, 30 public tools | MCP transport is not an authority proof |
| ChatGPT / Codex public plugin candidate | remote OAuth profile, 11 curated tools, scoped skill | local lifecycle evidence is not production deployment, OpenAI approval, or publication |
| Codex | generated plugin/profile, setup helper, canonical operation skill | Codex owns approvals, credentials, and tool policy |
| Hermes | generated profile, ordinary MCP registration, durable supervisor integration | Hermes owns MCP Sampling and physical provider requests |

See [Host Compatibility](HOST-COMPATIBILITY.md) for reproducible setup and verification commands.

## Release verification

The `v0.4.0a5` candidate is verified through:

- explicit platform skips where a process or filesystem primitive is unavailable;
- Ruff checks;
- locked dependency resolution;
- generated contract, MCP asset, and host-profile verification;
- canonical skill parity across the source, Codex, and Hermes copies;
- public-release checks for credentials, private keys, machine-local paths, runtime databases, local receipts, internal work logs, binaries, broken links, broken anchors, translation identity, and release identity;
- exact-wheel ZIP, metadata, RECORD, packaged-asset, and isolated-install readback.

The exact commit must also pass the full Python 3.11–3.14 Linux GitHub Actions matrix, exact-wheel
setup and lifecycle probes, restarted Desktop native verification, and a fresh independent release
review before the `v0.4.0a5` tag is created.

The GitHub release publishes `adaptive_agent_runtime-0.4.0a5-py3-none-any.whl` with a matching
`.sha256` sidecar. Verify the downloaded wheel against that sidecar before installation. The same
release includes the deterministic public plugin packet as submission material; it is not evidence
that the plugin is listed in the official directory.

The repository suite emitted one MCP Sampling deprecation warning. Sampling is deprecated in protocol revision `2026-07-28` under SEP-2577; the current integration retains a bounded compatibility path for hosts that support the bidirectional back-channel.

The stable `v0.4.0a5` source candidate passed 445 Windows repository tests. They cover the exact
native process-handle, virtual-environment child PID, supervisor replacement, managed-worker
handoff, configuration containment, and ownership-lock seams that lower-altitude Linux-only checks
cannot represent. One directory-symlink capability test was skipped and the deprecated Sampling
path emitted its expected warning. This does not replace the supported-Python Linux CI gate or
fresh local Codex acceptance.

## Security and authority boundaries

AAR owns:

- programmable workspace execution;
- bounded RLM jobs and traces;
- durable operation and reconciliation semantics;
- immutable adaptive assets;
- transport-neutral broker, artifact, and evidence contracts;
- evaluation and proposal logic.

The host owns:

- principal and session identity;
- admission, grants, budgets, and policy;
- provider credentials and physical provider calls;
- authoritative external effects;
- activation, promotion, rollback authorization, and final delivery.

A successful MCP call, model response, prepared materialization, or generated profile does not imply deployment, activation, or delivery.

## Known limitations

- public alpha interfaces may change before a stable release;
- no security sandbox is provided; the public structured-state adapter separates tenant databases, while arbitrary programmable execution still requires a host isolation boundary;
- package installation does not create an operating-system service or configure a host;
- MCP Sampling is deprecated and requires a future replacement transport;
- provider response lookup is not portable; unresolved post-send outcomes may remain indeterminate;
- exactly-once provider execution is claimed only when the provider or owner receipt can prove it;
- no managed AHC adapter is included in this release;
- no formal cross-harness benchmark result or universal performance claim is published;
- generated host profiles preserve the public contract but do not grant authority.

## Next work

- deploy and qualify the public profile with real OAuth, reviewer access, operational controls, and both Codex and ChatGPT execution before requesting directory review;
- retire the fixed MCP Sampling route after supported Hermes users migrate to caller-delegated or another host-owned broker transport with equivalent receipt semantics;
- expand provider-neutral attempt receipts and reconciliation adapters;
- improve portable workspace restoration;
- add conformance kits for additional hosts and non-Python consumers;
- strengthen paired evaluation tooling with complete call-census and wasted-token accounting;
- stabilize extension interfaces only after multiple independent hosts prove them.

See [Development Roadmap](DEVELOPMENT-PLAN.md), [Architecture](ARCHITECTURE.md), [Testing](docs/TESTING.md), and the [Changelog](CHANGELOG.md).
