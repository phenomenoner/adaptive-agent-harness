# AR-PRW Test Strategy and Gap Analysis

**Document status:** specification and test-planning artifact

**Implemented baseline:** `adaptive-agent-runtime 0.5.0a0`

**Baseline source:** commit `bd30df40a9f3e77bcf2d244dbf4fd9bba0148ba1`

**Provisional successor target:** `adaptive-agent-runtime 0.6.0a0`

**Product lane:** Provider-Ready RLM Workbench (AR-PRW)

**Implementation status:** not started

**Test execution status for the successor:** planned, not executed

## 1. Purpose

This document consolidates the test objectives, test-item descriptions, test methods, required environments, current-version gaps, relevant technical details, and recommended optimization work for the AR-PRW successor release.

It is intended for implementation engineers, reviewers, release engineers, and operators who need one practical answer to four questions:

1. What behavior must the successor prove?
2. At what test altitude can each claim be falsified?
3. Which gaps in `0.5.0a0` prevent a product-usable workbench journey today?
4. What is the smallest maintainable implementation and test approach that closes those gaps?

This document is subordinate to the normative contracts in:

- [`README.md`](README.md)
- [`ARCHITECTURE.md`](ARCHITECTURE.md)
- [`CONTRACTS.md`](CONTRACTS.md)
- [`LIFECYCLE.md`](LIFECYCLE.md)
- [`MIGRATION.md`](MIGRATION.md)
- [`ACCEPTANCE.md`](ACCEPTANCE.md)
- [`verification/requirements.json`](verification/requirements.json)
- [`verification/acceptance-matrix.json`](verification/acceptance-matrix.json)

Where this summary conflicts with a normative contract, the normative contract wins.

## 2. Executive Summary

AAR `0.5.0a0` has a substantial implemented foundation:

- an MCP v8 surface with 38 tools;
- registry-v6 DDL and migration primitives;
- durable supervisor and generation ownership;
- caller-work claim, send-start, cancellation, commit, and reconciliation commands;
- route, receipt, and usage models;
- workbench coordinator and strict capability projection;
- a passing component test baseline of `743 passed, 5 skipped, 1 warning` on the exact source baseline.

However, the installed product remains unable to execute the complete provider-backed caller-delegated workbench journey because the product-level activation path is missing. The observed live baseline still has:

- registry versions 1 through 5 only;
- no reviewed operator-owned v5-to-v6 cutover;
- no published `rlm.workbench.execute` grant;
- six workbench method rows reported as unconfigured;
- no production root-planner caller-driver integration;
- no installed journey that proves requested and effective route, physical attempts, and usage receipts.

Therefore, the successor test strategy must not ask only whether classes and schemas work. It must prove the whole authority chain:

```text
operator intent
  -> preserved-v5 or empty-runtime-v5-bootstrap ordinary v6 cutover
  -> generated final activation profile
  -> supervisor generation and package-owned adapters
  -> truthful method capabilities and scoped grants
  -> durable root-planner/caller-work lifecycle
  -> reconciled route, response, usage, and final result
```

A green lower-level suite is necessary but insufficient. Product readiness requires one immutable built wheel to pass every mandatory no-network gate from T0 through T3. Live provider qualification is a separate T4 operation with separate authorization.

## 3. Baseline and Claim Boundary

### 3.1 Exact baseline

| Item | Baseline |
|---|---|
| Package | `adaptive-agent-runtime 0.5.0a0` |
| Source commit | `bd30df40a9f3e77bcf2d244dbf4fd9bba0148ba1` |
| Python support declared by package | `>=3.11,<3.15` |
| Locked environment tool | `uv` |
| Existing CI | `ubuntu-latest`, Python 3.11, 3.12, 3.13, and 3.14 |
| Existing CI gates | public-release hygiene, contract verification, Ruff, full pytest suite |
| Verified local reference environment | WSL2 Linux, Python 3.12.3, uv 0.12.3 |
| Existing test files | 53 `test_*.py` modules in the exact baseline |
| Historical component result | `743 passed, 5 skipped, 1 warning` |

### 3.2 What the baseline result proves

The historical source-suite result establishes that the exact `0.5.0a0` component implementation was internally coherent under that test environment. It does **not** prove:

- that a preserved runtime has migrated to registry v6;
- that an operator can safely plan, apply, reconcile, or restore a cutover;
- that a final activation profile was generated and verified;
- that the durable supervisor publishes a mutation grant;
- that a production caller driver or root planner is configured;
- that a physical provider request occurred;
- that effective route or usage evidence exists;
- that a built `0.6.0a0` wheel behaves like an editable source checkout.

### 3.3 Successor claim states

| State | Minimum evidence |
|---|---|
| `spec_planned` | Reviewed contracts and complete requirement-to-test mapping. |
| `implementation_in_progress` | Authorized code work on a clean accepted implementation base. |
| `implemented_unverified` | Candidate code exists, but at least one mandatory T0-T3 gate is open. |
| `implementation_verified` | One immutable candidate passes all mandatory T0-T3 rows. |
| `live_qualified` | Separately authorized T4 proves the exact effective route and evidence boundary. |
| `blocked` | A named prerequisite is absent; no higher state is inferred. |

## 4. Test Objectives

| Objective ID | Target | Why it matters | Release consequence if unproven |
|---|---|---|---|
| OBJ-01 | Preserve frozen MCP v7/v8 compatibility and existing product behavior. | The successor closes host activation gaps; it must not silently create a new protocol. | Release blocked. |
| OBJ-02 | Prove canonical contracts, digest domains, and secret-free artifacts. | Activation and receipts are authority-bearing. Ambiguous or cyclic digests make verification impossible. | Release blocked. |
| OBJ-03 | Prove operator-owned, atomic, recoverable registry-v6 cutover. | Package installation does not migrate preserved SQLite state. | Upgrade journey blocked. |
| OBJ-04 | Prove empty-runtime bootstrap without fabricating migration evidence. | New installations must first create canonical empty v5 and then use the same truthful snapshot/row-set-bound v6 cutover. | Empty-runtime journey blocked. |
| OBJ-05 | Prove final activation profile, adapter factory, capability, and Ready truth. | Configuration text must not create false-green capabilities. | Mutation admission blocked. |
| OBJ-06 | Prove scoped workbench grant issuance and revocation. | Activation policy is not itself mutation authority. | Mutation admission blocked. |
| OBJ-07 | Prove durable caller-delegated root planning and external waiting. | The current synchronous planner seam cannot support a reliable host journey by itself. | Caller-delegated execution blocked. |
| OBJ-08 | Prove send-start, unknown-outcome, reconciliation, and no-blind-replay semantics. | A request may spend resources even when the caller loses the response. | Safety and accounting blocked. |
| OBJ-09 | Prove method-scoped admission. | A bounded model-only job must not require unrelated methods, and missing authority must not be hidden. | Partial profiles remain unusable or unsafe. |
| OBJ-10 | Prove exact-wheel preserved-v5 and empty-runtime-v5-bootstrap host journeys. | Source tests cannot establish installed package behavior. | `implementation_verified` blocked. |
| OBJ-11 | Prove route, response, physical-attempt, and usage receipts in a bounded live qualification. | Requested configuration is not proof of effective treatment. | `live_qualified` blocked; release may still remain implementation-verified. |
| OBJ-12 | Keep all negative outcomes and prior attempts visible. | A later green rerun must not erase a prior failure, timeout, or indeterminate send. | Evidence cannot support a release decision. |

## 5. Test Environments

### ENV-0 — Static contract and source environment

**Purpose:** T0 schema, contract, compatibility, package-asset, lint, and traceability checks.

**Required shape:**

- clean implementation worktree based on the accepted source commit;
- locked `uv` environment;
- Python 3.11 through 3.14 on Ubuntu CI, matching existing project policy;
- network disabled or unused;
- no provider credentials required;
- canonical fixtures checked into the repository;
- fixed source, schema, profile, skill, and wheel digests.

**Reference commands:**

```bash
uv sync --locked --python <3.11|3.12|3.13|3.14>
uv run --python <version> aar-contract verify
uv run --python <version> ruff check .
uv run --python <version> pytest -q
```

These commands are baseline CI shapes. Successor implementation should add focused test selections, not replace the full gate.

### ENV-1 — Disposable SQLite migration and fault environment

**Purpose:** T1 cutover, snapshot, WAL/SHM, authority, idempotency, restore, and crash-boundary tests.

**Required shape:**

- disposable runtime homes under an isolated temporary root;
- populated deterministic v5 fixture database;
- explicit journal modes and fixtures for no sidecar, zero-length sidecar, committed WAL frames, changing WAL, corrupt sidecar, and unclassifiable state;
- SQLite backup API, never a main-file-only copy;
- injected clock, boot identity, process-start identity, cutover epoch, and filesystem failure points;
- two independent processes or file descriptors for lock exclusion tests;
- source database and authority-store hashes captured before and after every negative case;
- no live provider access.

### ENV-2 — In-process runtime lifecycle integration environment

**Purpose:** T2 activation, capability, grant, planner, caller-work, admission, cancellation, timeout, and reconciliation tests.

**Required shape:**

- real registry-v6 schema and repository implementation;
- real supervisor/reference-host composition where practical;
- deterministic package-owned fake method adapters;
- deterministic fake caller driver that implements claim, mark-send-start, commit, lookup, cancel, and reconcile without network access;
- fake monotonic clock and explicit deadlines;
- deterministic IDs and physical-attempt ordinals;
- controlled barriers instead of sleeps for races;
- fail-if-called synchronous planner canary in caller-delegated mode;
- two database connections or processes where transaction ordering matters;
- no provider credentials and no external network.

### ENV-3 — Exact-wheel installed-host environment

**Purpose:** T3 package provenance and complete no-inference product journey.

**Required shape:**

- wheel built from the frozen candidate;
- fresh virtual environment with no editable install and no source-root leakage on `PYTHONPATH`;
- two disposable runtime roots:
  1. a preserved-v5 upgrade root;
  2. an absent/uninitialized empty-runtime root bootstrapped through canonical v5 and ordinary v6 cutover;
- real `aar-admin`, `aar-supervisor`, and `aar-mcp` executables from the installed wheel;
- real supervisor process lifecycle, discovery file, private attachment, and MCP client;
- frozen activation intent, generated final profile, bundled schemas, profile assets, and operation skill;
- deterministic no-network caller-driver responses;
- restart, status/readback, native capability call, compatibility smoke, and cleanup evidence.

### ENV-4 — Bounded live route qualification environment

**Purpose:** T4 proof of effective route, response identity, physical-attempt lineage, and provider-reported usage.

**Required shape:**

- exact immutable wheel/profile/route/driver/contract digests already passing T0-T3;
- explicit operator authorization for each physical request;
- credentials resolved outside AAR artifacts and never written to profiles, databases, receipts, logs, or fixtures;
- network enabled only for the bounded qualification process;
- fallback disabled;
- one non-scored, low-cost request for the positive path and separately authorized negative probes only when a physical request is unavoidable;
- complete send-start, receipt, reconciliation, and usage evidence;
- dedicated evidence root separate from ordinary test output;
- no automatic rerun after timeout, unknown outcome, or receipt loss.

### ENV-5 — Platform coverage environment

The existing upstream CI proves Ubuntu behavior across Python 3.11-3.14. It does not by itself prove native Windows or macOS file locking, process identity, Unix-socket alternatives, filesystem atomicity, or SQLite sidecar behavior.

Recommended policy:

1. Keep Ubuntu/Python 3.11-3.14 as the required source matrix.
2. Use Linux/WSL2 as the first full lifecycle and exact-wheel qualification environment.
3. If native Windows is a supported release target, add a native Windows T1/T3 lane for path handling, lock semantics, process identity, supervisor transport, OpenSSL, atomic replacement, and SQLite WAL restore.
4. Add macOS only when it is an explicit supported product target; do not turn an unclaimed platform into a release blocker.
5. Mark every platform-gated skip explicitly and explain which claim it withholds.

## 6. Detailed Test Catalog

### 6.1 T0 — Contracts, static compatibility, and evidence shape

| Test ID | Target and explanation | Test method | Environment | Required pass evidence |
|---|---|---|---|---|
| T0-01 | Validate all fourteen activation, adapter, activation-generation, prepared/terminal marker, cutover, restore, readback, grant, alias, component-wise route-evidence and paired-admission schemas. Strict schemas prevent silent authority expansion. | Generate Draft 2020-12 valid/invalid fixtures including non-canonical versions, seventh-method `artifact.read`, backend/reference invariants, collection order/cardinality, deadline bounds, marker variants and generation-prior cases; validate with project and independent implementations. | ENV-0 | Both validators agree on all fixtures; all required fields, bounds and cross-field rules are complete. |
| T0-02 | Prove canonical self-digest domains and acyclic activation/operator-marker construction. | Recompute each self digest with only its own root field omitted; mutate/swap plan, prepared, receipt and terminal layers plus intent, attestation and final profile independently. | ENV-0 | Valid `plan→prepared→transaction/receipt→terminal` and activation chains pass; every cycle/equality/swap mutant fails before authority. |
| T0-03 | Preserve frozen MCP v7/v8 bytes. | Byte-compare generated and bundled tool names, order, input schemas, output schemas, and capability artifacts to the accepted baseline. | ENV-0 | Exact byte equality and 38-tool order. |
| T0-04 | Reject secrets and executable injection in authority artifacts. | Canary fixtures place tokens, cookies, environment expansion, shell substitution, arbitrary imports, paths, URLs, and credential resolver output only in prohibited semantic fields or exercise resolution/execution behavior; permitted opaque identifier contents remain inert data. Run targeted secret scanning. | ENV-0 | Certain validation failure; no rejected secret appears in logs or generated artifacts. |
| T0-05 | Bind package-owned adapter factories. | Validate factory registry rows against implementation digest, allowed backend kind, the exact six-method frozen catalog, method contract digests, frozen capability evidence tier, and reference projection; attempt arbitrary imports and manifest/factory swaps. | ENV-0 | Only package-registered exact factories resolve. |
| T0-06 | Prove requirement-to-test ownership. | Validate all mandatory requirement IDs have at least one acceptance row and all rows point to existing requirements. | ENV-0 | Zero orphan requirements and zero orphan acceptance rows. |
| T0-07 | Preserve claim-state separation. | Static validation of receipts/status documents and negative fixtures that attempt to promote source, installed, live, or release status without required evidence. | ENV-0 | Unsupported state promotion rejects. |

### 6.2 T1 — Cutover, migration, backup, and restore

| Test ID | Target and explanation | Test method | Environment | Required pass evidence |
|---|---|---|---|---|
| T1-01 | Ensure `cutover plan` is stdout-only and read-only. | Snapshot DB, WAL, SHM, authority directory, process table projection, and profile-output path before/after planning; reject `--output`; capture stdout/stderr and inject write/checkpoint canaries. | ENV-1 | Exactly one canonical plan plus newline is emitted on stdout, diagnostics stay on stderr, and all source bytes/service state remain unchanged. |
| T1-02 | Enforce one runtime-home owner. | Hold supervisor shared lock, then attempt cutover; hold cutover exclusive lock, then attempt supervisor start; test replacement process and PID reuse. | ENV-1 | Conflicting owner receives deterministic rejection with zero mutation. |
| T1-03 | Include committed WAL pages in snapshots. | Commit a sentinel row that remains in WAL, invoke SQLite backup API, then verify sentinel and canonical row-set digest in the standalone snapshot. | ENV-1 | Snapshot includes sentinel; integrity and foreign-key checks pass. |
| T1-04 | Reject WAL/SHM drift and unsafe sidecars, including snapshot-before-prepared crashes. | Change sidecar identity/digest after plan; supply hot/unreadable/corrupt variants; publish exact and mismatched final snapshots then crash before the prepared marker. | ENV-1 | Stale/unsafe inputs reject; one exact snapshot may be adopted; mismatch/ambiguity is recovery-required and no final snapshot is overwritten/deleted. |
| T1-05 | Commit v6 DDL and attestation atomically. | Inject failure after selected DDL statements, version insertion, and attestation preparation but before transaction commit. | ENV-1 | Either complete v5 or complete v6+attestation is visible; no partial v6 state. |
| T1-06 | Prove ordinary cutover CAS for preserved v5 and separately bootstrapped-empty v5, append-only activation-history CAS, derived pointer, and strict prepared/terminal markers. | First bootstrap one empty-v5 root without a cutover epoch; then independently materialize ordinary plans for preserved/empty v5 and race same/different epochs, profiles, prior history tips and generations; tamper/swap/delete/fork history, current, inner receipts and outer markers. | ENV-1 | Bootstrap creates no cutover authority; first or strict higher ordinary history link commits once; exact replay returns nested receipt; lower/stale/ABA and inner/outer mismatch conflict. |
| T1-07 | Reconcile every cutover and restore crash boundary. | Interrupt after snapshot-before-prepared, during transaction/replacement, after DB commit/readback/final-profile, around immutable history/current, and before terminal marker; invoke exact-input cutover/restore reconcile and changed-input variants, including original cutover recovery terminal plus matching/mismatching restore terminal. | ENV-1 | Only matching authority repairs the specified missing suffix; prepared old DB returns abort-or-apply-required; one exact cutover_recovery_required→restore_committed pair closes the original epoch; changed input/fork/ambiguity cannot overwrite authority or repeat replacement blindly. |
| T1-08 | Enforce rollback frontier. | Attempt restore before commit, after commit but before Ready, after Ready, and after a durable v6 admission/write. | ENV-1 | Only explicitly allowed pre-frontier restore succeeds; later restore is refused. |
| T1-09 | Verify restore preparation, cross-epoch closure, replacement and sidecar disposition. | Publish the original cutover_aborted or cutover_recovery_required terminal, bind its digest in strict restore preparation, restore through SQLite backup into a temporary DB, fsync/replace/dispose target sidecars/reopen; crash before and after replacement and restore-terminal publication. | ENV-1 | Success receipt binds both prepared digests and the exact original cutover terminal; one matching restore_committed closes recovery-required; missing/mismatching/duplicate closure remains blocked; ambiguous replacement never repeats and failed restore emits no success terminal. |
| T1-10 | Verify minimal empty-runtime bootstrap semantics without claiming abrupt-crash recovery. | Initialize absent, exact-uninitialized and canonical-empty-v5 roots; inject caught faults immediately before/after the existing Registry v5 commit; separately SIGKILL before commit to produce any WAL/SHM residue; rerun classification and try non-empty/non-canonical v5. | ENV-1 | Caught pre-commit faults roll back and caught post-commit faults converge to canonical empty v5; initialize stops before cutover. Abrupt crash-left residue returns `UNINITIALIZED_RUNTIME_RESIDUE` with zero delete/checkpoint/adoption; no retryability or automatic recovery claim; other invalid roots reject. |
| T1-11 | Prove idempotency and changed-byte conflict. | Repeat exact plan/apply/status; then change DB, intent, candidate, profile-output, SQL, or authority bytes under the same identity. | ENV-1 | Exact rerun returns existing receipt; changed bytes fail before mutation. |
| T1-12 | Separate read-only and mutation CLI commands. | Observe filesystem, DB, process, token, network and WAL checkpoint canaries for every `aar-admin` command. | ENV-1 | Plan/status/verify are zero-write and zero-checkpoint; only explicit apply/abort/reconcile/restore/initialize mutators may write their named targets under exclusive lock; no command silently stops or starts services. |

### 6.3 T2 — Activation, grants, planner lifecycle, and admission

| Test ID | Target and explanation | Test method | Environment | Required pass evidence |
|---|---|---|---|---|
| T2-01 | Preserve no-profile compatibility while failing closed on supplied-invalid profiles. | Start once with no activation-profile path; then under the shared runtime-home lock supply absent, stale, malformed, cyclic or mismatching intent/profile/attestation/current-authority/candidate bytes with generation/listener/discovery/grant/operation canaries. | ENV-2 | No-path startup preserves v0.5 behavior and truthful unconfigured/reference rows with no mutation grant or migration; every supplied-invalid profile rejects before ReferenceHost construction or start_runtime with zero generation/listener/Ready/grant/operation/send. |
| T2-02 | Derive capability truth from runtime objects. | Vary factory registration, implementation digest, manifest, contract, backend kind, frozen capability evidence tier, reference-only flag, and runtime generation. | ENV-2 | Exact native/caller rows are configured and admission-usable; exact reference rows are configured/reference-only/unknown and never usable; stale/missing rows are unconfigured. |
| T2-03 | Order Ready and grant publication. | Barrier-test adapter construction, issuer configuration, Ready publication, context request, shutdown, and generation rollover. | ENV-2 | No grant before Ready; Ready and grant-set digest agree; shutdown removes Ready before issuance stops. |
| T2-04 | Resolve full grant scope, union and expiry. | Exercise coherent multi-record `grant_ids`, missing/revoked/expired/mixed-set/identity/ceiling and duplicate-capability records, budget/deadline/TTL, generation retirement, route/profile drift and ABA. | ENV-2 | Only one coherent set yields the exact capability union; all authority mismatches fail `GRANT_DENIED` before availability, operation or send. |
| T2-05 | Prove activation has no inference side effect. | Instrument every provider-send sink while verifying and activating a profile and minting a grant. | ENV-2 | Send count remains zero. |
| T2-06 | Persist every caller-delegated root-planner phase. | Execute initial, correction, recovery, and finalizer paths using deterministic caller receipts and the frozen `{kind, phase, step_index}` logical-owner schema. | ENV-2 | One durable owner/ticket per phase and `step_index`; no `ordinal` wire field exists; all v6 field mappings validate. |
| T2-07 | Prevent synchronous planner use in caller-delegated mode. | Inject a planner object that fails the test if called; execute a full caller-delegated path. | ENV-2 | Canary call count is zero. |
| T2-08 | Release attempt ownership while waiting externally. | Pause after ticket creation, return the waiting snapshot through the host, inspect attempt/operation transitions, and advance fake time beyond the former lease. | ENV-2 | Operation remains accepted/waiting_external without a live execution-attempt lease; the host does not mark it SUCCEEDED; no false expiry or duplicate worker. |
| T2-09 | Enforce send-start ordering. | Try commit before claim, send before mark-send-start, duplicate send reservation, and stale claim generation. | ENV-2 | Invalid order rejects; physical send is possible only after durable send-start. |
| T2-10 | Contain unknown provider outcomes. | Crash or disconnect after send-start and before receipt; return conflicting lookup observations; reconcile, then advance the cumulative deadline over `cancel_requested`/`outcome_unknown`/`quarantined` tickets. | ENV-2 | Those states have no successor outbox and remain reconciliation/suspension-owned until authoritative evidence; no automatic replay, planner successor or false certain deadline terminal. |
| T2-11 | Fence stale receipts and writers. | Race late original claimant, successor attempt, cancellation, deadline, and finalization. | ENV-2 | Candidate evidence may append, but only current reconciler/finalizer mutates authority. |
| T2-12 | Prove the exact frozen caller-work→planner state map and crash-convergent cell-free successor authority. | For every phase, assert outbox creation only for `settled_success`, `settled_failure`, and `cancelled_certain`; assert no outbox for `cancelled_before_send`, `cancel_requested`, `outcome_unknown`, and `quarantined`; race eligible pending/prepared/takeover/consume and probe cell-bound tables. | ENV-2 | Eligible known outcomes consume/project once under the current tuple; before-send cancellation uses outer finalization; uncertain/parked states remain reconcile-only with no outbox; dead owner is fenced, old writer rejects, and both cell-bound access counts remain zero. |
| T2-13 | Preserve usage across retries and recovery. | Produce correction, retry, recovery, cancelled, and discarded attempt receipts with deterministic usage plus records with absent dimensions. | ENV-2 | Cumulative accounting includes every physical attempt; absent dimensions remain null and are never zero-imputed. |
| T2-14 | Enforce cancellation boundaries. | Cancel pending, send-reserved, send-started, candidate-received, directive-ready, and finalization-CAS states. | ENV-2 | Certain no-send states cancel cleanly; possibly sent states reconcile; receipts are never deleted. |
| T2-15 | Enforce one cumulative deadline. | Advance fake time across admission, planner, cell, caller claim/send/commit, cleanup, reconciliation, and finalization. | ENV-2 | All phases use one absolute deadline; cleanup cannot silently extend it. |
| T2-16 | Prevent unsafe recovery replay. | Supply a certain pre-cell checkpoint and failed source digest; ask recovery to repeat failed source or unjournaled effects. | ENV-2 | Unsafe continuation is rejected or parked before another effect. |
| T2-17 | Prove profile-bound method-scoped admission. | Activate separate caller-driver/native model.request profiles; table-drive exact job→profile normalization, matching/mismatching factory tuple, budgets, resolved capability unions, reference rows and `start_only`. | ENV-2 | Only caller_delegated→caller_delegated_ticketed and service_managed→service_managed match; required methods remain a pure pre-operation result. |
| T2-18 | Distinguish missing authority from current adapter unavailability. | Make a feature imply an ungranted method; drift factory/profile to retire generation; separately keep valid current grants while same-generation instantiated adapter health drops. | ENV-2 | First two cases are `GRANT_DENIED`; only the constructible third case is `CAPABILITY_UNAVAILABLE`; all occur before send. |
| T2-19 | Contain optional unavailable method calls. | Admit a job that does not require an optional method, then attempt that method at runtime. | ENV-2 | Job admission succeeds; optional invocation fails certain before physical work. |
| T2-20 | Preserve predecessor behavior. | Run focused v1 RLM, workspace, assets, public runtime, caller-work, supervisor, and compatibility suites after each affected lane. | ENV-2 | No regression in unchanged supported contracts. |

### 6.4 T3 — Exact-wheel product qualification without provider inference

| Test ID | Target and explanation | Test method | Environment | Required pass evidence |
|---|---|---|---|---|
| T3-01 | Prove wheel and bundled-asset identity. | Build once; install exact wheel into a fresh environment; compare wheel, direct-url, contract, schema, profile, skill, and fixture digests. | ENV-3 | Every installed byte matches the frozen candidate manifest. |
| T3-02 | Reject source import leakage. | Move or hide the source checkout and run installed executables from a neutral working directory. | ENV-3 | All imports and assets resolve from the wheel installation. |
| T3-03 | Qualify preserved-v5 upgrade root. | Install candidate, plan/apply cutover, restart supervisor, read activation status, call capabilities, complete deterministic no-network workbench journey. | ENV-3 | Exact v6 attestation/profile/readback/grant/capability/result lineage; no provider call. |
| T3-04 | Qualify empty-runtime bootstrap root. | Initialize a second absent/uninitialized runtime through canonical empty v5 and ordinary v6 cutover, then start/restart supervisor, read status, call capabilities and run deterministic journey. | ENV-3 | Ordinary snapshot/row-set-bound cutover receipt, frozen-v6 attestation and installed journey pass; no separate fresh authority exists. |
| T3-05 | Prove live host pickup, not standalone discovery only. | Start the real installed supervisor/MCP server; list exact tools and call the read-only workbench capability method through the intended host/session. | ENV-3 | Current session sees exact 38 tools and current runtime generation/profile digest. |
| T3-06 | Prove restart and cleanup. | Interrupt during waiting, settlement, and idle states; restart/reconcile; stop all processes and inspect sockets, workers, claims, and locks. | ENV-3 | No orphan worker/socket/claim; durable state resumes according to contract. |
| T3-07 | Prove default fail-closed behavior. | Install/start without an activation profile and with a preserved v5 database. | ENV-3 | No auto-migration, no execute grant, no configured production backend. |
| T3-08 | Run exact-candidate full gates. | On the immutable wheel/source generation, run contract verification, lint, full suite, compatibility smoke, preserved-v5 root, empty-runtime-v5-bootstrap root, and independent review. | ENV-3 | One coherent evidence bundle; no mixed candidate hashes. |

### 6.5 T4 — Bounded live route and receipt qualification

T4 is not part of ordinary CI and does not repair a failed T0-T3 row.

| Test ID | Target and explanation | Test method | Environment | Required pass evidence |
|---|---|---|---|---|
| T4-01 | Prove the exact positive route. | Submit one explicitly authorized non-scored request through the activated caller-driver path with provider `openai-codex`, model `gpt-5.6-luna`, reasoning `max`, fallback `none`, and frozen cache policy. | ENV-4 | Requested and effective route, response, physical attempt, send-start, settlement, and usage receipts join exactly. |
| T4-02 | Classify route components and reject drift. | Independently vary provider, model, reasoning visibility, fallback, cache, profile and catalog; include Prime observed provider/model/fallback/cache with requested-only reasoning. | ENV-4 | Each component gets its exact tier/effective-null rule; the valid Prime mixed record derives prime_live_qualified, while any contradiction or insufficient provider/model blocks qualification. |
| T4-03 | Validate usage and null semantics. | Recompute per-attempt and aggregate arithmetic; remove or alter reasoning/cache fields and physical-attempt rows. | ENV-4 | Provider-reported integer fields reconcile; unavailable dimensions stay null; incomplete rows block token-efficiency claims. |
| T4-04 | Prove no blind replay after ambiguous send. | Induce a bounded disconnect after durable send-start, then use supported lookup/reconciliation. | ENV-4 | Original attempt is reconciled or quarantined; no automatic second physical send. |
| T4-05 | Prove stale-observation rejection. | Submit a receipt from a retired adapter/runtime generation after a new generation becomes active. | ENV-4 | Evidence may be retained, but cannot authorize current success. |

### 6.6 Paired planning schema only — no T5 execution authority in v1

| Test ID | Target and explanation | Test method | Environment | Required pass evidence |
|---|---|---|---|---|
| T0-EVAL-PLAN | Validate the bounded paired planning document and prove it grants no launch authority. | Build from exact live-qualified fixtures; mutate expiry, candidate/profile/alias/classification/case/protocol/budget/stop/order/operator-planning bytes; probe package surfaces for any consumer. | ENV-0 | Only exact `benchmark_ready_planned` bytes validate; no attempt fields exist, no package launcher consumes the document, and provider/contender send canaries remain zero. |

T5 physical paired execution is `NOT AUTHORIZED` and has no test lane in this release. A future launcher-authority SDD must define and own its physical-attempt CAS/reconcile tests; those tests are not preallocated here.

## 7. Current `0.5.0a0` Gap Analysis

| Gap ID | Current state | Product impact | Required closure | Primary tests |
|---|---|---|---|---|
| GAP-01 | Package upgrade can reopen a preserved registry ending at schema v5. | New workbench tables and caller lifecycle are unavailable despite newer MCP discovery. | Explicit, dry-runnable, receipt-backed v5-to-v6 cutover. | T1-01 through T1-11; T3-03. |
| GAP-02 | Migration helpers exist, but no supported command owns stop, snapshot, authority, apply, reconcile, and restore. | Operators cannot safely cross the cutover frontier. | One `aar-admin` surface plus external epoch markers and stable locking. | T1-01, T1-02, T1-06, T1-07, T1-12. |
| GAP-03 | Official launchers do not accept a verified activation profile. | Implemented factories, routes, grants, and recovery policy cannot be bound into startup. | Acyclic activation intent, v6 attestation, generated final profile, and startup verification. | T0-02; T2-01 through T2-05; T3-03/T3-04. |
| GAP-04 | Default reference context does not publish `rlm.workbench.execute`. | Mutation requests fail `GRANT_DENIED`. | Supervisor-owned scoped grant issuer after Ready verification. | T2-03, T2-04, T2-05. |
| GAP-05 | All six live method rows are unconfigured; tests manufacture caller-driver availability. | Component tests can be green while no executable production adapter exists. | Package-owned factory registry and component-derived capability projection. | T0-05; T2-02; T3-05/T3-07. |
| GAP-06 | Workbench execution requires an injected synchronous planner; the production profile supplies none. | Caller-delegated root planning cannot start or recover durably. | Ticket every root-planner phase through existing caller work. | T2-06 through T2-16. |
| GAP-07 | Current admission rejects a journey when any broker method is unavailable. | A model-only bounded job cannot run on a truthful partial profile. | Pure method-scoped required-set derivation. | T2-17 through T2-19. |
| GAP-08 | Route and usage models exist, but the installed workbench journey does not emit complete qualification evidence. | Requested route cannot be promoted to effective route; usage and retry claims remain unproven. | Join existing route, receipt, usage, ticket, and physical-attempt records. | T2-09 through T2-13; T4-01 through T4-05. |
| GAP-09 | The local canonical working branch predates the exact installed `0.5.0a0` baseline and contains unrelated dirty work. | Direct implementation risks overwriting user work or building against the wrong source lineage. | Preserve dirty work and create a clean worktree from the accepted baseline before implementation. | T0 source-custody gate; T3-08. |
| GAP-10 | Existing CI is Ubuntu-only, even though some platform-sensitive code and tests exist. | Native Windows/macOS lock, path, process, transport, and atomic-replace behavior is not established. | Add native lanes only for explicitly supported release platforms. | ENV-5 platform qualification. |
| GAP-11 | The existing broad suite does not exercise the complete activated exact-wheel journey. | A green source suite can coexist with a blocked product. | Two exact-wheel runtime roots and live host/session readback. | T3-01 through T3-08. |

## 8. Relevant Technical Details

### 8.1 Authority model

The successor has four separate authority layers:

1. **Operator authority** selects the candidate, activation intent, cutover epoch, route policy, and grant policy.
2. **Cutover authority** owns exclusive mutation of the runtime home and records immutable prepared/committed/recovery markers.
3. **Supervisor authority** owns runtime generation, operations, attempts, tickets, fences, capability projection, Ready publication, and bounded grant issuance.
4. **Provider evidence** reports only the effective route, response identity, and usage fields actually observed by the caller driver or provider.

No layer may infer a stronger claim from a weaker one. In particular:

```text
profile present        != active profile
active profile         != live caller
configured capability  != physical request
requested route        != effective route
source test pass        != installed product pass
```

### 8.2 Acyclic activation digest graph

The required construction order is:

```text
activation intent
  -> intent_digest
  -> registry-v6 migration attestation
  -> migration_attestation_digest
  -> generated final activation profile
  -> final profile_digest
  -> supervisor readback, capabilities, and grants
```

The legacy migration-attestation field named `profile_digest` must equal the pre-cutover `intent_digest`; it must not refer to the later final-profile digest. Tests must enforce this distinction because reversing the dependency creates an impossible digest cycle.

### 8.3 Cutover atomicity and rollback frontier

The cutover uses:

- an external immutable epoch directory;
- one stable runtime-home lock;
- a prepared marker before the SQLite transaction;
- registry-v6 DDL and attestation in one transaction;
- read-only post-commit verification;
- generated final-profile and committed marker publication after DB verification.

Rollback is safe only before the defined frontier. After a committed v6 attestation, Ready publication, or durable successor admission/write, recovery is forward-only unless a separately reviewed reverse journal exists. The first release proposes no reverse journal.

### 8.4 Capability truth

For a method to report `configured=true`, all of these must be true:

```text
active verified profile
AND package-owned factory resolves
AND implementation digest matches
AND factory instantiates
AND manifest digest matches
AND adapter generation equals current runtime generation
AND method contract digest matches
AND adapter is not reference-only
```

A profile boolean is input, not evidence. A caller-driver capability states that an authorized executable adapter contract exists; it does not guarantee a currently polling external process.

### 8.5 Durable planner ownership

Each root-planner call owns a distinct logical identity:

```text
operation_id
planner_phase = initial | correction | recovery | finalizer
planner_step_index        # frozen logical-owner wire field: step_index
route_binding_digest
request_digest
directive_schema_digest
suspension_revision
```

The coordinator persists the ticket and moves the operation to `waiting_external` before releasing local attempt ownership. The caller then claims, marks send-start, performs the physical request, commits a candidate observation, and allows the current reconciler to settle it. Only a successor attempt with current fences may apply the directive.

### 8.6 Send-start and unknown outcome

`mark_send_started` is the conservative may-have-sent boundary. After it:

- timeout, disconnect, process crash, or lost response is not proof of no spend;
- automatic replay is forbidden;
- late evidence is append-only candidate evidence;
- only the current reconciler may settle or quarantine the attempt;
- stale attempts cannot mutate current state.

This boundary must be tested with deterministic barriers, not timing sleeps.

### 8.7 Method-scoped admission

The required method set is calculated before operation creation from:

- planner execution mode;
- frozen artifact and subagent budgets/features;
- effective grant capabilities;
- exact native/caller factory availability.

Examples:

- caller-delegated planning requires a ticketed `model.request` caller driver and `start_only=true`;
- service-managed planning requires a separately qualified native planner;
- nonzero artifact limits require `artifact.put`;
- nonzero subagent limits require both submit and result methods;
- a zero budget never creates authority;
- feature-required but ungranted is `GRANT_DENIED`;
- granted but unavailable is `CAPABILITY_UNAVAILABLE`.

### 8.8 Usage and receipt accounting

The product should reuse existing route, receipt, usage, ticket, command-receipt, candidate-receipt, and physical-attempt records. It should not create a second token ledger.

Required accounting properties:

- every physical attempt has a durable lineage row;
- retries, corrections, recovery, cancelled, and discarded output remain charged;
- input, output, total, reasoning, and cache fields retain provider semantics;
- absent values remain null, not zero;
- aggregate values equal the declared in-scope physical-attempt rows;
- an unknown outcome remains unknown until authoritative reconciliation.

## 9. Recommended Optimization Plan

### OPT-01 — Organize tests by claim-bearing seam

Do not add one monolithic `test_provider_ready.py`. Preserve existing test ownership and add focused modules around the first failure-bearing seams, for example:

```text
tests/test_activation_contracts.py
tests/test_cutover_cli.py
tests/test_cutover_faults.py
tests/test_activation_composition.py
tests/test_workbench_grants.py
tests/test_root_planner_caller_work.py
tests/test_workbench_method_admission.py
tests/test_exact_wheel_host.py
tests/test_live_route_qualification.py   # opt-in only
```

Names may change during implementation, but each module should own one coherent contract and one evidence tier.

### OPT-02 — Build a reusable deterministic test kit

Create reusable helpers for:

- populated v5 registry fixtures;
- WAL-only committed sentinels;
- DB/WAL/SHM identity snapshots;
- canonical row-set and artifact digests;
- activation intent/final-profile builders;
- package-owned adapter manifests;
- fake clocks, IDs, generations, deadlines, and boot/process identities;
- two-party barriers for lock and CAS races;
- deterministic caller-driver receipts and lookup outcomes;
- filesystem and transaction failpoints.

This reduces duplication without inventing a second runtime abstraction. Helpers should expose product contracts, not hide them behind broad mocks.

### OPT-03 — Prefer table-driven cross-products over ad hoc cases

Method admission and grant behavior have a finite, meaningful cross-product. Use `pytest.mark.parametrize` to cover:

- native vs caller-delegated planner;
- caller-delegated `start_only=true` admitted; `start_only=false` rejected pre-operation;
- zero/nonzero artifact budgets;
- zero/nonzero subagent budgets;
- absent/present grants;
- native/caller/reference/unconfigured factories;
- current/stale runtime generations.

Start with an explicit table. Add a property-based testing dependency only if the matrix becomes too large for readable deterministic rows and the added dependency is justified.

### OPT-04 — Inject failures at named linearization points

Add test-only failpoints around:

- prepared marker publication;
- SQLite transaction begin and pre-commit;
- post-commit verification;
- final-profile publication;
- committed marker publication;
- Ready publication;
- ticket creation;
- send reservation;
- send-start;
- candidate receipt append;
- settlement;
- successor enqueue;
- finalization CAS.

Named failpoints are faster and more discriminating than random process killing. Retain a small number of real process-kill T3 scenarios to validate that test hooks match actual restart behavior.

### OPT-05 — Ban sleeps from race assertions

Use barriers, events, fake clocks, explicit transaction locks, and two connections/processes. Sleeps turn ordering tests into probability. A race test should prove both legal commit orders and the stale loser behavior deterministically.

### OPT-06 — Split fast, lifecycle, package, and live gates

Recommended markers:

```text
contract       # T0, fast and hermetic
migration      # T1, disposable SQLite/filesystem
lifecycle      # T2, real repositories with deterministic collaborators
package        # T3, exact wheel and subprocesses
live_provider  # T4, explicit opt-in and authorized network
platform       # native platform-specific behavior
```

Normal pull-request CI should run T0-T2 plus affected compatibility tests. Candidate qualification should add T3. T4 must never run from an ordinary pull request or merely because credentials are present.

### OPT-07 — Freeze and test one wheel once

Build the candidate wheel once, hash it, and use that same wheel for:

- preserved-v5 root;
- empty-runtime-v5-bootstrap root;
- host pickup;
- compatibility smoke;
- independent review subject;
- optional later live qualification.

Rebuilding between gates creates a moving candidate and invalidates downstream evidence.

### OPT-08 — Use readback as the product smoke seam

The smallest useful installed-host smoke is:

1. start exact installed supervisor;
2. read activation status;
3. list exact MCP tools;
4. call workbench capabilities through the intended host/session;
5. assert registry/profile/runtime/grant/capability digests;
6. stop and verify cleanup.

This is stronger than a process-is-running check and cheaper than a provider request.

### OPT-09 — Minimize live test cost

Move every route, schema, stale-generation, arithmetic, and error-classification case that can be falsified with recorded or synthetic evidence into T0-T2. Reserve T4 only for facts that require a real external observation:

- effective provider/model/effort;
- provider response identity;
- provider-reported usage;
- real lookup/cancel behavior when claimed.

No live request should be repeated simply to obtain a green latest result.

### OPT-10 — Preserve unknowns as first-class data

Do not normalize unknown usage, retry count, physical attempts, fallback, or response identity to zero or empty values. Provide the strict evaluator-owned route/usage/visibility classification and nullable rendering in models, receipts, test fixtures, and reports; capability evidence metadata cannot upgrade it. This prevents false efficiency and false no-send claims.

### OPT-11 — Keep platform support proportional

The first successor should not expand platform claims merely because Python supports more operating systems. Keep the release matrix tied to declared support. For native Windows, prioritize filesystem replacement, file locks, process identity, SQLite WAL/SHM, supervisor transport, OpenSSL, and cleanup. Broad duplicate unit testing adds little value compared with these seams.

### OPT-12 — Add performance gates only after correctness

Recommended post-correctness measurements:

- cutover plan and apply time on representative populated registries;
- supervisor startup/readback latency;
- ticket claim-to-settlement overhead excluding provider time;
- SQLite lock contention under bounded concurrent status/claim calls;
- recovery time for prepared, post-commit, and send-start crash states;
- receipt and database growth per physical attempt.

These should begin as diagnostic budgets. Promote them to release blockers only after stable baselines and explicit service-level requirements exist.

## 10. Suggested Execution Order

```text
Phase A — Contract freeze
  T0-01 .. T0-07

Phase B — Operator cutover
  T1-01 .. T1-12

Phase C — Activation and grants
  T2-01 .. T2-05

Phase D — Durable planner lifecycle
  T2-06 .. T2-16

Phase E — Method-scoped admission and compatibility
  T2-17 .. T2-20

Phase F — Exact-wheel product qualification
  T3-01 .. T3-08

Phase G — Independent current-byte review and candidate freeze

Optional Phase H — Separately authorized live route qualification
  T4-01 .. T4-05
```

Each phase follows RED-GREEN-REFACTOR at the smallest seam:

1. write or identify a discriminating failing test;
2. record why it fails;
3. make the smallest implementation change;
4. pass the focused test;
5. run affected compatibility tests;
6. refactor only while green;
7. defer broad full-matrix execution until candidate bytes stabilize.

An import, syntax, missing-dependency, or invalid-runner failure is not behavioral RED evidence.

## 11. Evidence and Reporting Requirements

Every formal test result should record:

- requirement and acceptance-row IDs;
- exact source commit and dirty-state classification;
- exact wheel/profile/contract/skill digests where applicable;
- Python, OS, SQLite, uv, and relevant MCP client/server versions;
- test command and exit code;
- fixture and fault-point identities;
- returned classification;
- proof that the wrong mutation, send, replay, success, or authority publication did not occur;
- prior failed, timed-out, cancelled, or indeterminate attempts without erasure;
- explicit non-claims.

For a frozen candidate, evidence dependencies must remain acyclic:

```text
source + contracts + fixtures
  -> built wheel and candidate manifest
  -> T0-T3 verification receipts
  -> independent review receipt
  -> optional live qualification receipt
```

Any executable, schema, fixture, validator, profile, or package-asset change reopens the affected evidence and all downstream claims. Documentation-only changes should be classified by whether they alter a normative contract or merely explanatory text.

## 12. Exit Criteria

### 12.1 Ready to begin implementation

- explicit implementation authorization exists;
- current dirty canonical work is preserved;
- clean worktree is created from the exact accepted baseline or an explicitly reviewed successor;
- baseline contract, lint, and full test results are recorded;
- source-delta review maps all superseded citations;
- T0 schema and fixture ownership is assigned.

### 12.2 `implementation_verified`

- all mandatory T0-T3 acceptance rows pass;
- one immutable wheel and profile generation own all evidence;
- preserved-v5 and empty-runtime-v5-bootstrap roots both pass;
- current host/session reads exact capabilities and generation;
- no source import leakage exists;
- independent current-byte review has no release-blocking finding;
- live T4 remains either explicitly unexecuted or separately evidenced.

### 12.3 `live_qualified`

- `implementation_verified` remains valid for the exact same candidate;
- each authorized physical request has complete send-start and outcome custody;
- requested and effective route evidence meets the declared tier;
- usage arithmetic and physical-attempt coverage reconcile;
- fallback, stale generation, duplicate send, and unknown-outcome probes are contained;
- qualification evidence is stored separately from ordinary test and release output.

## 13. Residual Risks and Non-Claims

This strategy does not claim that:

- AR-PRW is implemented;
- registry v6 has been applied to a real runtime;
- an activation profile has been installed or activated;
- any production method adapter is configured;
- a mutation grant has been published;
- any live provider route has been qualified;
- native Windows or macOS behavior has passed;
- a provisional `0.6.0a0` version number is final;
- a passing component suite is equivalent to product readiness.

The largest implementation risk is not schema generation. It is preserving one coherent temporal authority across cutover, supervisor activation, durable external waiting, send-start, reconciliation, successor scheduling, and finalization. Tests must therefore focus on ownership, ordering, fences, and absence of the wrong effect—not merely on return values or configured booleans.

## 14. Recommended Immediate Next Step

After explicit implementation authorization, create a clean implementation worktree from the accepted `0.5.0a0` source baseline, import this SDD, and implement **T0 contract fixtures first**. Do not begin with live provider integration or a broad end-to-end test. Freeze the authority-bearing document formats and digest graph, then close migration and lifecycle seams in dependency order.
