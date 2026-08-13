# AR-LT2 — Durable Supervisor and Restart Recovery Implementation Plan

**Status:** verified locally and installed for Hermes; AR-LT3 handoff open
**Updated:** 2026-08-11
**Owning workstream:** [LONG-TASK-CONTINUITY-PLAN.md](LONG-TASK-CONTINUITY-PLAN.md)
**Cross-project coordination:** maintained in the separate AHC-by-AAR planning workspace; not normative to AAR core

## 1. Objective

Separate durable operation ownership from ephemeral MCP/direct frontend processes. A frontend may exit and reconnect while a host-managed AAR supervisor continues dispatching; a supervisor restart uses persisted attempts, leases, receipts, and recovery policy to create a classified successor or park the work.

AR-LT2 does not install an always-on operating-system service by default. It provides a host-manageable service process and private attachment contract. Packaging and service installation remain explicit deployment choices.

## 2. Target process model

```text
Codex/Hermes/AHC/reference host
          |
          | MCP stdio or direct SDK
          v
+------------------------------+
| ephemeral adapter/frontend   |
| validate transport mapping   |
| no operation ownership       |
+--------------+---------------+
               | authenticated private IPC
               v
+------------------------------+
| durable AAR supervisor       |
| registry / dispatcher /      |
| leases / brokers / events    |
+----------+-------------------+
           |
           +--> IPython workers
           +--> bounded RLM execution
           +--> adaptation workers later
```

Only the supervisor advances runtime and dispatcher generations, owns leases, starts workers, and commits operation transitions. A frontend closure never calls supervisor shutdown.

## 3. Supervisor lifecycle

```text
starting -> migrating -> recovering -> ready -> draining -> stopped
                          |             |
                          +-> degraded  +-> reconcile-required
```

Startup gates:

1. exclusive instance ownership for one database/runtime home;
2. schema-version and capability-digest validation;
3. durable runtime generation allocation;
4. discovery-record publication only after validation;
5. prior-attempt/lease scan and recovery classification;
6. worker and broker readiness;
7. Ready receipt with exact process identity and generation;
8. dispatcher claims enabled.

Shutdown gates:

1. stop accepting new attachments/claims;
2. persist drain state;
3. request handler yield/cancel under policy;
4. fence or classify unresolved attempts;
5. close workers and broker resources;
6. remove discovery record only if still owned by this exact process identity;
7. write terminal supervisor receipt.

## 4. Private attachment contract

Define a transport-neutral `SupervisorTransport` interface with platform implementations:

- POSIX Unix-domain socket with owner-only permissions and peer checks;
- Windows named pipe with explicit owner ACL;
- loopback TCP only as a declared fallback with a host-owned ephemeral attachment credential.

The discovery record contains:

- schema version;
- endpoint kind and opaque endpoint reference;
- supervisor PID and native process-start identity;
- runtime and dispatcher generation;
- capability digest;
- database/runtime-home digest;
- ready timestamp and discovery-record digest.

It contains no provider credentials, raw command line, workspace contents, channel identity, or reusable cross-host secret.

Every private frame binds:

- protocol version and message kind;
- request and trace IDs;
- runtime/dispatcher generation;
- principal/session authority digest from the adapter;
- payload length and digest;
- deadline;
- attachment credential or peer identity when the transport requires one.

Stale discovery records, PID reuse, endpoint replacement, generation mismatch, and capability drift fail closed.

## 5. Attach-or-start behavior

The host adapter, not AAR core, chooses policy:

- attach to an exact ready supervisor;
- start a foreground/host-supervised supervisor and wait for Ready;
- refuse because policy requires an already managed service;
- operate without AAR.

AAR must not daemonize itself invisibly from an MCP stdio request. Codex/Hermes packages may provide a documented host-managed launcher, while AHC will eventually own its Rust-side supervision policy.

## 6. Restart recovery algorithm

For each nonterminal logical operation:

1. load the latest attempt, lease, control state, broker journal, terminal receipts, and checkpoint bindings;
2. reject any old owner still claiming authority after generation change;
3. classify whether a live old worker can be proven and fenced;
4. determine the last certain durable boundary;
5. apply the operation kind's versioned recovery policy;
6. persist a recovery decision before acting;
7. either commit an already durable terminal receipt, create a successor attempt, restore a checkpoint, request effect reconciliation, park needs-user, or quarantine;
8. emit events linking the prior attempt and decision.

A supervisor restart never rewrites an old attempt as if it continued. Native reconnect, where a backend truly supports it, still creates a new supervisor-side ownership generation and records the native handle used.

## 7. Worker ownership

Introduce a `WorkerManager` that owns:

- worker process group/job object;
- PID plus native process-start identity;
- operation/attempt and workspace generation binding;
- capability/environment digest;
- heartbeat and last event sequence;
- termination escalation and receipt;
- optional native recovery handle.

The in-memory `_states`, `_running`, and `_receipts` maps in the current plain/IPython backends remain caches, not authority. Durable bindings and receipts live in the registry/content store.

The first candidate may keep one IPython worker per workspace generation. A worker that outlives its owner fence must be terminated or quarantined before a successor is allowed to write.

## 8. Adapter behavior

### MCP stdio frontend

- constructs or attaches through the private client;
- maps validated MCP contexts to host-neutral request envelopes;
- returns operation handles and bounded pages;
- exits without draining the supervisor;
- never exposes private endpoint/attachment credentials through MCP results.

### Direct Python client

- uses the same private client and schemas;
- may be embedded by a host that owns supervisor lifecycle;
- cannot bypass grants, generations, or operation state.

### Future remote/HTTP frontend

Deferred. If added, it maps into the same supervisor API and requires explicit authentication, disclosure limits, and host policy. It is not needed for AR-LT2 verification.

## 9. Recovery classifications

| Observation | Decision |
|---|---|
| accepted, no attempt | claim normally |
| attempt claimed, no broker/effect start | start successor |
| durable terminal receipt exists | commit terminal from receipt |
| broker request has authoritative receipt | reuse receipt and continue from next safe boundary |
| broker/effect started, no receipt | reconcile effect or needs-user |
| worker proven live and native reconnect supported | resume native under new ownership generation |
| worker identity unknown or PID reused | fence/quarantine; do not attach |
| compatible checkpoint exists | AR-LT3 may restore; before LT3 park with checkpoint available |
| cancellation requested | recover toward cancelled; no new effect work |

## 10. Source impact map

Expected new modules:

- `src/aar/runtime/supervisor.py`;
- `src/aar/runtime/supervisor_client.py`;
- `src/aar/runtime/supervisor_protocol.py`;
- `src/aar/runtime/worker_manager.py`;
- platform transport modules under `src/aar/runtime/transports/`;
- CLI entrypoint for an explicit `aar-supervisor` process.

Expected modified modules:

- `src/aar/runtime/reference_host.py` — split service ownership from frontend facade;
- `src/aar/runtime/dispatcher.py` and registry;
- `src/aar/runtime/ipython_backend.py`, `programming.py`, and `ipython_worker.py`;
- `src/aar/mcp/server.py` — remote facade rather than owner;
- package data, `pyproject.toml`, Codex/Hermes profiles, install docs, compatibility smoke.

Expected tests:

- new supervisor protocol/lifecycle tests;
- real subprocess frontend-exit and supervisor-restart tests;
- Windows named-pipe or declared fallback transport tests;
- PID-reuse/stale-discovery/generation-fence tests;
- existing package/Codex/Hermes compatibility suites.

## 11. Ordered work packages

### LT2-A — supervisor protocol and fake transport

Freeze private frame schemas, lifecycle receipts, exact attachment failures, and a deterministic in-process fake.

### LT2-B — foreground supervisor CLI

Start one supervisor against a runtime home, publish Ready, accept private requests, drain, and close without MCP.

### LT2-C — stdio frontend split

Make `aar-mcp` attach to the supervisor. Prove killing the frontend leaves a durable RLM job running and a fresh frontend can read it.

### LT2-D — process ownership and discovery

Implement exact process-start identity, exclusive runtime-home lock, stale discovery cleanup, PID-reuse falsifiers, and generation fencing.

### LT2-E — worker manager

Move worker lifecycle receipts and authoritative bindings out of backend-local maps. Preserve backend-neutral interfaces.

### LT2-F — supervisor restart recovery

Fault-inject every durable boundary, persist recovery decisions, create safe successor attempts, and park uncertain effects.

### LT2-G — platform transports

Verify POSIX and Windows paths with the same frame fixtures. Unsupported transport/security enforcement fails visibly.

### LT2-H — packaging and host profiles

Ship explicit supervisor/attach commands, no silent service install, uninstall/rollback instructions, and fresh host compatibility rows.

## 12. Required scenario matrix

1. close MCP stdin while a deterministic RLM job runs; job completes;
2. reconnect through a fresh MCP process and read events/result;
3. kill frontend during cancel request; cancellation remains durable;
4. kill supervisor before claim, after claim, during broker call, after broker receipt, and before terminal projection;
5. stale old supervisor cannot heartbeat or commit after successor generation starts;
6. duplicate supervisor startup for the same runtime home fails before Ready;
7. stale discovery record and PID reuse are rejected;
8. kill worker while supervisor remains; failure is classified and fenced;
9. supervisor restart with a live orphan worker never guesses ownership;
10. unrelated runtime home/database cannot attach;
11. unsupported Windows ACL or POSIX permission enforcement fails closed;
12. ordinary no-AAR host workflow remains unchanged;
13. package uninstall removes launch metadata without deleting retained operation evidence;
14. downgrade refuses a newer unsupported database rather than corrupting it.

## 13. Exit criteria

AR-LT2 is verified only when:

- ephemeral frontend loss does not stop an admitted durable job;
- fresh frontend attachment can observe and control under valid authority;
- supervisor restart produces a durable decision for every nonterminal operation;
- stale processes and late receipts are fenced by exact generation/lease identity;
- private transport authentication/ACL behavior is verified on declared platforms;
- no hidden daemon or OS service is installed;
- package and compatibility rows include supervisor version/digest and unsupported behaviors;
- AAR absent or stopped remains a supported host mode;
- no arbitrary workspace/process-memory resurrection claim is made.

### Executed closeout — 2026-08-11

AR-LT2 is verified for the standalone reference host and the installed Hermes profile at source
commit `38f338255f38b266f0cbe4d87db6f659274d80c6`.

Bound package and protocol identities:

- package `adaptive-agent-runtime==0.2.0a0`;
- immutable wheel SHA-256
  `e867c69c82139d567303591a8c94a204d9b779366e6e22927ad184ae6a6ad624`;
- MCP surface `aar.mcp-tools.v7`, 30 tools, digest
  `sha256:370d8a3177e80e94523c07537fc6b3107beacd52956add61b0e31b9d32bf2555`;
- private supervisor protocol `aar.supervisor.protocol.v1`, digest
  `sha256:2ec0e07c390041517632aee6d2962bec55cd974fd31c2a99284ff5e45a2196c8`;
- schema bundle
  `sha256:8f8e456e3af25d66063469e3b4722ccf7768b0b1b2d0c37ca897002c5385ef22`;
- operation skill `0.8.0`, digest
  `sha256:b48d014ca89809a1d710a5208036e6df6b3f348a9f86c42586989aaff60fb9c9`.

Executed evidence:

- Ruff passed for `src` and `tests`; the affected LT2 suite passed 99 tests with one
  platform-guarded Windows identity test skipped before the later native Windows probe;
- the repository run completed with 184 passes, one platform skip, and one stale generated contract
  bundle; regenerating the contract/MCP/host assets removed that only failure and the exact affected
  39-test shard passed;
- three real-subprocess scenarios passed: concurrent/fresh frontend attachment, hard supervisor loss
  with predecessor and orphan-worker reconciliation, and durable RLM completion across frontend exit;
- an isolated Linux exact-wheel environment attached through the Unix socket and read a durable RLM
  terminal result from a fresh frontend;
- a native Windows 3.13 exact-wheel probe exercised `GetProcessTimes` identity, the credentialed
  `127.0.0.1` TCP fallback, invalid-credential rejection, and the v7 30-tool surface;
- the installed Hermes profile connected through a fresh `hermes mcp test aar` process and the
  current native MCP session reported `attached-supervisor`, runtime/dispatcher generation 10;
- an installed durable RLM operation was admitted as `accepted` and read through a separate fresh
  frontend as `succeeded`;
- the live supervisor and frontend have different PIDs; the private directory is mode `0700`, the
  discovery record, attachment credential, and Unix socket are `0600`, registry schema v3 is active,
  and no nonterminal operation remained after verification.

The package does not silently install a service. This installation uses an operator-selected,
least-privilege Windows Scheduled Task to keep the WSL supervisor in the foreground. The task is a
Hermes deployment choice, not a portable AAR requirement. The prior LT1 uv-tool tree and pre-v3
SQLite database remain recoverable in operator-local rollback evidence that is not tracked here.

Boundaries remain explicit: the declared Windows path is credentialed loopback TCP, not a named-pipe
ACL implementation; remote HTTP is deferred; arbitrary process-memory resurrection, external effect
execution, provider credentials, activation, final delivery, AHC admission, push, and publication are
not AR-LT2 claims.

## 14. Rollback

- Freeze new claims and drain/fence attempts before replacing a supervisor candidate.
- Retain the prior executable/package and exact database schema support matrix.
- Use a copied runtime home for candidate tests; do not downgrade in-place after new schema writes.
- Remove only task-created discovery records whose exact process identity is absent.
- Never kill a process based solely on PID without native process-start identity.
- A failed candidate leaves accepted work visible as accepted/parked/indeterminate for explicit recovery.

## 15. AHC coordination boundary

HC-R0's compiled service host and accepted-work recovery remain independent and authoritative for AHC work. AR-LT2 borrows the generic ideas of process identity, generation fencing, recovery decisions, and successor attempts; it does not import AHC domain types or delivery semantics.

After HC-1 exists, AHC may supervise the AAR supervisor as an optional dependency. IG-1 continuity evidence must prove:

- exact AHC task to AAR operation binding;
- AAR process and attempt generations are subordinate to AHC owner generation;
- AAR/frontend loss cannot corrupt the AHC queue or create final delivery;
- optional AAR loss degrades without blocking ordinary work;
- required-on-use AAR failure occurs before the affected task is admitted.
