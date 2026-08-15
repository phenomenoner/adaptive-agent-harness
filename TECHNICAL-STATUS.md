# Technical Status

**Candidate:** `v0.4.0a6` (candidate, unreleased)
**Package:** `adaptive-agent-runtime==0.4.0a6`
**MCP surfaces:** local `aar.mcp-tools.v7` with 30 tools; remote public profile with 11 tools
**Operation skill:** `aar-operations` `0.9.6`

The machine-readable authority for this page and the other five public release surfaces is
[`profiles/release-status-v1.json`](profiles/release-status-v1.json). It records the candidate as
`candidate_unreleased`, the 62-row repair matrix, and the pending external receipts. The preceding
`v0.4.0a5` candidate is **blocked, unreleased, and historical**; it is not the current release.

Adaptive Agent Harness (AAR) is an executable, contract-first runtime for bounded agent operations.
It provides durable operation state, programmable workspaces, brokered RLM jobs, immutable adaptive
assets, and receipt-backed model routing through a host-owned provider gateway.

> AAR computes and proposes. The host authorizes and delivers.

AAR does not own provider credentials, external-effect authorization, activation, deployment, or
final delivery.

## Candidate behavior

The `0.4.0a6` candidate closes the reviewed lifecycle seams with these contracts:

- spawned supervisor and IPython children are admitted with one exact native process object and
  terminalized through that object, including same-handle wait and at-most-once receipts;
- each continuity database admits one active runtime through the existing process lock acquired
  before host construction and released automatically on process exit;
- supervisor endpoint, credential, and shutdown-request paths are generation-unique while
  `discovery.json` is an atomically advanced stable pointer carrying the publication ID; normal
  lifecycle retirement is **non-destructive** and retains generation-specific control artifacts as
  forensic state rather than deleting a possible successor;
- the subprocess Codex configuration adapter reports `NO_ATOMIC_AUTHORITY` and returns an ordered
  manual/provider-authority-required plan before the first forward mutation;
- automatic Codex installation, rollback, and best-effort compensation are not claimed;
- the canonical operation skill and generated Codex/Hermes copies are `0.9.6`.

The provider-backed RLM boundary remains host-owned. A main agent chooses one model and optional
reasoning effort per job; the host performs each physical call; AAR persists bounded tickets,
receipts, and continuation state without provider credentials.

## Current capabilities

### Contract and transport

- strict Pydantic contracts with generated JSON Schemas;
- deterministic canonical JSON and content digests;
- direct Python APIs and a local-stdio MCP server;
- structured tool results with identities, generations, revisions, deadlines, grants, budgets, and
  idempotency keys;
- a canonical host-neutral operation skill copied into generated host profiles.

### Durable operation lifecycle

- accepted intent is persisted before execution;
- attempts, leases, events, cancellations, receipts, and recovery decisions survive frontend loss;
- stale generations and late writers fail closed;
- uncertain state-changing outcomes remain `indeterminate` until reconciled;
- one durable supervisor owns the runtime while replaceable MCP frontends attach through an
  owner-private authenticated transport.

### Programmable workspaces and RLM

- plain-Python and supervised IPython backends with generation and revision checks;
- JSON-subset checkpoint manifests with explicit exclusions;
- bounded persisted RLM jobs, typed broker contracts, retained child handles, and explicit result
  retrieval;
- immutable content-addressed assets and dependency-closed import/export;
- proposal-only external effects and host-owned materialization.

### Public plugin and caller-delegated RLM

The separate OAuth-authenticated Streamable HTTP profile exposes six tenant-private structured
workspace tools and five caller-delegated RLM tools. Each job has one host-selected executor, model,
and optional reasoning effort, exact pre-spend claim tickets, compare-and-set result commits, and
durable idempotency/restart/cancellation semantics. AAR does not receive provider credentials or
execute the public model call.

## Host support

| Host surface | Candidate support | Boundary |
|---|---|---|
| Direct Python | contract models, reference host, supervisor/frontend APIs | embedding host preserves identity and receipt semantics |
| Generic MCP | local stdio, 30 public tools | transport is not authority proof |
| ChatGPT / Codex public plugin candidate | remote OAuth profile, 11 curated tools, scoped skill | not production deployment, OpenAI approval, or publication |
| Codex | generated plugin/profile, setup helper, canonical operation skill | Codex owns approvals, credentials, and tool policy |
| Hermes | generated profile, ordinary MCP registration, durable supervisor integration | Hermes owns MCP Sampling and physical provider requests |

See [Host Compatibility](HOST-COMPATIBILITY.md) for commands and evidence boundaries.

## Verification state

The candidate has **62** required lifecycle repair rows. The status authority records all of the
following as `PENDING` until exact receipts are bound:

The planned evidence altitudes are T1 (the matrix), T2 (real component seams), and T3 (fresh-host
and release scenarios).

- local repair matrix completion;
- Windows full repository suite;
- supported-Python CI;
- exact wheel and isolated-install readback;
- fresh restarted host and caller-delegated Luna/max drill;
- independent release review.

Exact candidate commit, tree, and wheel fields remain null under the explicit
`post-freeze-external-receipt` binding mode. A local unit result, generated profile, or package scan
does not by itself establish a release claim. The previous `v0.4.0a5` counts and reports are
historical blocked evidence only.

## Publication and authority limits

Official Plugin Directory deployment, reviewer access, OpenAI review/approval/publication, and
provider-signed attestation are **not completed**. GitHub publication and local Codex installation
are independent later gates. The local setup route cannot authorize them and reports
`NO_ATOMIC_AUTHORITY` when the host provider lacks an opaque revision/CAS contract.

## Known limitations

- public alpha interfaces may change before a stable release;
- no security sandbox is provided for arbitrary programmable execution;
- package installation does not create an operating-system service or configure a host;
- MCP Sampling remains a deprecated compatibility path for hosts that support it;
- unresolved post-send provider outcomes may remain `indeterminate`;
- exactly-once provider execution is claimed only when a provider or owner receipt proves it;
- no managed AHC adapter is included;
- generated host profiles preserve the public contract but do not grant authority.

See [Development Roadmap](DEVELOPMENT-PLAN.md), [Architecture](ARCHITECTURE.md), [Testing](docs/TESTING.md),
and the [Changelog](CHANGELOG.md).
