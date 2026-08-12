# Adaptive Agent Runtime

> **Public repository note (2026-08-12):** this file preserves detailed historical phase evidence.
> Generation-12/schema-v4 and “no release published” statements below describe their original
> checkpoints. The current delta immediately below, [`README.md`](README.md), and
> [`CHANGELOG.md`](CHANGELOG.md) define the present public surface.

## Current public delta — 2026-08-12

- complete standalone AR-LT3 source is verified at
  `aabbcfc76c9ba1b2e837fc4c0f01e743fa455479`;
- installed AAR remains package `0.3.0a0` but has completed the additive schema-v5 cutover and reads
  runtime/dispatcher generation `13 / 13` through one ready supervisor;
- `0.3.0a1` is the source-parity maintenance prerelease and does not replace the installed runtime;
- it adds the final compensation deadline-admission fence and regressions, refreshes package-bound
  generated assets, and preserves MCP v7 with 30 tools;
- public repository verification is `249 passed, 1 platform-gated skip`;
- managed AHC admission, provider credentials, generic exactly-once effects, activation, and final
  delivery remain outside the claim.

Adaptive Agent Runtime (AAR) is a Python-first programmable execution and adaptation runtime for agent harnesses.

The first product target is a small, useful MCP server that can be attached to more than one harness. Codex App and Hermes Agent are the first portability targets; Agent Harness Core (AHC) remains the first strong managed host.

## Current state

**Status:** AR-0 through AR-3 and standalone AR-LT0 through AR-LT2 are locally verified;
the first bounded AR-LT3 RLM recovery slice is installed and verified; no public package or release published
**Current phase:** AR-LT3 workspace restore and general effect reconciliation remain open
**Updated:** 2026-08-11 (exact LT3 wheel installed behind the host-managed supervisor)

AR-0 and AR-1 remain verified at `f3d1b913b479d7f8ef329c0bcc45ddd1f6e04395` and
`6aa8fc7f7598ebc77560defe1d31d30719a64339`. The AR-2/AR-3 executable candidate at
`c21d595` adds and hardens a bounded persisted RLM state machine,
transport-neutral typed broker contracts, deterministic benchmarks, immutable adaptive assets,
explicit lifecycle events, deterministic import/export and migrations, and a prepare-only
materializer seam. The full suite passes on Python 3.11 through 3.14; an isolated wheel and a fresh
Codex CLI process read back and exercised the v4 MCP surface. The existing Hermes AR-1 row was not
replayed for AR-2/AR-3, and AHC-native integration, shadow adaptation, managed materialization,
publication, and release remain separate gates.

The cross-cutting `AR-LT` workstream adds reconnectable durable async execution, restart-safe
dispatch, cursor-based events, successor attempts, and checkpoint-boundary recovery without binding
the core to MCP connection lifetime or host-specific identity. AR-LT0 through AR-LT2 are now
verified at the standalone boundary. AR-LT3-A/B now verify the initial policy-bound RLM
step-successor slice through the installed Hermes supervisor boundary; the full AR-LT3 phase remains open
and does not expand the verified AR-0 through AR-3 acceptance. Start with:

- [`docs/LONG-TASK-CONTINUITY-PLAN.md`](docs/LONG-TASK-CONTINUITY-PLAN.md) — architecture, failure boundaries, scope, and workstream gates;
- [`docs/AR-LT0-CONTRACT-AND-FAILURE-MODEL-PLAN.md`](docs/AR-LT0-CONTRACT-AND-FAILURE-MODEL-PLAN.md) — the sole current approval decision;
- [`docs/AR-LT1-DURABLE-ASYNC-DISPATCH-PLAN.md`](docs/AR-LT1-DURABLE-ASYNC-DISPATCH-PLAN.md);
- [`docs/AR-LT2-DURABLE-SUPERVISOR-PLAN.md`](docs/AR-LT2-DURABLE-SUPERVISOR-PLAN.md);
- [`docs/AR-LT3-CHECKPOINT-AND-EFFECT-RECOVERY-PLAN.md`](docs/AR-LT3-CHECKPOINT-AND-EFFECT-RECOVERY-PLAN.md).

The AR-LT1 candidate adds `aar.operation-continuity.v1`, an additive SQLite v2 migration, immutable
attempt and lease fencing, bounded operation-event pages, durable cancellation intent, a bounded
`rlm.execute` dispatcher, receipt-aware restart recovery, and a database-scoped live-owner lock.
Process-loss tests cover successor attempts, stale-writer rejection, persisted broker-receipt reuse,
indeterminate unresolved calls, and queued cancellation. The v6 MCP surface exposes 30 tools and
`aar-operations` `0.7.0`; exact package, host, rollback, and test evidence is recorded in
[`HOST-COMPATIBILITY.md`](HOST-COMPATIBILITY.md) and the append-only [`WAL.md`](WAL.md).

The AR-LT2 candidate adds an explicit `aar-supervisor` process, authenticated owner-only private
IPC, ephemeral `aar-mcp` frontends, PID-reuse-safe supervisor/worker identity, durable worker
bindings, additive SQLite v3 supervisor state, and predecessor-generation recovery. Package
`0.2.0a0` exposes `aar.mcp-tools.v7` with 30 tools and `aar-operations` `0.8.0`. The exact wheel is
installed for Hermes through an operator-selected Windows Scheduled Task that keeps the WSL
supervisor in the foreground; the package itself does not silently install a service. Linux/WSL uses
an owner-only Unix socket. The native Windows compatibility probe verifies the declared credentialed
loopback-TCP fallback; no named-pipe ACL claim is made.

The first bounded AR-LT3 candidate at `9b7a1d9c8f8aa64fbd43a42dbfd6a6cc8b25d23a`
adds an admission-time `rlm.step-boundary.v1` recovery policy, exact operation/attempt/generation/
lease/input/environment continuation bindings, additive SQLite schema v4, receipt-aware next-action
planning, cumulative budget/deadline/cancel preservation, and atomic successor admission. Unknown or
tampered policy/receipt evidence, stale fences, unresolved broker calls, and incompatible environments
park or quarantine rather than replay. Package `0.3.0a0` passed the full reference-host suite,
fresh Linux/Windows exact-wheel probes, v3-to-v4 migration rehearsal, and an installed Hermes
cutover. The serving supervisor now owns runtime/dispatcher generation 12 and schema v4; a fresh
native-session canary completed with a durable receipt, the policy binding was read back, and a
second owner failed closed without changing the generation. Workspace checkpoint selection/restore
and general effect reconciliation remain unverified AR-LT3 work packages.

The default runtime installation declares IPython, NumPy, and pandas as wheel dependencies. The
compatibility smoke imports them inside the supervised IPython worker and performs a DataFrame
calculation. NumPy arrays, DataFrames, modules, and other live objects remain workspace-local and
are excluded from portable JSON-subset checkpoints unless the caller converts them explicitly.

Normal Codex App installation is intentionally two commands:

```powershell
uv tool install --force adaptive-agent-runtime
aar-codex-setup
```

For an unreleased/local build, give `uv tool install --force` the exact wheel path instead. The
setup command runs an approximately single-digit-second real-MCP dependency preflight, installs the
bundled Codex plugin as the sole owner of its MCP transport, and removes a matching legacy global
`aar` registration that would create a second authority. It refuses to remove a conflicting global
server with different bytes or configuration. Repeated setup is idempotent, and the receipt requests
an App restart only when configuration changed. After an initial or changed setup, restart the App
and call `aar_capabilities` in a fresh task. The Python 3.11-3.14
matrix and full repository suite are maintainer gates, not general-user installation steps. See
[`docs/CODEX-INSTALL.md`](docs/CODEX-INSTALL.md). Maintainers should use the claim-driven cadence in
[`docs/TESTING.md`](docs/TESTING.md) instead of replaying every historical gate after each edit.

On Windows, close an existing Codex App task that is already using AAR before upgrading the uv
tool; the running MCP process can otherwise keep the old console entrypoint locked.

## AR-0A contract evidence

- Supported Python range: 3.11 through 3.14; focused contract tests passed in isolated 3.11,
  3.13, and 3.14 environments and the local 3.12 project environment.
- Package: `adaptive-agent-runtime` `0.1.0a0`, standard `pyproject.toml`, Hatchling build,
  uv lockfile, `src/` layout.
- Schema versions: `aar.envelope.v1`, `aar.runtime.v1`, `aar.workspace.v1`,
  `aar.artifact.v1`, and `aar.broker.v1`.
- Schema bundle digest: `sha256:e45f45a5c2df8dfaf4561bcc59410b4a35c51775a9f713211eee6e655e35fa5f`.
- Fixture set digest: `sha256:18f42e30c9119bd82be81eebe7d5163ef67509e22945168e257dfa1500b91835`.
- Verification entrypoint: `uv run --locked aar-contract verify`.

## AR-0B reference-host evidence

- Executable candidate: `a04ab92f5900c59e7dd8f4ec1e268369488aee5f`.
- SQLite/WAL operation truth persists accepted intent, scoped idempotency, state transitions,
  progress events, generations, uncertainty, and reconciliation requirements.
- The deterministic workspace fake commits state and its operation receipt atomically and rejects
  stale generation, stale revision, and cross-session access.
- Credential-free Model, Subagent, Effect-proposal, Artifact, and Evidence fakes expose retained
  handles and deterministic receipts. The effect fake has no execute or delivery path.
- The full 28-test suite passed under Python 3.11, 3.12, 3.13, and 3.14. Separate child-process
  probes proved restart reconciliation after both committed uncertainty and abrupt process loss.

## AR-0C MCP and operation-skill evidence

- Executable candidate: `7656baa23d35086b1ecb20df50c3fdebf7e92ebd`.
- Package dependency: `mcp==2.0.0`; the same stdio server negotiated modern `2026-07-28` and
  legacy `2025-11-25` clients without changing the AAR operation semantics.
- Ten deterministic tools cover capabilities, workspace create/attach/execute/inspect, operation
  status/cancel/reconcile, checkpoint metadata, and bounded artifact resolution.
- Tool-surface digest:
  `sha256:a6d5723fdddde62683d3908dc68161f193c612e7e7ac18081482296438db0e14`.
- `aar-operations` version `0.1.0` digest:
  `sha256:ce5dc48d883baec0e42220031f502b13515957a545273910d7f79ab5ad5fbfa7`.
- Structured output and JSON text fallback, deterministic reference grants, stale/runtime/revision
  failures, cancellation, deadlines, restart uncertainty, reconciliation, unsupported checkpoints,
  and artifact disclosure limits are exercised through MCP.
- The full 38-test suite passed under Python 3.11, 3.12, 3.13, and 3.14. A wheel installed in a
  fresh Python 3.14 environment read back the bundled skill and tool-surface digests.

## AR-0D portability evidence

- Executable candidate: `f3d1b913b479d7f8ef329c0bcc45ddd1f6e04395`.
- Current tool-surface digest:
  `sha256:5b503d1b0600d70a8d1d3f7f8995378533694279dc8b233c7c9342e96d075e5d`.
- Current `aar-operations` version `0.2.2` digest:
  `sha256:8ef484155b31022293596dfb5825aaa66e03d5ac9b82dcaa89226b1d3e54fa68`.
- The server reports the request-context `negotiated_protocol_version` and declares every revision
  served by `mcp==2.0.0`: `2026-07-28`, `2025-11-25`, `2025-06-18`, `2025-03-26`, and
  `2024-11-05`.
- A fresh `codex-cli 0.146.0` process negotiated `2025-06-18`; an isolated Hermes Agent `0.19.0`
  CLI profile negotiated `2025-11-25`. Both loaded the same skill bytes and completed capability,
  create, execute, artifact resolve, inspect, status, cancellation, stale-revision, and unsupported
  checkpoint scenarios through real MCP tools.
- The full 44-test suite passed under Python 3.11, 3.12, 3.13, and 3.14. Generated MCP assets,
  host profiles, Codex plugin structure, source distribution, and wheel were independently read
  back. Exact host receipts and limitations are recorded in `HOST-COMPATIBILITY.md` and `WAL.md`.

## AR-1 programmable-workspace evidence

- Executable candidate: `6aa8fc7f7598ebc77560defe1d31d30719a64339`.
- Complete programmable handles bind workspace identity, backend kind/version/capability digest,
  checkpoint formats and features, generation, and revision. Foreign, stale, cross-session, and
  partially reconstructed handles fail closed.
- Portable checkpoint manifests bind the source handle, checkpoint operation, trace, environment
  fingerprint, supported values, exclusions, artifact references, and content digest. Restore
  creates a new generation at revision zero; checkpoint creation records an operation without
  advancing the workspace revision.
- Worker loss and runtime restart do not manufacture terminal truth. Missing programmable receipts
  remain indeterminate and reconcile as `PROGRAM_RECEIPT_UNAVAILABLE_AFTER_RESTART`.
- MCP tool surface `aar.mcp-tools.v2` exposes 20 tools with digest
  `sha256:71a42a4b8dd554c717bfd0a5e36f601234da3016d6fa96138548c2291747d44b`.
  `aar-operations` `0.3.1` has digest
  `sha256:bc83747e71ec7ae9e9bbf8c35661bfad770f282e035fae8ffa675f91bc5ca7d3`.
- The full 64-test suite passed under Python 3.11, 3.12, 3.13, and 3.14. Ruff, MCP asset,
  host-profile, skill, and wheel checks passed. A fresh Codex CLI process and an isolated Hermes
  one-shot process each executed `answer=42`, reconciled it, checkpointed, restored generation 2,
  read ready health, and closed through real MCP tools. Exact receipts and host limitations are in
  `HOST-COMPATIBILITY.md` and `WAL.md`.
- The default supervised analysis environment is installed from the package metadata rather than
  inherited accidentally from the operator machine: IPython, NumPy, and pandas are required
  runtime dependencies and are exercised by `aar-compat-smoke`.

## AR-2 portable-RLM evidence

- Executable candidate: `c21d595`; package and fresh-Codex probes pass. The independent closure
  wave `9c9e7a87...19df` finished `PASS / AUDITED_BATCH_COMPLETE` with no unresolved challenge.
- Persisted RLM jobs bind strategy, session-scoped outer operation, budgets, grants, broker calls,
  retained children, artifacts, evidence, trace steps, terminal result, uncertainty, and
  reconciliation. The current portable RLM is workspace-independent; it does not implicitly bind or
  execute inside a programmable workspace.
- Typed Model, Subagent, Effect-proposal, Artifact, and Evidence contracts are separate from the
  reference broker implementation and contain no provider credentials or final-delivery path.
- Abrupt process loss preserves accepted intent. A missing RLM row is reported without mutation by
  status and may be reconstructed only by mutation-authorized reconciliation; missing completion
  evidence remains indeterminate.
- The deterministic benchmark pack compares an evidence-heavy RLM strategy with a non-RLM baseline
  and reports quality, latency, cost, uncertainty, and safety bounds.
- A transport-neutral AHC fixture exercises the shared broker and operation boundary. It is not an
  AHC adapter and does not make AAR core depend on AHC.

## AR-3 canonical-asset evidence

- Agent fingerprints, episodes, outcomes, evaluations, and proposals are immutable typed documents
  backed by content-addressed bodies and manifests.
- Selection, disclosure, use, outcome, and attribution are explicit events. An absent outcome is
  represented as unknown rather than inferred success or failure.
- Export is deterministic and dependency-closed. Import validates the complete bundle before one
  transaction, rejects missing or kind-conflicting references, and is stable under replay.
- Detached migrations must be deterministic; the registry runs a migration twice and fails closed
  when its output differs.
- The materializer surface only prepares a bounded preview. AAR exposes no activation, serving
  mutation, provider credential, external effect execution, or final-delivery operation.
- MCP surface `aar.mcp-tools.v5` exposes 29 tools with digest
  `sha256:1ba64cb622ee9c75f3302c9d90cdabcb08804c56bd0d9b9138080b8772911a2c`.
  Schema and fixture digests are
  `sha256:6e2aee5ba92c07fb18b7cb074712679617d64b2c895ebf4fbd167f3f31f112ab` and
  `sha256:5d07a6dac808b44339bfd93a37257fb74ea575a64b40b6c1cda26cb0e6add1e4`.
  `aar-operations` `0.6.1` has digest
  `sha256:f4c47ebee6e114de990d2be6339698584809c10d1ab1b10bf799f69c1bc5c045`.
- The full 113-test suite passed under Python 3.11, 3.12, 3.13, and 3.14. Refreshed package and fresh-host
  probes pass; exact final-wheel and host
  evidence, including observed failed correction calls, is recorded in `HOST-COMPATIBILITY.md` and
  `WAL.md`.

CodeGraph integration remains a separate optional skill-guided workflow for organizing and
navigating AAR IPython artifacts. It is not bundled into `aar-operations` or AAR core. NOOA is used
only as design input; AAR contains no NOOA adapter, backend, or dependency.

## Product boundary

AAR owns:

- programmable workspace execution;
- bounded RLM jobs and traces;
- immutable adaptive assets and revisions;
- evaluation, proposal, and materialization logic;
- transport-neutral operation, broker, artifact, and evidence contracts.

The host owns:

- principal and session identity;
- admission, grants, budgets, and policy;
- provider credentials and authoritative effects;
- durable child-work scheduling;
- activation, promotion, rollback authorization, and final delivery.

The durable rule is:

> AAR computes and proposes. The host authorizes and delivers.

## MCP-first means

MCP is the first public integration surface, not the security authority and not the entire internal architecture.

- `aar-mcp` belongs in AR-0 instead of being deferred to ecosystem expansion.
- The initial MCP surface is local stdio and exposes bounded workspace lifecycle, status, cancellation, reconciliation, and artifact-reference operations.
- MCP tool calls map to the same transport-neutral AAR schemas and operation state machine used by other adapters.
- Supervisor-to-worker IPC may use framed JSON internally; it is not a second public harness contract.
- AHC may later use a native adapter for stronger lifecycle and authority integration, but it must preserve the same core semantics and conformance fixtures.
- Unsupported host capabilities are declared and rejected explicitly. A successful tool call never implies durable admission, effect authority, or final delivery.

The initial MCP compatibility spike must measure the actual protocol versions supported by each target client. The implementation will not assume that Codex App, Hermes, and the current MCP specification advance in lockstep.

## First adopter profiles

| Host | Initial mode | Transport target | Initial authority ceiling |
|---|---|---|---|
| Reference host | EXECUTE | direct SDK and MCP stdio | deterministic local fakes only |
| Codex App | EXECUTE | MCP stdio first | client approvals plus AAR-local workspace policy; no inferred durable host authority |
| Hermes Agent | EXECUTE | MCP stdio first | declared Hermes MCP/tool policy; no inferred durable host authority |
| AHC | EXECUTE, then SHADOW/MANAGED through later gates | shared schemas plus native adapter; optional MCP parity | AHC-owned identity, admission, budgets, effects, evidence, activation, and delivery |

Codex App and Hermes are portability rows, not weaker substitutes for AHC. Their adapters must not invent callbacks or guarantees that the host does not expose.

## Development order

1. **AR-0A — repository and protocol baseline:** package boundaries, version policy, canonical schemas, fixtures, and deterministic fake brokers.
2. **AR-0B — reference host:** end-to-end request lifecycle without AHC.
3. **AR-0C — MCP vertical slice:** discover/list/call, structured results, operation handles, status, cancel, reconcile, artifact references, and the canonical bundled `aar-operations` skill over stdio.
4. **AR-0D — portability proof:** package the same operation skill with the Codex App and Hermes integrations, run the guided MCP surface, and publish exact compatibility rows.
5. **AR-1 — programmable workspace:** plain-Python conformance backend plus supervised IPython backend, checkpoint/restore, and generation fencing.
6. **AR-2 — portable RLM:** brokered model/child/artifact/evidence calls with bounded budgets and no direct final delivery.
7. **AR-3 — canonical assets:** immutable adaptive records, explicit lifecycle events,
   deterministic bundles and migrations, and a prepare-only materializer seam.
8. **AR-4+ — shadow adaptation, managed materialization, and ecosystem hardening.**
9. **AHC integration gates:** begin the Rust contract boundary from the transport-neutral fixtures;
   stronger managed behavior remains dependency-gated.

See [DEVELOPMENT-PLAN.md](DEVELOPMENT-PLAN.md), [ARCHITECTURE.md](ARCHITECTURE.md), and [HOST-COMPATIBILITY.md](HOST-COMPATIBILITY.md).

## Definition of the first useful release

The first useful release is not “an MCP process starts.” It requires:

- one pinned AAR schema and fixture digest;
- a reference host that passes valid and invalid lifecycle cases;
- an MCP stdio server whose advertised tools and structured results are deterministic;
- a versioned `aar-operations` skill whose exact bytes/digest match the advertised MCP surface and whose server instructions provide the short cross-tool constraints;
- create/execute/inspect/status/cancel/reconcile behavior with explicit generations and revisions;
- exact artifact references instead of unbounded inline payloads;
- a fresh-process Codex App readback and a fresh-process Hermes readback;
- an explicit unsupported-capability list for each host;
- no AHC imports, provider credentials, direct effects, activation path, or final-delivery path in AAR core.

## Evidence policy

Repository setup, documentation, skill presence, config presence, or tool catalog visibility are not runtime compatibility evidence. Compatibility claims must bind the AAR commit, package version, schema digest, operation-skill version/digest, host/client version, transport, exact command or interaction, and meaningful result.

Status values are limited to `planned`, `in-progress`, `implemented-unverified`, `verified`, `blocked`, and `superseded`.
