# A1b-op2 — Activation Readback and Grant Strict Models

**Status:** `generation_13_review_pending`

**Parent lane:** A1b — operator/readback/evaluation contract models

**Task-start candidate parent:** `373fa147f0fbcd55627e5ce3f0b585245a41440d`

**SDD binding:** Generation-13 semantic tree `sha256:f676ed30a08e3f9cbfe38b091f69c69857fe7da047612a964565a76d0ab702fb`; complete 24-file SDD tree `sha256:d9373a26bb56e3604b08857eb81b0770bbcfd5f81f8d6174f89e130256330d6d`; receipt file `sha256:c416093655ad0d2c311f132ea1e0e6408d8d60222b74cf9ea6cd04a2de2d3d8c`; CONTRACTS `sha256:638cd1ed531ccc5ad0f3930d10fbab6b7f7a790516b71ca4c53759e496ccd591`; validator `sha256:8a51ed6a86fa9f87c5752ba6f3b146ed3de8891d0d3d7804c97eac400f9c1fdf`. Deterministic validator PASS twice with byte-identical receipt; independent fixed-byte review pending.

**Focused-green dependencies:** A1a `3ca321fd0df5a20a969965a0c1705fdd02dc83b6`; A1b-op1 `d03a10f4ac1cc18b43addb0e62245073f7274e3e`.

## 1. Outcome

Implement strict, transport-neutral, inert data models for exactly two remaining runtime-owned top-level schemas and three nested/internal records:

1. `aar.activation-readback.v1`;
2. `aar.workbench-grant-set.v1`;
3. nested `BackendAvailability`;
4. nested `PlannerReadback`;
5. internal server-owned `IssuedWorkbenchGrant` with no public schema version or shape digest.

This slice validates wire shape, canonical collections, document-local state/nullability/binding rules and root self digests only. It does not observe a runtime, choose a state, scan files/history/markers, publish Ready, instantiate adapters, mint/lookup/revoke grants, resolve contexts, start operations, send providers or mutate anything. It closes no acceptance row alone.

## 2. Exclusive writable paths

Exactly:

- `src/aar/provider_ready_runtime_models.py`
- `tests/test_provider_ready_runtime_models.py`

Both must be absent and collision-free at task start. No other path may be edited, staged or committed.

## 3. Reuse and inert boundary

Reuse without modifying:

- `StrictModel`, `Digest`, `OpaqueToken`, `PositiveCounter`, `BudgetCounter`, `CapabilityName` from existing package seams;
- `ModelRouteValue` from its predecessor package seam;
- `ProviderReadyCandidate`, `GrantBudgetCeiling`, `LocalAuthorityStoreId`, `WorkbenchActivationMethodName`, `CapabilityEvidenceTier`, `PlannerMode` from A1a;
- `UnixMs` and `CandidateBinding` from A1b-op1 when their domains are identical;
- `canonical_sha256` for root self digests.

No file, DB, environment, process, clock, network, provider, MCP, issuer, registry or runtime call/import is permitted. Builders receive all observations/timestamps explicitly.

## 4. Nested records

### 4.1 `BackendAvailability`

Exactly ten required fields, no schema version/default/extra:

- `method: WorkbenchActivationMethodName`;
- `contract_id: CapabilityName`;
- `request_schema_digest: Digest`;
- `response_schema_digest: Digest`;
- `backend_kind: unconfigured|native|caller_driver|reference`, the four-value readback-only domain fixed by Generation-11;
- `configured: bool`;
- `reference_only: bool`;
- `adapter_id: OpaqueToken | null`;
- `adapter_generation: PositiveCounter | null`;
- `evidence_tier: CapabilityEvidenceTier`.

Document-local truth:

- `unconfigured`: `configured=false`, `reference_only=false`, null adapter ID/generation and `evidence_tier=unknown`;
- `reference`: `configured=true`, `reference_only=true`, null adapter ID/generation and `evidence_tier=unknown`;
- `native|caller_driver`: `configured=true`, `reference_only=false`, non-null adapter ID/generation and any valid `CapabilityEvidenceTier`;
- `artifact.put` cannot use caller-driver, matching A1a manifest truth.

No fourth combination is valid. Implement a local readback backend literal; do not widen A1a's manifest-only `BackendKind`. Exact equality to an activated manifest tier and current-generation identity is later observer/composition evidence because the nested row carries no manifest; the pure row validates only its supplied backend tuple and evidence-tier domain.

### 4.2 `PlannerReadback`

Exactly five required fields: nullable `mode: caller_delegated_ticketed|service_managed`, `ready: bool`, nullable `factory_id: CapabilityName`, nullable `factory_digest: Digest`, nullable `method_manifest_digest: Digest`.

- null mode requires all three bindings null and `ready=false`;
- non-null mode requires all three bindings non-null;
- `ready=true` is structurally permitted only when the containing activation readback state is `active`.

Equality to the selected manifest belongs to later composition unless the selected manifest is present in the same document.

### 4.3 `IssuedWorkbenchGrant`

Internal strict server-owned record; exactly twenty required fields and no schema version, default, extra or shape digest:

`grant_id`, `grant_set_digest`, `capability`, `principal_id`, `session_id`, `runtime_generation`, `activation_generation`, `profile_digest`, `activation_authority_digest`, `capability_digest`, `route_catalog_digest`, six concrete ceiling fields `wall_time_ms`, `model_requests`, `input_tokens`, `output_tokens`, `child_operations`, `artifact_bytes`, `issued_at_unix_ms`, `expires_at_unix_ms`, `revoked`.

Use the same strict scalar/bounds as the matching `GrantBudgetCeiling` component. Generation-11 explicitly binds both timestamp fields to `UnixMs` and requires `issued_at_unix_ms < expires_at_unix_ms`. Live-clock, grant-set TTL, profile/job deadline and generation-retirement comparisons remain later issuer/composition behavior.

No record-level digest is added. No minting, lookup, expiry check against a live clock, projection or revocation behavior is implemented.

## 5. `aar.workbench-grant-set.v1`

Exact fourteen required fields from `CONTRACTS.md` §9:

- literal schema version;
- positive runtime and activation generations;
- profile ID and profile/activation-authority/capability/route digests;
- `principal_ids`: sorted unique exact `OpaqueToken` tuple length `1..64`, no glob/regex semantics;
- literal `session_binding_policy=bind_exact_request_session`;
- `capabilities`: sorted unique `CapabilityName` tuple length `1..64`;
- exact `GrantBudgetCeiling`;
- `max_ttl_ms: 1000..900000`;
- self `grant_set_digest`.

An issue/builder may sort validated collection input. Direct validation rejects supplied noncanonical order/duplicates and wrong self digest. The cross-document rule `max_ttl_ms <= intent.max_deadline_ms` and policy/executable intersection are not derivable from this document and remain later composition responsibility.

Directly resolve local `$defs/$ref` and assert emitted schema bounds for both collections (`1..64`). Generic empty/65-item/error-type/duplicate/order rejection is not a substitute.

## 6. `aar.activation-readback.v1`

Implement all 31 required properties and exact domains from `CONTRACTS.md` §5, including:

- literal schema version, explicit `observed_at_unix_ms`, eight-state and exact reason-code literals;
- all nullable runtime/candidate/registry/profile/history/grant/capability/catalog/tool/route/authority/operator fields;
- exactly six `BackendAvailability` rows in frozen broker-catalog order: `model.request`, `subagent.submit`, `subagent.result`, `evidence.query`, `artifact.put`, `effect.propose`;
- `PlannerReadback`;
- sorted unique `route_profile_ids` length `0..64`;
- sorted unique `evidence_sources` length `0..8` from the exact frozen enum;
- self `readback_digest`.

Issue/build may canonicalize only route profile IDs and evidence sources; methods remain exact catalog order and are never sorted by caller text.

### 6.1 Root binding rules

- null `operator_epoch_kind` requires null `operator_epoch` and `latest_operator_receipt_digest`;
- non-null kind requires non-null epoch; receipt digest remains nullable until a matching terminal exists;
- a receipt digest is the nested receipt self digest, never a terminal-marker digest;
- all method names are unique and exact order/length six;
- planner `ready=true` iff state is `active`; every non-active state requires planner not ready;
- direct validation rejects noncanonical supplied collections and wrong root digest.

External equality to manifests/history/current/Ready and evidence-source truth is not claimed by the pure model.

### 6.2 Exhaustive state/reason/nullability matrix

Implement every document-local implication in the frozen table; any contradiction rejects:

- `unconfigured`: reason `none`; profile/history/runtime/grant/capability bindings null and registry absent; independently installed `candidate` may be null or non-null; methods use only exact unconfigured/reference rows; planner null/not ready.
- `migration_required`: registry version `5`, registry digest non-null, reason `REGISTRY_VERSION_UNSUPPORTED`; profile/history/runtime/grant/capability bindings null; planner not ready; all methods are unconfigured/reference rows with no configured native/caller-driver adapter. Candidate and independently observed non-authority broker/tool/route facts remain source-dependent and cannot imply an active profile.
- `profile_invalid`: registry version `6` and digest non-null; reason exactly one of `ACTIVATION_PROFILE_INVALID|ACTIVATION_BINDING_MISMATCH|GRANT_POLICY_INVALID`; candidate non-null; profile-derived nullable values remain source-dependent and the pure model does not infer which owning document validated; runtime/grant/capability fields null, planner not ready and no configured mutation row.
- `profile_verified`: reason `none`; candidate, registry-v6/digest, attestation, profile ID/digest, activation generation, intent, activation authority, authority store and history tip all non-null; history tip equals activation authority digest; runtime/grant/capability fields null; broker/tool/route observations are independently source-dependent; methods/planner non-ready.
- `starting`: reason `none`; all profile-verified bindings plus runtime generation and supervisor identity non-null; grant/capability/catalog/tool/route null; methods/planner non-ready.
- `active`: reason `none`; registry `6`; every prior-active candidate/profile/history/runtime/grant/capability/broker/tool/route/authority field is non-null except first-generation `previous_activation_authority_digest` and the independently nullable operator epoch/receipt triple; history tip equals activation authority digest; route profile IDs non-empty; only native/caller-driver executable rows carry current runtime generation while configured reference rows retain null adapter identity/generation; planner ready with non-null bindings.
- `degraded`: reason exactly `GRANT_BINDING_MISMATCH|PLANNER_UNAVAILABLE|CAPABILITY_UNAVAILABLE|STALE_ADAPTER_GENERATION`; retains the same prior-active facts with the same predecessor/epoch exceptions; planner not ready. Method availability may show the named failure but cannot claim a mutation-ready planner.
- `recovery_required`: reason exactly `ACTIVATION_HISTORY_CONFLICT|CUTOVER_RECOVERY_REQUIRED|ABORT_OR_APPLY_REQUIRED|RECONCILE_INPUT_REQUIRED`; runtime generation, supervisor, grant set and capability bindings null; planner not ready and no configured mutation row. Broker/tool/route plus validated profile/history/operator observations remain source-dependent and are not erased by state alone.

The deterministic state-selection precedence is later observer behavior, not performed by the model. The model only rejects combinations that contradict the selected state.

Across every state, `previous_activation_authority_digest` remains nullable and the operator triple follows only its local rule: null kind requires null epoch/receipt; non-null kind requires non-null epoch and permits null receipt until terminal publication.

The focused suite MUST contain separate full-document positive witnesses, each built with an independently correct `readback_digest`, for:

1. active and each of the four degraded reasons with at least one configured reference row whose adapter identity/generation are null, plus native/caller-driver rows bound to the current runtime generation;
2. all four degraded reasons individually: `GRANT_BINDING_MISMATCH`, `PLANNER_UNAVAILABLE`, `CAPABILITY_UNAVAILABLE`, `STALE_ADAPTER_GENERATION`;
3. each of the three profile-invalid reasons in both minimal-null and retained permitted profile-derived-observation forms;
4. profile-verified with broker/tool/route observations all null and with each permitted source-backed form present;
5. each of the four recovery-required reasons retaining permitted profile/history/operator observations and retaining permitted broker/tool/route observations, plus the minimal-null forms;
6. candidate-null and candidate-present unconfigured/migration, each with capability digest null and only unconfigured/reference method rows;
7. first-generation active/degraded with null predecessor authority;
8. operator triple variants across representative unconfigured/profile-invalid/profile-verified/active/degraded/recovery families: all null, kind+epoch with null receipt, and kind+epoch+receipt.

One witness cannot stand in for a different reason or nullable family. This matrix is a minimum completeness requirement, not optional coverage guidance. Migration-required adds two separate one-axis, correctly re-digested negatives: non-null `capability_digest`, and one configured native/caller-driver method row; each must hit its exact migration-state invariant rather than a tuple/digest/other-state validator.

## 7. Digest and test-oracle rules

Both public documents use root-only self-digest omission; nested records retain their bytes and no internal grant shape digest exists. Independent test oracles recompute canonical SHA-256 without calling production helpers.

Every non-digest negative on a self-digested document must use a builder or recompute the otherwise-correct root digest after mutation; changed nested self-digested content must be reissued through each containing outer document. Stale digest failures count only in dedicated digest-tamper tests.

Direct schema assertions after local `$defs/$ref` resolution are mandatory for:

- `ActivationReadback.methods`: unconditional exact `minItems=6`, `maxItems=6` plus the four-value readback backend enum;
- `route_profile_ids`: `0..64`;
- `evidence_sources`: `0..8` plus exact item enum;
- `WorkbenchGrantSet.principal_ids`: `1..64`;
- `WorkbenchGrantSet.capabilities`: `1..64`.

Generic validation/error types do not replace schema assertions, especially where finite enums force duplicates at max+1.

A module-local registry contains exactly the two top-level schema-version keys. A1c owns the canonical cross-module registry/loader/generator.

## 8. RED and minimum-sufficient tests

### Assertion RED

First add only `importlib.util.find_spec("aar.provider_ready_runtime_models")` and run the focused test file; fail at assertion, not collection/import.

### Behavioral RED

After an importable skeleton exists, prove at least one state/reason/nullability or planner-ready contradiction is accepted before validators are completed. Capture targeted failure with an otherwise-correct digest.

### Focused T0/T1 cases

At minimum:

1. exact fields/counts, required/no-default/extra-forbid, strict/no-bool scalar and recursive schema shape;
2. BackendAvailability reference/configured/unconfigured/nullability and artifact.put backend matrix;
3. PlannerReadback null/non-null/ready matrix;
4. exact six methods order, uniqueness and direct emitted bound assertion;
5. route/evidence/principal/capability exact min/max/order/duplicate/schema-bound cases without strict-list false positives;
6. representative valid document for every readback state plus the positive nullable variants named above; every reason-family negative and critical nullability contradiction is a one-axis mutation from a valid witness with recomputed root digest;
7. active/degraded runtime-generation method binding and planner-ready distinction;
8. operator epoch/receipt triple rules and receipt-vs-marker digest naming boundary;
9. grant-set collection/budget/TTL/self-digest rules and explicit absence of cross-document intent claims;
10. IssuedWorkbenchGrant exact 20-field shape, budget bounds, no public schema/digest, time order and required revoked bool;
11. independent readback/grant-set digest oracles, wrong-root rejection and nested-change propagation;
12. inert source/import boundary: no runtime/provider/MCP/issuer/DB/filesystem/env/clock/process/network behavior;
13. exact two-key local registry.

Run only:

```bash
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-cache --isolated --python 3.12 --frozen pytest -q -p no:cacheprovider tests/test_provider_ready_runtime_models.py
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-cache --isolated --python 3.12 --frozen ruff check --no-cache src/aar/provider_ready_runtime_models.py tests/test_provider_ready_runtime_models.py
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-cache --isolated --python 3.12 --frozen ruff check --no-cache --select W291,W293 src/aar/provider_ready_runtime_models.py tests/test_provider_ready_runtime_models.py
```

No full suite/T2/real runtime/issuer/clock/DB/filesystem/provider work is warranted for this pure-model slice.

## 9. Forbidden/out of scope

- every existing A1a/A1b-op1 file;
- evaluator alias/classification/paired models (A1b-eval);
- canonical registry/byte loader/generator/checked-in schema/fixture/package assets (A1c/A2);
- state observer, status CLI, history/current/marker scan, Ready/discovery, supervisor, adapter instantiation or health;
- grant issuer storage, mint/lookup/revoke/expiry, context resolver, public Grant projection, admission or operation creation;
- runtime/provider/MCP/transport, SQLite/filesystem/process/env/network/credentials/install/live/T2+/push/tag/release;
- SDD/control/evidence paths.

Every targeted negative is a one-axis mutation from one of those full valid witnesses, recomputes every affected root/nested/outer digest, and asserts the exact intended error location plus stable validator message fragment. Broad `ValidationError`, tuple typing, stale digest or another state validator cannot satisfy it. Python-mode payloads keep tuples; JSON-mode arrays use `model_validate_json` intentionally. Direct resolved schema checks are unconditional and cannot be skipped because a chosen representation omits a bound.

## 10. Stop/reopen

Return `BLOCKED` without widening scope if:

- timestamp or state/nullability rules cannot be derived exactly from frozen authority;
- a valid state requires external-source truth that cannot be represented as document-local validation;
- implementation needs any path beyond the two owned files or any shared/runtime/issuer behavior;
- a focused failure credibly requires T2+ evidence.

## 11. Handoff

After final focused GREEN and both Ruff gates PASS, stage exactly the two owned paths and locally commit only if dispatch explicitly authorizes it. Report exact parent/packet, RED evidence, model inventory, final commands, file hashes, commit/status and confirmation that forbidden/live/release paths were untouched. Do not self-certify integration, row closure or candidate readiness.
