# Development Roadmap

Adaptive Agent Harness (AAR) is a public-alpha runtime for bounded, durable agent operations. The roadmap is organized by user-visible capability rather than internal completion codes or private verification history.

## Design commitments

Every roadmap item must preserve the same architectural boundary:

> AAR computes and proposes. The host authorizes and delivers.

That implies:

- versioned, language-neutral contracts remain the narrow waist;
- identities, grants, budgets, deadlines, generations, revisions, and idempotency are explicit;
- operation intent is persisted before dispatch;
- receipts and uncertainty survive frontend or worker loss;
- provider credentials, external effects, activation, and final delivery stay in the host;
- missing authority or evidence fails closed instead of being inferred.

## Available in `v0.3.0a2`

### Contract and MCP surface

- strict Pydantic models and generated JSON Schemas;
- deterministic canonical JSON and content digests;
- valid and invalid conformance fixtures;
- local-stdio MCP server with 30 public tools;
- canonical host-neutral `aar-operations` skill with generated host-profile copies;
- direct SDK and reference-host paths for embedding and conformance testing.

### Durable operations

- persisted operation state, events, attempts, leases, and recovery decisions;
- idempotent request admission with payload-digest conflict detection;
- cancellation, timeout, stale-generation fencing, and explicit indeterminate outcomes;
- durable supervisor with replaceable MCP frontends;
- process identity checks and owner-controlled private attachment transport;
- reconciliation before replay after uncertain state-changing work.

### Programmable workspaces

- plain-Python and IPython worker backends;
- create, attach, execute, inspect, interrupt, checkpoint, restore, health, reconcile, and close;
- portable JSON-subset checkpoints with explicit exclusions;
- new-generation restore rather than pretending to resurrect arbitrary process memory;
- packaged NumPy and pandas support for the default analysis environment.

### Brokered RLM and assets

- bounded persisted RLM jobs and step traces;
- typed model, subagent, effect, artifact, and evidence broker contracts;
- retained child handles and explicit result retrieval;
- content-addressed adaptive assets, dependency-closed export/import, and unknown outcomes;
- proposal-only external effects and host-owned materialization.

### Receipt-backed model routing

- digest-bound owner route catalogs and profiles;
- an explicit `openai-codex / gpt-5.6-luna / max` MCP Sampling profile;
- strict request/result receipt validation;
- provider-reported usage committed to the durable broker journal;
- no-retry/no-fallback qualification semantics;
- timeout, cancellation, disconnect, and post-send indeterminate classifications;
- reusable benchmark manifest and usage-ledger contract fragments.

See [Receipt-backed model routing and fair evaluation](docs/MODEL-ROUTING-AND-EVALUATION.md) for the exact public contract.

## Near-term priorities

### Replace deprecated MCP Sampling

MCP Sampling is deprecated in protocol revision `2026-07-28`. A future host-owned model broker transport should retain the same route and receipt invariants while removing dependence on that back-channel.

Acceptance requirements:

- the host still owns credentials and the physical provider call;
- the route profile remains digest-bound and deny-by-default;
- every physical attempt is counted;
- retries and fallbacks are either disabled or explicitly receipted;
- provider usage remains authoritative;
- post-send uncertainty remains indeterminate until reconciled.

### Expand provider and host adapters

Add adapters only when they preserve the core semantics rather than translating them away.

Priority work:

- provider-neutral receipt envelopes with driver-specific attribution;
- direct host broker adapters for environments that cannot support bidirectional MCP;
- additional fresh-process compatibility probes;
- Windows service guidance that does not embed machine-specific paths;
- remote transports with authentication, replay protection, bounded payloads, and explicit ownership.

### Improve portable recovery

Current recovery is deliberately bounded. Planned improvements include:

- richer portable workspace checkpoints;
- explicit environment compatibility policies;
- artifact-backed restoration of supported state;
- provider receipt lookup where an authoritative API exists;
- reusable reconciliation adapters for external effect owners.

The project will not promise arbitrary stack, socket, generator, native-process, or GPU-memory resurrection.

### Strengthen evaluation tooling

The public benchmark fragments define run manifests and request-level usage rows, but formal comparison remains an operator-owned process.

Planned work:

- neutral artifact materialization;
- call-census and attempt-receipt validators;
- cache-scope declarations;
- failed, cancelled, timed-out, retry, and wasted-token accounting;
- paired common-denominator tracks separated from native product-fit tracks;
- reproducible report generation without collapsing unlike architectures into one decorative score.

A formal run is admissible only when every contender proves the same effective route and accounts for every physical attempt.

## Longer-term work

### Shadow adaptation

- idempotent episode and outcome ingestion;
- frozen replay and evaluation sets;
- recommendation, abstention, and uncertainty reporting;
- zero serving writes during analysis and fault injection.

### Managed materialization

- deterministic candidate and rollback bundles;
- prepare, validate, request-activation, reconcile, and abort lifecycle;
- host-authorized activation only;
- no implicit deployment or delivery authority in AAR.

### Ecosystem hardening

- stable adapter interfaces after more than one independent host proves them;
- remote stores, strategy plugins, evaluators, and observability exporters;
- conformance kits for non-Python consumers;
- package-registry publication and compatibility policy;
- multi-tenant isolation only after a separate security design and threat model.

## Non-goals

The roadmap does not turn AAR into:

- a security sandbox;
- a credential store;
- an autonomous deployment controller;
- a universal exactly-once system;
- a hidden retry engine;
- a replacement for host policy;
- a claim that one agent architecture universally wins.

## Contributing

Choose one observable contract or behavior, add a falsifiable test or fixture, preserve the authority boundary, and document unsupported cases. See [CONTRIBUTING.md](CONTRIBUTING.md), [ARCHITECTURE.md](ARCHITECTURE.md), [HOST-COMPATIBILITY.md](HOST-COMPATIBILITY.md), and [docs/TESTING.md](docs/TESTING.md).
