# Adaptive Agent Runtime Architecture

**Status:** AR-0 through AR-3 locally verified; AR-4 and later managed-host layers remain gated
**Updated:** 2026-08-09

## 1. Architectural split

```text
Codex App      Hermes Agent       AHC
    |               |              |
    +------ MCP adapters ---------+|  public EXECUTE seam
                                    |
                            native AHC adapter
                                    |  stronger managed seam
                                    v
                    +---------------------------+
                    | AAR supervisor            |
                    | schemas, operations,      |
                    | grants, revisions, trace  |
                    +-------------+-------------+
                                  |
                         private framed IPC
                     +------------+------------+
                     |                         |
              workspace workers       adaptation workers
              plain Python/IPython     assets/eval/proposals
                     |
                     v
              typed broker proxy -> host-owned capabilities
```

There is one semantic contract and more than one adapter. MCP messages, direct SDK calls, and any later native AHC transport must map to the same request envelope, operation state, generation/revision checks, failure categories, and artifact references.

## 2. Why MCP is first but not everything

MCP is the practical common denominator for Codex App and Hermes. It provides a standard way to discover and call tools and, depending on the client/protocol era, structured results, progress, cancellation, resources, and long-running task mechanisms.

It does not prove the host's identity namespace, durable admission, budget authority, external effects, child ownership, serving activation, or final delivery. AAR therefore treats MCP as an adapter with an explicit capability ceiling.

`aar-codex-setup` is a host bootstrap adapter, not a second runtime contract. It locates the
marketplace bundled in the installed wheel, verifies the installed tool-environment `aar-mcp`
through one temporary programmable-worker dependency preflight, and installs the plugin as the
single MCP transport authority. A matching legacy global registration is removed; a different
global server with the same name fails closed for operator review. The bootstrap does not change
tool semantics, grants, activation, effects, or delivery authority. Full cross-version and
repository-wide verification stays outside the general-user installation path.

The current MCP specification has a modern per-request version/capability model, while earlier clients use an initialization handshake. AAR will measure target-client behavior and publish the exact supported matrix instead of binding core types to either era.

## 3. Runtime roles

| Role | Responsibility |
|---|---|
| Runtime supervisor | validate requests, own operation lifecycle, supervise workers, route brokers, reconcile uncertainty |
| Workspace backend | execute and inspect code within one workspace generation |
| MCP adapter | map MCP discovery/tools/resources/results to core operations without adding authority |
| Direct host adapter | embed or call the same operations without MCP-specific types |
| RLM engine | bounded model-program state machine and trace |
| Asset service | immutable revisions, manifests, canonicalization, migrations |
| Adaptation service | evidence ingest, evaluation, abstention, and shadow proposals |
| Materializer | deterministic candidate and rollback preparation |
| Runtime host | identity, grants, brokers, authoritative evidence, activation, and delivery |

Sharing a process never transfers authority between roles.

## 4. Operation model

Every accepted operation binds:

- AAR envelope and payload-schema versions;
- request id and idempotency key;
- opaque host, principal, lane, and session references;
- runtime and workspace generations;
- expected workspace revision where applicable;
- selected capability digest;
- deadline, grants, and budgets;
- trace id and parent operation;
- input digest and terminal classification.

State-changing intent is persisted before acceptance is acknowledged. Reusing an idempotency key with the same input digest returns the known state; a different digest is a conflict.

Transport loss produces `indeterminate` when the effect cannot be proven. Retry waits for reconciliation rather than guessing that work failed.

## 5. MCP surface

The AR-0 surface should remain deliberately small:

- `aar_capabilities`
- `aar_workspace_create`
- `aar_workspace_attach`
- `aar_workspace_execute`
- `aar_workspace_inspect`
- `aar_operation_status`
- `aar_operation_cancel`
- `aar_operation_reconcile`
- `aar_checkpoint_describe`
- `aar_artifact_resolve`

Names are provisional until schema fixtures freeze. Mutating tools carry explicit operation, generation, revision, deadline, and grant fields. Large outputs return artifact references. Long-running work returns an operation handle unless the negotiated client task mechanism is both supported and covered by conformance.

MCP annotations, instructions, client/server self-identification, and tool catalog state are untrusted metadata for authorization purposes. The adapter exposes only the capabilities permitted by the host profile and still validates each call.

### Operation guidance layers

AAR ships three related but distinct surfaces:

1. **Tool schemas:** machine-validated input/output contracts for each MCP operation.
2. **Server instructions:** a short, self-contained statement of cross-tool constraints, authority limits, and safe defaults.
3. **Bundled `aar-operations` skill:** the full agent-facing workflow for capability discovery, workspace lifecycle, long operations, cancellation/reconciliation, artifacts, and common failure recovery.

The skill does not grant access and cannot repair missing host capabilities. Host bundles may adapt installation metadata, but the canonical skill bytes or a declared semantic translation must be versioned and digest-bound to the same MCP surface. A visible or installed skill is configuration evidence only; compatibility requires an executed guided workflow.

## 6. Workspace boundary

`WorkspaceBackend` provides:

```text
create(spec) -> handle
attach(handle, expected_generation, expected_revision) -> handle
execute(handle, spec) -> event stream + result
inspect(handle, query) -> snapshot
interrupt(handle, operation) -> result
checkpoint(handle, policy) -> manifest
restore(checkpoint, spec) -> handle
health(handle) -> health
reconcile(handle, observed_revision) -> report
close(handle, reason) -> result
```

The first deterministic backend is plain Python. The production interactive backend is IPython in a separately supervised worker. A live namespace is scoped to one generation and is never the sole durability record.

Checkpoint formats are explicit and portable for a conservative supported-value subset. Arbitrary pickle data is not the portable recovery format.

The default package declares IPython, NumPy, and pandas so a fresh wheel installation provides the
same baseline analysis environment on every supported Python version. Availability does not make
NumPy arrays or pandas objects portable: callers must convert durable state to the declared JSON
subset, and checkpoint manifests report remaining live objects as exclusions.

## 7. Broker boundary

Model-written code can receive a typed facade such as:

```text
aar.model.request(...)
aar.agents.submit(...)
aar.agents.status(...)
aar.artifacts.put(...)
aar.artifacts.read(...)
aar.effects.propose(...)
aar.evidence.query(...)
aar.context.capabilities()
```

The facade contains no provider credentials or host objects. Calls carry the parent operation, grant, deadline, budget, and idempotency context. The host's receipt is authoritative.

Codex and Hermes initial profiles need not expose all brokers. Missing capabilities must remain unavailable and visible in compatibility metadata. AHC can expose stronger brokers once its integration gates pass.

## 8. Persistence

Initial persistence uses:

1. SQLite metadata in WAL mode for runtime generations, workspace/operation state, idempotency keys, checkpoint manifests, and reconciliation state.
2. Content-addressed artifacts for code units, bounded outputs, checkpoints, traces, assets, and later candidate/rollback bundles.

The metadata store does not contain arbitrary live Python objects. Each stored artifact has an explicit media type, size, digest, and compatibility metadata.

## 9. Security posture

- Model-written code is untrusted.
- Process isolation is containment, not authorization.
- Grants are deny-by-default and scoped.
- Provider credentials stay in the host.
- Filesystem, subprocess, network, native extensions, and dynamic imports require backend policy.
- Traces and artifacts have redaction and size limits.
- Deserialization validates media type, schema, size, and digest.
- A runtime crash cannot turn a pending effect into success.
- Unsupported enforcement fails or is reported; it never silently degrades.

## 10. Decision checkpoints

The following stay open until executable spikes produce evidence:

- exact Python build/package tool;
- MCP SDK choice and its modern/legacy era support;
- exact operation-skill packaging for Codex App and Hermes without duplicating workflow semantics;
- whether one server process owns one or many workspaces for each host profile;
- embedded IPython shell versus ipykernel-managed worker;
- Windows process containment and resource enforcement details;
- whether AHC requires a native transport for every managed operation or only a strict subset.

These are bounded implementation decisions, not reasons to weaken the stable schema or authority boundary.
