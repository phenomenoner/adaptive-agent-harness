# Adaptive Agent Harness Architecture

Adaptive Agent Harness (AAR) is a contract-first runtime for bounded, durable agent operations. It separates computation from authority: AAR can execute, persist, reconcile, evaluate, and propose; the embedding host retains credentials, policy, external effects, activation, and final delivery.

> AAR computes and proposes. The host authorizes and delivers.

## System shape

```text
+-------------------- AI-agent host --------------------+
| identity · sessions · policy · credentials · delivery |
|                                                       |
| Codex / Hermes / direct host adapter                  |
+--------------------------+----------------------------+
                           |
                 MCP stdio or direct API
                           |
+--------------------------v----------------------------+
| Replaceable AAR frontend                              |
| schema validation · request mapping · bounded output  |
+--------------------------+----------------------------+
                           |
       authenticated owner-private supervisor protocol
                           |
+--------------------------v----------------------------+
| Durable AAR supervisor                                |
| operation registry · dispatcher · leases · receipts   |
| recovery decisions · broker routing · worker control  |
+--------+-----------------+-------------------+---------+
         |                 |                   |
+--------v-------+ +-------v--------+ +--------v---------+
| Workspace     | | RLM and broker | | Adaptive assets |
| workers       | | execution      | | and proposals   |
| Python/IPython| | model/child/...| | import/export   |
+---------------+ +-------+--------+ +------------------+
                          |
                  host-owned gateways
                          |
                 provider or effect owner
```

There is one semantic contract and several adapters. MCP calls, direct Python calls, and future native integrations must preserve the same identities, budgets, operation states, receipts, failures, and reconciliation rules.

## Authority boundary

AAR owns:

- contract validation and canonicalization;
- durable operation admission and lifecycle;
- bounded workspace and RLM execution;
- immutable adaptive assets;
- trace, receipt, usage, and uncertainty records;
- evaluation and proposal logic;
- deterministic candidate preparation.

The host owns:

- principal, lane, and session identity;
- grants, budgets, policy, and admission authority;
- provider credentials and physical provider calls;
- authoritative external effects;
- activation, promotion, rollback authorization, and final delivery.

Process co-location does not transfer authority. An MCP annotation, visible skill, successful model response, or prepared candidate is not proof that an effect was authorized or delivered.

## Public contract

The public narrow waist consists of:

- strict Pydantic models;
- generated JSON Schemas;
- deterministic canonical JSON and content digests;
- valid and invalid conformance fixtures;
- opaque typed references rather than host-internal objects;
- explicit capability, grant, budget, deadline, generation, revision, and idempotency fields;
- structured failures and explicit indeterminate outcomes.

Unknown fields fail closed. Version identifiers are independent by domain so one schema family can evolve without pretending the entire system changed atomically.

## Durable operation lifecycle

Every admitted operation binds:

- schema and capability versions;
- request and idempotency identity;
- host, principal, lane, and session references;
- runtime and relevant workspace generations;
- expected revision where applicable;
- deadline, grants, and budgets;
- input digest and trace relationship.

State-changing intent is persisted before acceptance is acknowledged. Reusing an idempotency key with the same input digest returns known state; reusing it with different input is a conflict.

The registry distinguishes:

- accepted but not yet claimed;
- running attempts with leases;
- succeeded, failed, and cancelled terminal states;
- indeterminate outcomes requiring reconciliation;
- quarantined evidence that cannot be trusted or replayed.

Transport loss is not failure evidence. If an authoritative outcome cannot be proven, AAR preserves uncertainty instead of inventing success, failure, or a safe retry.

## Supervisor and frontend lifecycle

One durable supervisor owns:

- runtime and dispatcher generations;
- operation and attempt claims;
- worker processes;
- leases and cancellation intent;
- predecessor recovery;
- broker journals and reconciliation decisions.

MCP frontends are replaceable clients of that supervisor. They attach through a signed discovery record and owner-private authenticated transport. A frontend exit does not imply operation cancellation, and a new frontend can read durable status and events.

The supervisor validates native process identity rather than trusting a PID alone. Stale discovery, duplicate ownership, late writes, generation drift, malformed private frames, and oversized payloads fail closed.

## MCP surface

`v0.3.0a2` exposes 30 local-stdio tools grouped by responsibility:

- capability and reference-context discovery;
- deterministic reference workspace lifecycle;
- programmable workspace lifecycle;
- immutable adaptive asset lifecycle;
- bounded RLM submission and status;
- broker catalog and method schemas;
- operation status, events, cancellation, and reconciliation;
- checkpoint capability description;
- bounded artifact resolution.

Large outputs return artifact references rather than unbounded inline payloads. Long-running operations return durable handles; callers inspect events and status instead of holding one transport open indefinitely.

MCP is an interoperability adapter, not the security authority. The server validates every call even if a host claims it has already approved the tool.

## Agent guidance

AAR ships three related layers:

1. **Tool schemas** define machine-validated inputs and outputs.
2. **Server instructions** provide concise cross-tool constraints.
3. **`aar-operations` skill** teaches the complete host-neutral workflow.

The canonical skill uses only public `aar_*` tools. Generated Codex and Hermes copies must be byte-identical; host packaging may explain installation but may not fork operation semantics or expand authority.

## Programmable workspaces

Workspace handles bind:

- workspace and session identity;
- backend kind and version;
- backend capability digest;
- runtime and workspace generations;
- revision;
- supported checkpoint formats and features.

The plain backend provides deterministic conformance behavior. The IPython backend provides persistent interactive Python with packaged NumPy and pandas support.

Checkpoint manifests contain portable JSON-subset values, explicit exclusions, source identity, environment fingerprint, artifact references, and content digests. Restore creates a new generation. AAR does not claim to serialize arbitrary modules, file handles, sockets, generators, native state, or process stacks.

A programmable workspace is not a security sandbox. Multi-tenant or hostile execution requires a separate host isolation boundary.

## Brokered RLM execution

RLM jobs are persisted state machines rather than hidden synchronous loops. They bind:

- strategy and session;
- outer operation and execution attempts;
- cumulative budgets and deadlines;
- typed broker grants;
- step inputs, outputs, artifacts, and traces;
- cancellation and recovery policy;
- authoritative receipts.

Broker domains remain distinct:

- model requests;
- retained subagents;
- proposal-only external effects;
- content-addressed artifacts;
- evidence retrieval.

A recovered job may reuse an authoritative committed receipt. An unresolved call remains indeterminate; it is not replayed merely because a frontend or worker disappeared.

## Receipt-backed model routing

The model broker uses a host-authored, digest-bound route catalog. An admitted route profile freezes:

- driver and provider;
- request model and reasoning effort;
- output-token bound;
- fallback policy;
- cache policy;
- provider metadata constraints.

Model-authored code cannot provide credentials, endpoint URLs, arbitrary model names, or alternate provider selectors.

The first owner gateway uses MCP Sampling as a bidirectional back-channel. The MCP client performs the physical provider call and returns `aar.model-receipt.v1` metadata. AAR compares requested and effective provider, request model, response model, reasoning effort, API mode, retry ordinal, fallback chain, and provider-reported usage before committing the result.

Under the strict `openai-codex / gpt-5.6-luna / max` profile:

- exactly one physical request is permitted;
- retry ordinal must be zero;
- fallback must be empty;
- provider usage must be present and internally consistent;
- route drift or missing receipt evidence fails closed.

A post-send timeout, cancellation race, disconnect, or ownership loss remains indeterminate unless an authoritative receipt resolves it. See [Receipt-backed model routing and fair evaluation](docs/MODEL-ROUTING-AND-EVALUATION.md).

## Adaptive assets and materialization

Adaptive assets are immutable, typed, and content-addressed. Manifests bind dependencies, schema versions, and body digests. Export is deterministic and dependency-closed; import validates the full bundle before mutation.

Outcomes are explicit. Missing outcome evidence remains unknown rather than being inferred from absence.

Materialization is prepare-only in AAR. It can create a deterministic candidate and rollback description, but activation remains a host-authorized operation outside the runtime.

## Failure and recovery principles

AAR uses several rules consistently:

1. **Persist intent before dispatch.**
2. **Fence writes by generation, attempt, lease, and revision.**
3. **Treat transport loss as uncertainty, not failure.**
4. **Reconcile before replay.**
5. **Reuse authoritative receipts; reject malformed or conflicting evidence.**
6. **Keep credentials and external authority outside AAR.**
7. **Return bounded artifacts and event pages.**
8. **Prefer explicit unsupported results over silent fallback.**

These rules preserve changeability: providers, hosts, transports, workers, and storage implementations may change without changing the meaning of an accepted operation.

## Deployment boundary

Installing the package supplies executables and generated profiles. It does not:

- create an operating-system service;
- choose a runtime-home directory;
- register a host automatically;
- configure provider credentials;
- authorize inference or external effects;
- activate a candidate;
- publish or deliver user-visible output.

A deployment is active only after the installed package identity, supervisor identity, runtime generation, fresh frontend connection, tool surface, and relevant receipts have been read back.

## Known architectural limits

- MCP Sampling is deprecated in protocol revision `2026-07-28`; a replacement host-owned broker transport is required.
- There is no portable provider lookup API for every post-send uncertainty.
- The reference host is an executable conformance implementation, not a multi-tenant security boundary.
- Exactly-once behavior is claimed only where authoritative state or receipts prove it.
- Managed-host admission, external-effect execution, activation, and final delivery require separate host integrations.

See [Technical Status](TECHNICAL-STATUS.md), [Host Compatibility](HOST-COMPATIBILITY.md), and the [Development Roadmap](DEVELOPMENT-PLAN.md) for the current public support boundary.