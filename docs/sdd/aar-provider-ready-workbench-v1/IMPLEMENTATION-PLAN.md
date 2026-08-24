# Implementation plan — clean-install provider-ready rev2

**Authority state:** this is a plan, not product-code authorization. No implementation, installation, provider call, stage, commit, push, service start, or runtime mutation is claimed.

## 1. Entry and worktree rule

Implementation may begin only after the rev2 specification receipt is final and the exact frozen v6/v7/v8/D1/D2 inputs are rebound. The three pre-existing dirty cutover files may be replaced in this same worktree by bounded patch; a separate clean worktree is not required. Record their task-start hashes, retire only the superseded cutover seams, preserve unrelated read-only behavior, and do not use reset, stash, clean, or checkout.

Do **not** force the clean installer back into the 2,785-line legacy `runtime.operator` monolith. New behavior is owned by focused modules and tests: strict install/receipt models; `aar.runtime.installer` for target/receipt/SQLite/staging/publication; `aar.runtime.provider_ready_activation` and `ProviderReadyActivationStore` for C2; focused installer/activation tests; and additive generated receipt schema/fixtures. `aar-admin` is a thin dispatch adapter and `runtime.operator` remains read-only. Supervisor, `ReferenceHost`, and MCP may receive only one explicit optional activation injection required to run C2 after runtime-generation allocation. Any wider registry, migration, transport, broker, daemon, or system-seam change requires a separate scope checkpoint.

This documentation task does not write those product paths.

## 2. Non-goals and fixed seams

Do not add a second workbench, broker, provider client, credential store, daemon, database, schema v7, transition mutator, adoption path, downgrade path, old-root product mutation, or durable recovery ledger. The planned new receipt schema and clean-install integration fixtures are additive future implementation assets. The existing 14 schemas and 64 fixtures remain byte-identical; do not regenerate them.

The public mutation is fixed, with no unresolved locator:

```text
aar-admin runtime install --runtime-home <absolute-runtime-home> --intent <exact-intent> --candidate-receipt <absolute-json> --wheel <absolute-wheel>
```

The receipt schema is exactly `aar.install-candidate-receipt.v1`. Absolute receipt/wheel files are opened once no-follow, retained, and read/hash-verified from those same descriptors. Fixed wheel members, factory entries, implementation digests, receipt self-digest, and installed-member startup checks follow `CONTRACTS.md`.

## 3. Ordered lanes

### Lane A — identity, strict receipt, and frozen assets

Implement the exact target algorithm, `database_identity`, authority/snapshot projections, one preparation object, exact attestation digest, receipt parser, ZIP byte inspection, fixed bundled assets, factory declarations, and credential-free boundary. Existing compatibility bytes remain unchanged.

### Lane B — staging and publication

Implement retained no-follow parent/ancestor handles, fstat identity records, dirfd-relative mode-0700 staging, and the constructibility-proven Linux `SQLiteStageAdapter` over `/proc/self/fd/<stage_fd>`. Reuse existing `OperationRegistry` v1-v5 construction, backup, and unchanged v6 transaction without moving ownership. Add 25 statement failpoints, complete read-only verification, sidecar allowlisting, cleanup refusal on identity drift, Linux `renameat2` no-replace, target inode readback, parent fsync, and deterministic parent/stage races. Native Windows or unavailable stable-fd SQLite support fails before database mutation.

### Lane C1 — generation-independent installation evidence

Before publication verify intent, exact candidate receipt/wheel/assets, factory declarations, route policy, grant policy, v6, profile, generation-1 history/current, and staged evidence. Do not create executable capability rows, `WorkbenchGrantSet`, session grants, or runtime health because runtime_generation is absent.

### Lane C2 — postpublication supervisor authority

After publication verify immutable install/profile/evidence, enter `starting`, allocate/fence the normal runtime generation, and invoke the standalone activation coordinator. `ProviderReadyActivationStore` publishes mode-0600 canonical bytes to the exact 20-digit generation path with no-replace/idempotent-same/conflicting-different semantics and readback before Ready. Prior generation files remain historical; session grants are memory-only/current-generation/revocable and issued only on an explicit policy-approved request. Startup never initializes, migrates, repairs, adopts, or rewrites schema/profile/install state.

### Lane D1 — durable root planner

Retain exact model.request tickets, `{kind, phase, step_index}`, waiting lease release, pending/prepared and dead-owner takeover CAS, cell-free successor ownership, and start-only admission.

### Lane D2 — settlement and reconciliation

Retain claim/send-start/receipt fences, settled success/failure/certain and before-send cancellation rules, correction/deadline/uncertainty/takeover outcomes, null usage, lookup/reconcile, and no blind resend.

### Lane E — method-scoped admission

Test caller/native planner modes, zero/positive budgets and feature implications, exact grant unions, factory variants, `GRANT_DENIED` versus `CAPABILITY_UNAVAILABLE`, and the no-operation-before-admission barrier.

### Lane F — exact-wheel installed host

Use one immutable wheel and exact receipt in a neutral environment. Prove absent-target install, installed-member provenance, startup/Ready barriers, read-only status/verification, rerun refusal, source-import isolation, and conditional old-root archive/no-old-root boundaries.

### Lane G — separately authorized T4

Only after T0–T3 passes may a separate authorization exercise route/usage evidence. T5 remains not authorized.

## 4. Verification discipline

Use deterministic barriers, fake clocks, transaction failpoints, exact bytes, and two independent filesystem/process actors. Do not use sleeps. Keep source, staged, published, installed, live, and evaluator evidence separate. The machine matrix has 214 atomic rows (T0=22, T1=91, T2=73, T3=15, T4=13, T5=0); its non-counted A-TEST-001 summary cannot substitute for those rows.

## 5. Definition of implementation complete

Implementation is complete only when the exact candidate receipt/wheel, all 201 T0–T3 atomic rows, exact startup/readback, conditional old-root evidence, and independent current-byte review pass. This plan does not claim that any of those events has occurred.
