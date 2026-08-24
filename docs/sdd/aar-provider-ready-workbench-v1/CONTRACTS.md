# Contracts

All proposed documents use strict Draft 2020-12 JSON schemas, `additionalProperties: false`, canonical UTF-8 JSON, and `sha256:` lowercase digests over canonical bytes. Schema generation and cross-validator fixtures belong to implementation; examples here are normative field inventories, not generated schema artifacts.

## 0. Canonical digest domains

For every document with a self-digest, the digest domain is the complete strict document with **only that document's own digest field omitted**. Nested documents retain their already-computed digests. Canonicalization is the predecessor canonical JSON algorithm; no whitespace, map-order, path, timestamp normalization, or environment expansion occurs after validation.

| Document | Self-digest | Excluded field | Required equality |
|---|---|---|---|
| host activation intent | `intent_digest` | root `intent_digest` only | equals `intent.intent_digest` in the final profile and the legacy-named migration-attestation `profile_digest` field |
| final host activation profile | `profile_digest` | root `profile_digest` only | equals activation readback and grant-set binding; it is not stored in the pre-existing v6 attestation |
| method adapter manifest | `manifest_digest` | root `manifest_digest` only | equals the intent adapter entry and instantiated factory readback |
| activation-generation authority record | `authority_digest` | root `authority_digest` only | equals its immutable history filename/content digest, the exact derived `current.json` copy, activation readback and grant binding; a committed terminal marker binds it only through its already-computed nested receipt |
| cutover plan | `plan_digest` | root `plan_digest` only | equals the nested plan and explicit plan binding in the prepared marker and the apply command binding |
| operator prepared marker | `prepared_marker_digest` | root `prepared_marker_digest` only | equals the migration/restore transaction binding; its already-computed nested plan/snapshot digests remain present |
| cutover receipt | `receipt_digest` | root `receipt_digest` only | equals the receipt object nested unchanged in the downstream terminal marker and status readback for either an observed v5 registry or an empty-runtime v5 bootstrap |
| restore receipt | `receipt_digest` | root `receipt_digest` only | equals the receipt object nested unchanged in the downstream terminal marker and pre-frontier restore readback |
| operator terminal marker | `marker_digest` | root `marker_digest` only | binds one already-computed cutover/restore receipt without being nested back into that receipt |
| activation readback | `readback_digest` | root `readback_digest` only | equals status output/readback evidence |
| workbench grant set | `grant_set_digest` | root `grant_set_digest` only | equals Ready/discovery, activation readback and issuer policy binding |
| provider alias attestation | `alias_digest` | root `alias_digest` only | equals the paired evaluation admission binding |
| evaluation evidence classification | `classification_digest` | root `classification_digest` only | equals one arm binding in the paired evaluation admission |
| paired evaluation admission | `admission_digest` | root `admission_digest` only | equals one immutable benchmark planning document; it is not physical-launch or replay authority |

### Acyclic activation construction

1. Validate `aar.host-activation-intent.v1` and compute `intent_digest` with only its own digest omitted.
2. Bind that digest into the existing registry-v6 migration attestation. For compatibility, its field named `profile_digest` MUST equal `intent_digest`; it does **not** mean the later final-profile digest.
3. Commit migration DDL plus attestation and obtain `migration_attestation_digest`.
4. Build `aar.host-activation-profile.v1` from the exact complete intent plus that attestation digest.
5. Compute final `profile_digest` with only the final root field omitted.
6. Under the exclusive runtime-home lock, publish one immutable `aar.activation-generation-authority.v1` history record by compare-and-swap against the exact append-only history tip. History publication plus parent fsync is the generation commit linearization point. Atomically replace `current.json` only as a derived pointer to that exact record.
7. Supervisor activation, readback, capabilities and grants require the immutable history tip, derived current pointer, final `profile_digest`, and `authority_digest` to agree; cutover identity binds the pre-cutover `intent_digest`.

A document MUST NOT contain a nested digest of itself. Candidate asset digests bind external package/contract/skill bytes. Any equality or construction-order mismatch is certain failure before authority or provider send.

### Acyclic operator-marker construction

1. Compute and validate the cutover plan `plan_digest`.
2. Under exclusive apply ownership, create and verify the standalone snapshot, then build `aar.operator-prepared-marker.v1` from the complete plan, snapshot and final precheck observations; compute `prepared_marker_digest` with only that root field omitted.
3. Publish that prepared marker exactly once. Migration attestation or restore replacement binds its already-computed digest; neither transaction embeds a terminal marker.
4. After authoritative DB/profile/history or restore readback, build the appropriate cutover/restore receipt and compute `receipt_digest` with only that receipt field omitted. A receipt contains `prepared_marker_digest` but no terminal marker or terminal-marker digest.
5. Build `aar.operator-terminal-marker.v1` around the complete already-computed receipt and compute `marker_digest` with only the terminal root field omitted. Publish that marker as the epoch terminal file. Status returns the nested receipt unchanged and separately reports the outer marker digest when required.

This order has no fixed point: `plan → prepared marker → transaction/receipt → terminal marker`. Swapping a receipt across markers, mutating either layer, or nesting a terminal digest back into its receipt is invalid.

## 1. Activation intent and final profile

### `aar.host-activation-intent.v1`

This operator-authored, pre-cutover document contains all intended candidate, factory, route, grant and recovery inputs but no migration-attestation digest.

### v1 lexical, collection, and validation domains

All validation below occurs before canonical digest computation. Every lexical domain is strict ASCII and performs no Unicode normalization, case folding, environment expansion, path resolution, URL resolution, import resolution, or shell parsing. An identifier is data only; no v1 model may execute or dereference its contents.

| Field | Normative wire domain | Semantics / owner |
|---|---|---|
| `profile_id` | predecessor `OpaqueToken`: 1–128 ASCII characters matching `^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$` | Activation-profile identity. |
| `activation_generation` | predecessor `PositiveCounter`: integer `1..9223372036854775807` | The strict model validates only the wire range. The local-file activation-generation authority record is the unique per-`profile_id` history owner; its CAS rejects a generation less than or equal to the last committed generation. |
| `previous_activation_authority_digest` | predecessor `Digest` or `null` | Exact immutable per-profile history-tip digest, which must also equal derived `current.json`; `null` is valid only when no history record exists. It is a stale-plan/CAS precondition, not a self-reference. |
| `candidate.package_version` | 1–64 ASCII characters matching `^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:(?:a|b|rc)(?:0|[1-9][0-9]*))?(?:\.post(?:0|[1-9][0-9]*))?(?:\.dev(?:0|[1-9][0-9]*))?$` | Canonical public candidate version in the supported three-release-component subset; every numeric component is exactly `0` or has no leading zero, and no local-version suffix is allowed. |
| `candidate.source_commit` | exactly 40 lowercase hexadecimal characters | Exact Git source identity. |
| every `*_digest` | predecessor `Digest`: `sha256:` plus 64 lowercase hexadecimal characters | Content identity in the named domain. |
| `runtime.database_identity` | predecessor `OpaqueToken` | Owner-issued stable database identity, never a filesystem path. |
| `runtime.required_registry_version` | literal integer `6` | Registry-v6 only. |
| `runtime.programmable_backend` / `runtime.security_profile` | literals `ipython` / `trusted_local` | No alternate v1 runtime coordinates. |
| `planner.mode` / `planner.method` / `planner.directive_schema_version` | `planner.mode` is `caller_delegated_ticketed | service_managed`; method/version remain literals `model.request` / `aar.rlm-directive.v1` | The mode selects the exact `model.request` adapter manifest as root-planner factory authority; no separate import/constructor namespace exists. |
| `routes.allowed_profile_ids[]` | predecessor `ModelRouteValue`: 1–128 strict ASCII characters matching `^[A-Za-z0-9][A-Za-z0-9._~:/+\-]*$` | Exact members of the digest-bound route catalog; values are identifiers, not provider coordinates. |
| `grant_policy.principal_patterns[]` | predecessor `OpaqueToken` | Despite the legacy field name, v1 semantics are exact principal-ID equality only. Glob, regex, prefix and substring matching are unsupported; a future matching language requires a new schema version. |
| `grant_policy.capabilities[]` | predecessor `CapabilityName`: lowercase segmented capability syntax | Exact grant capabilities. |
| `cutover_authority_store_id` | 25–152 ASCII characters matching `^local-file-authority-v1:[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$` | Identity of the reviewed shared cutover/runtime-initialize/activation local-file authority implementation; despite the retained field name, it is not cutover-only and is never a path or endpoint. |
| adapter `method` | `model.request | subagent.submit | subagent.result | evidence.query | artifact.put | effect.propose` | Exact frozen v8 workbench broker-catalog domain. The seventh generic `BrokerMethodName`, `artifact.read`, is not an activatable workbench method and MUST reject here. |
| adapter `contract_id` / `factory_id` | predecessor `CapabilityName` | Package-owned immutable registry identifiers. |
| adapter `adapter_id` | predecessor `OpaqueToken` | Host adapter identity. |
| adapter `evidence_tier` | frozen capability domain `unknown | caller_observed | host_receipt_bound | provider_attested` | Exact value projected into configured native/caller v8 capability rows. Evaluator route/usage/visibility is separately owned by `aar.evaluation-evidence-classification.v1`; there is no runtime observation-tier mapping. |

Collection canonical form is part of the strict document and therefore part of every containing digest:

- `adapters` contains `1..6` manifests, is a unique subset sorted by frozen catalog rank `model.request`, `subagent.submit`, `subagent.result`, `evidence.query`, `artifact.put`, `effect.propose`, and has at most one manifest per method;
- `routes.allowed_profile_ids`, `grant_policy.principal_patterns`, and `grant_policy.capabilities` each contain `1..64` values, are sorted by ascending validated wire value, and contain no duplicate;
- empty arrays, duplicate keys, duplicate values, and non-canonical supplied order are rejected by strict validation;
- collection order carries no operator preference. An `issue`/builder may sort validated input into canonical order, while direct JSON/document validation MUST reject non-canonical order rather than silently rewriting signed or digested bytes;
- these rules are owned by this contract and MUST NOT be inferred from another catalog by analogy.

`planner` is one strict mode-tagged object with the same four required keys shown in the example; only `mode` varies over `caller_delegated_ticketed | service_managed`. The wire schema need not manufacture duplicate `oneOf` branches whose fields are otherwise identical; the cross-object validator owns the mode-dependent invariant. `caller_delegated_ticketed` requires the unique `model.request` manifest to have `backend_kind="caller_driver"`. `service_managed` requires that manifest to have `backend_kind="native"`. In either mode, that manifest's package-owned `factory_id`, `factory_digest` and method contract, together with the intent's route catalog binding, are the sole root-planner constructor authority. The supervisor resolves it through the same immutable factory registry used for method capabilities and projects those exact identifiers into planner readback; a second planner factory ID, arbitrary import, or profile-only constructor is invalid. The directive schema version/digest are identical in both modes.

For `aar.method-adapter-manifest.v1`, `backend_kind="reference"` requires `reference_only=true` and `evidence_tier="unknown"`; either `backend_kind="native"` or `backend_kind="caller_driver"` requires `reference_only=false`. The v1 pair `method="artifact.put"` plus `backend_kind="caller_driver"` is invalid: frozen registry-v6 caller-work request/DDL domains do not carry `artifact.put`; an activatable artifact manifest is therefore `native` or `reference`. This narrows activation only and does not change the frozen v8 capability enum/catalog. Whether a package-owned factory is fake or deterministic when its manifest uses another allowed backend kind is verified later by immutable factory-registry integration; the strict model does not infer implementation properties from an ID.

### Required fields

```json
{
  "schema_version": "aar.host-activation-intent.v1",
  "profile_id": "hermes-caller-luna-max-v1",
  "activation_generation": 1,
  "previous_activation_authority_digest": null,
  "candidate": {
    "package_version": "0.6.0a0",
    "source_commit": "<40 lowercase hex>",
    "wheel_digest": "sha256:<64 hex>",
    "contract_manifest_digest": "sha256:<64 hex>",
    "skill_digest": "sha256:<64 hex>"
  },
  "runtime": {
    "runtime_home_digest": "sha256:<64 hex>",
    "database_identity": "<owner-defined stable identity>",
    "required_registry_version": 6,
    "programmable_backend": "ipython",
    "security_profile": "trusted_local"
  },
  "planner": {
    "mode": "caller_delegated_ticketed",
    "method": "model.request",
    "directive_schema_version": "aar.rlm-directive.v1",
    "directive_schema_digest": "sha256:<64 hex>"
  },
  "adapters": ["<aar.method-adapter-manifest.v1 objects>"],
  "routes": {
    "catalog_digest": "sha256:<64 hex>",
    "allowed_profile_ids": ["hermes-codex-luna-max"],
    "fallback_policy": "none",
    "cache_policy": "disabled"
  },
  "grant_policy": {
    "principal_patterns": ["aar-eval-runner"],
    "capabilities": ["rlm.workbench.execute", "rlm.workbench.read"],
    "budget_ceiling": {
      "wall_time_ms": 900000,
      "model_requests": 128,
      "input_tokens": 9223372036854775807,
      "output_tokens": 9223372036854775807,
      "child_operations": 64,
      "artifact_bytes": 33554432
    },
    "max_deadline_ms": 900000
  },
  "cutover_authority_store_id": "local-file-authority-v1:runtime-primary",
  "recovery_compatibility_digest": "sha256:<64 hex>",
  "intent_digest": "sha256:<64 hex>"
}
```

### `aar.host-activation-profile.v1`

The final profile is generated only after a successful v5→v6 cutover, including the empty-runtime v5-bootstrap path:

```json
{
  "schema_version": "aar.host-activation-profile.v1",
  "intent": "<complete validated aar.host-activation-intent.v1 object>",
  "migration_attestation_digest": "sha256:<64 hex>",
  "profile_digest": "sha256:<64 hex>"
}
```

### `aar.activation-generation-authority.v1`

This strict sidecar record is one append-only link in the unique durable history for one `profile_id`. Its committed location is `<runtime-home>/authority/activations/<profile_id>/history/<activation_generation>-<authority_digest>.json`; `profile_id` and the generated filename components are path-segment safe under their frozen domains. `current.json` contains the exact same record bytes but is only a replaceable derived pointer. The document contains no path or timestamp:

```json
{
  "schema_version": "aar.activation-generation-authority.v1",
  "authority_store_id": "local-file-authority-v1:runtime-primary",
  "profile_id": "hermes-caller-luna-max-v1",
  "activation_generation": 1,
  "intent_digest": "sha256:<64 hex>",
  "profile_digest": "sha256:<64 hex>",
  "migration_attestation_digest": "sha256:<64 hex>",
  "previous_activation_authority_digest": null,
  "authority_digest": "sha256:<64 hex>"
}
```

The first history record requires `previous_activation_authority_digest=null`; every successor requires exact equality with the highest valid history-tip digest and a strictly larger `activation_generation`. Under the stable exclusive runtime-home lock, the writer validates the complete chain and all committed terminal markers, exclusively creates/fsyncs a temporary history file, atomically publishes the final immutable history path, and fsyncs the history directory. That publication is the activation-generation commit linearization point. It then temp-writes/fsyncs/atomically replaces `current.json` and fsyncs its parent as a derived pointer.

Cold-start and reconcile scan every strict history filename/document plus every committed cutover marker. There must be one unbranched digest-linked chain from the null predecessor, no duplicate generation, no fork, and every terminal-marker authority digest must resolve to exactly one chain member. `current.json` must equal the highest valid tip; an absent/stale pointer may be repaired only by explicit reconcile, while a missing history member, fork, lower/equal rewrite, terminal-marker disagreement, or deleted/newer-tip rollback visible in any retained authority artifact is `CUTOVER_RECOVERY_REQUIRED`. History files and committed markers are never overwritten or deleted.

The v1 local-file owner does not claim Byzantine rollback detection if an offline adversary atomically restores the DB **and every** history/current/marker byte to one older mutually consistent image. That threat requires a future external monotonic/CAS authority and a superseding contract. Normal crash, partial deletion, pointer rollback, stale backup restore, and owner-process races are in scope and fail closed. The final profile intentionally omits `authority_digest`, avoiding a digest cycle. Terminal markers, activation readback and grants bind both profile and history-tip digests.

### Invariants

- no secret-bearing field, env expansion, shell command, arbitrary job endpoint, or credential bytes;
- candidate/route/adapter/grant/recovery digests are contained by `intent_digest`; final profile contains that exact intent and the resulting migration-attestation digest;
- the legacy v6 attestation `profile_digest` equals `intent_digest`; activation readback and grants equal the distinct final `profile_digest`;
- route profiles are an allowlist, never caller-supplied arbitrary coordinates;
- `activation_generation` is an operator-controlled intent value whose monotonicity and non-reuse are committed only by the per-profile activation-generation authority CAS; a document alone proves only wire validity;
- `runtime_generation` is allocated by registry startup, must advance beyond the prior runtime generation, and is not required to equal `activation_generation`;
- derived v8 `adapter_generation` equals the current `runtime_generation`; capability/readback binds both generations and the final profile digest;
- `budget_ceiling.wall_time_ms` is an inclusive integer `1000..900000`, `model_requests` is `1..128`, `child_operations` is `0..64`, `artifact_bytes` is `0..33554432`, and token ceilings are `0..9223372036854775807`; `grant_policy.max_deadline_ms` is `1000..900000`. These bounds guarantee that every wire-valid active profile can issue at least one frozen workbench context rather than validating a non-issuable zero-deadline policy;
- an issued context budget must be component-wise less than or equal to the ceiling; cumulative retry/recovery/discarded usage consumes the same units;
- `max_deadline_ms` caps `absolute_deadline - mint_time`; the earlier absolute caller deadline still wins;
- changing any bound intent identity requires a new intent/final profile digest and fresh runtime generation;
- activation is refused unless the migration attestation binds the same intent/candidate/contract inputs and the final profile recomputes from them.

## 2. `aar.method-adapter-manifest.v1`

```json
{
  "schema_version": "aar.method-adapter-manifest.v1",
  "method": "model.request",
  "contract_id": "aar.broker-contract.model-request.v2",
  "request_schema_digest": "sha256:<64 hex>",
  "response_schema_digest": "sha256:<64 hex>",
  "backend_kind": "caller_driver",
  "factory_id": "aar.caller-work.model-request.v1",
  "factory_digest": "sha256:<64 hex>",
  "adapter_id": "hermes-caller-driver",
  "adapter_generation_policy": "runtime_generation",
  "reference_only": false,
  "evidence_tier": "caller_observed",
  "lookup_supported": true,
  "cancel_supported": false,
  "manifest_digest": "sha256:<64 hex>"
}
```

Allowed `backend_kind`: `native`, `caller_driver`, `reference`, subject to the frozen-owner cross-field restriction above. `unconfigured` is a derived capability row, not an activatable manifest.

The manifest `method` domain and relative order are exactly the six frozen v8 broker-catalog methods. Generic `artifact.read` remains available to lower-layer broker code where already supported, but it is neither accepted in this manifest nor projected into the frozen v8 workbench capability.

`factory_id` resolves only through a package-owned immutable registry. Each registry row binds factory callable bytes/import path, implementation digest, allowed backend kind, method contract digests, and supported frozen capability evidence tier. Profiles cannot name arbitrary Python imports, shell commands, entry points, URLs, or code. The only v1 `adapter_generation_policy` is `runtime_generation`; the runtime writes the actual current runtime generation into capability rows and receipts.

An implementation factory MUST instantiate the exact method contract before capability output reports `configured=true`. Native/caller rows project the exact manifest evidence tier. A valid reference factory projects the frozen row `configured=true`, `reference_only=true`, `adapter_id=null`, `adapter_generation=null`, `evidence_tier=unknown`; it is never admission-usable. Missing, stale or factory-unbound rows project `unconfigured/false/false/null/null/unknown`. A profile cannot claim `reference_only=false` for fake/deterministic backends.

## 3. Shared operator-document wire domains

The following nested records are strict objects with `additionalProperties: false`. Every property shown is required; `null` is legal only where the type explicitly includes it.

| Record | Exact properties and domains |
|---|---|
| `FileArtifact` | `artifact_id: OpaqueToken`; `digest: Digest`; `size_bytes: integer 0..9223372036854775807` |
| `SidecarObservation` | `state: absent | present`; `file_identity: OpaqueToken | null`; `size_bytes: integer 0..9223372036854775807 | null`; `digest: Digest | null`. `absent` requires all three nullable values to be null; `present` requires all three non-null. |
| `DatabaseChecks` | `integrity_result: ok`; `foreign_key_violation_count: 0` |
| `CandidateBinding` | `package_version: canonical package version from §1`; `source_commit: exactly 40 lowercase hex`; `wheel_digest: Digest`; `contract_manifest_digest: Digest`; `skill_digest: Digest` |
| `EpochOwnerBinding` | `authority_store_id: cutover authority store ID from §1`; `operator_identity_digest: Digest`; `runtime_owner_state: absent`; `exclusive_lock_state: available` |

`UnixMs` means an integer `0..9223372036854775807`. `OperatorPath` means a strict UTF-8 string of `1..4096` code points with no NUL; it is operator CLI input/output only, is normalized once before document construction, and is never accepted from a job, planner directive, grant, or provider response. Timestamp order is validated rather than normalized.

### `aar.cutover-plan.v1`

A valid plan is emitted only after all read-only checks pass. All properties below are required and no others are legal:

| Property | Exact wire domain |
|---|---|
| `schema_version` | literal `aar.cutover-plan.v1` |
| `cutover_epoch` | `OpaqueToken` |
| `runtime_home_digest` | `Digest` |
| `database_identity` | `OpaqueToken` |
| `database_file` | `FileArtifact` describing the observed source DB |
| `owner` | `EpochOwnerBinding` |
| `source_registry_version` | literal integer `5`; an already-v6 root returns existing status/receipt and does not issue a new cutover plan |
| `source_registry_schema_digest` | exact supported v5 schema `Digest` |
| `canonical_v5_row_set_digest` | required non-null `Digest` |
| `wal` | `SidecarObservation` |
| `shm` | `SidecarObservation` |
| `database_checks` | `DatabaseChecks` |
| `nonterminal_counts` | strict object with required nonnegative integer fields `operations`, `attempts`, `workbench_jobs`, `caller_tickets`, `workers`; every value must be `0` for an issuable plan |
| `snapshot` | strict object with `snapshot_id: OpaqueToken`, `destination: OperatorPath`, `backup_mode: sqlite_backup`; no digest exists before apply |
| `candidate` | `CandidateBinding` |
| `migration_sql_digest` | exact frozen v6 migration `Digest` |
| `activation_intent_digest` | `Digest` |
| `final_profile_output` | `OperatorPath` |
| `profile_id` | `OpaqueToken` |
| `proposed_activation_generation` | `PositiveCounter` |
| `previous_activation_authority_digest` | `Digest | null` |
| `authority_store_id` | exact ID equal to `owner.authority_store_id` and the intent |
| `allowed_recovery_actions` | sorted unique tuple of `abort | apply | reconcile | status`, length `1..4` |
| `created_at_unix_ms` | `UnixMs` |
| `expires_at_unix_ms` | `UnixMs`, strictly greater than creation |
| `plan_digest` | self `Digest` from §0 |

Plan construction opens SQLite in read-only/query-only mode and only stats/hashes existing DB/WAL/SHM files. It MUST NOT execute any WAL checkpoint pragma, create/delete/truncate a sidecar, create a snapshot/temp DB/authority directory, acquire a write transaction, stop a service, refresh a token, or call a provider. A write canary around the runtime home and SQLite authorizer must remain at zero. A hot/unreadable/unclassifiable sidecar or any owner/nonterminal row fails without emitting a valid plan.

### `aar.operator-prepared-marker.v1`

This is the single prepared authority for cutover or restore. Every property is required and no others are legal:

| Property | Exact wire domain |
|---|---|
| `schema_version` | literal `aar.operator-prepared-marker.v1` |
| `kind` | `cutover | restore` |
| `epoch` | `OpaqueToken` |
| `operator_identity_digest` | `Digest` binding host boot identity, process start identity and the operation input digest; raw PID is insufficient |
| `prepared_at_unix_ms` | `UnixMs` |
| `cutover` | `CutoverPreparation | null` |
| `restore` | `RestorePreparation | null` |
| `prepared_marker_digest` | self `Digest` from §0 |

`kind=cutover` requires non-null `cutover`, null `restore`, `epoch=cutover.plan.cutover_epoch`, a root `operator_identity_digest` for the current apply process/input (the nested plan owner remains planning provenance), and `plan.created_at_unix_ms <= prepared_at_unix_ms < plan.expires_at_unix_ms`. `CutoverPreparation` is a strict object with the complete validated `plan`, equal `plan_digest`, verified standalone `snapshot: FileArtifact`, `pre_migration_database_digest: Digest`, and final-precheck `wal`/`shm: SidecarObservation`; snapshot artifact ID and bytes equal `plan.snapshot.snapshot_id` and the bytes at its normalized destination, while DB/sidecar observations equal the plan and unchanged final precheck. `kind=restore` requires null `cutover`, non-null `restore`. `RestorePreparation` is a strict object with `cutover_plan_digest`, `cutover_prepared_marker_digest`, required `cutover_terminal_marker_digest` naming either the exact pre-DB `cutover_aborted` terminal or the exact `cutover_recovery_required` terminal being resolved, verified `snapshot: FileArtifact`, `runtime_home_digest`, `target_database_identity`, `pre_restore_database: FileArtifact`, pre-restore `wal`/`shm: SidecarObservation`, and the three required true booleans later copied exactly into the restore receipt's `frontier_proof`.

The final snapshot is created and verified before this marker. If the process crashes after snapshot publication but before prepared-marker publication, no operation authority exists yet. An exact rerun may adopt that snapshot only when destination, size/digest, plan, source DB identity/digest, sidecars, intent/candidate and operator input all still match; it then publishes this marker once. A mismatching or ambiguous final snapshot is `CUTOVER_RECOVERY_REQUIRED` and is never overwritten or deleted automatically. An operation-owned temporary snapshot that was never atomically published may be removed after proving no prepared marker references it. Same epoch plus different bytes is conflict.

## 4. Terminal operator receipts

The two receipt schemas use the shared records above. Every listed property is required and no extras are legal; nullable fields retain observed uncertainty instead of fabricating completion.

### `aar.cutover-receipt.v1`

| Property | Exact wire domain |
|---|---|
| `schema_version` | literal `aar.cutover-receipt.v1` |
| `cutover_epoch` | `OpaqueToken` |
| `plan_digest` | `Digest` |
| `outcome` | `committed | aborted_before_db_commit | recovery_required` |
| `database_commit_state` | `not_committed | committed | unknown` |
| `prepared_marker_digest` | exact `aar.operator-prepared-marker.v1` self `Digest` |
| `snapshot` | `FileArtifact` |
| `pre_migration_database_digest` | `Digest` |
| `canonical_v5_row_set_digest` | `Digest` |
| `migration_sql_digest` | `Digest` |
| `migration_attestation_digest` | `Digest | null` |
| `profile_id` | `OpaqueToken` |
| `activation_generation` | `PositiveCounter` |
| `previous_activation_authority_digest` | `Digest | null` |
| `activation_authority_digest` | `Digest | null` |
| `post_migration_database_digest` | `Digest | null` |
| `database_checks` | `DatabaseChecks | null` |
| `candidate` | `CandidateBinding` |
| `intent_digest` | `Digest` |
| `generated_profile_digest` | `Digest | null` |
| `generated_profile_output` | `OperatorPath` |
| `started_at_unix_ms` | `UnixMs` |
| `db_committed_at_unix_ms` | `UnixMs | null` |
| `completed_at_unix_ms` | `UnixMs` |
| `receipt_digest` | self `Digest` |

`committed` requires `database_commit_state=committed`, non-null attestation/authority/post-DB/checks/profile fields, ordered timestamps, exact v6 DB readback, and activation history/current-pointer equality. `aborted_before_db_commit` requires `not_committed` and all post-commit nullable fields null. `recovery_required` records only facts proven by readback; `unknown` never authorizes retry or restore. The downstream terminal-marker kind must correspond exactly to the outcome table below; the receipt itself contains no terminal marker. An empty runtime home is first initialized by the existing `Registry` transaction to canonical empty v5, then uses this exact plan/apply/receipt path; therefore its committed receipt and frozen v6 attestation contain a real backup snapshot and canonical empty-v5 row-set digest rather than a synthetic fresh-v6 claim.

### `aar.restore-receipt.v1`

| Property | Exact wire domain |
|---|---|
| `schema_version` | literal `aar.restore-receipt.v1` |
| `restore_epoch` | `OpaqueToken` |
| `cutover_plan_digest` | `Digest` |
| `outcome` | `restored_pre_frontier | recovery_required` |
| `replacement_commit_state` | `not_committed | committed | unknown` |
| `snapshot` | `FileArtifact` |
| `runtime_home_digest` | `Digest` |
| `target_database_identity` | `OpaqueToken` |
| `pre_restore_database` | `FileArtifact` |
| `pre_restore_wal` | `SidecarObservation` |
| `pre_restore_shm` | `SidecarObservation` |
| `restored_database_digest` | `Digest | null` |
| `sidecar_disposition` | `unchanged | absent | removed_after_verified_replace` |
| `database_checks` | `DatabaseChecks | null` |
| `cutover_prepared_marker_digest` | exact cutover `aar.operator-prepared-marker.v1` self `Digest` |
| `cutover_terminal_marker_digest` | required exact outer `aar.operator-terminal-marker.v1` digest for the original cutover epoch; kind is `cutover_aborted` or `cutover_recovery_required` as proven by pre-restore state |
| `restore_prepared_marker_digest` | exact restore `aar.operator-prepared-marker.v1` self `Digest` |
| `activation_authority_digest_before_restore` | `Digest | null` |
| `activation_authority_digest_after_restore` | `Digest | null` |
| `activation_authority_disposition` | literal `unchanged` |
| `frontier_proof` | strict object with required booleans `no_committed_v6_attestation`, `no_activation_history_record`, `no_v6_runtime_or_workbench_write`; all must be true for restored outcome |
| `started_at_unix_ms` | `UnixMs` |
| `replacement_committed_at_unix_ms` | `UnixMs | null` |
| `completed_at_unix_ms` | `UnixMs` |
| `receipt_digest` | self `Digest` |

`restored_pre_frontier` requires committed replacement, non-null restored digest/checks/replacement time, equal before/after activation authority, no frontier violation, and an exact `cutover_terminal_marker_digest` equal to the marker already named in the restore prepared marker. That original marker must be `cutover_aborted` when no DB replacement occurred, or `cutover_recovery_required` when restore resolves an ambiguous/pre-frontier replacement; no committed cutover terminal is restorable. `recovery_required` records uncertainty, never a successful replacement or authority rollback. The receipt contains no new terminal marker.

### `aar.operator-terminal-marker.v1`

This is the epoch terminal file and the only outer marker. Every property is required and no others are legal:

| Property | Exact wire domain |
|---|---|
| `schema_version` | literal `aar.operator-terminal-marker.v1` |
| `kind` | `cutover_committed | cutover_aborted | cutover_recovery_required | restore_committed | restore_recovery_required` |
| `epoch` | `OpaqueToken` |
| `receipt_schema_version` | `aar.cutover-receipt.v1 | aar.restore-receipt.v1` |
| `receipt` | complete strict receipt matching `receipt_schema_version` |
| `published_at_unix_ms` | `UnixMs`, not earlier than the nested receipt completion time |
| `marker_digest` | self `Digest` from §0 |

`epoch` equals the nested cutover/restore epoch. Kind/outcome mapping is exhaustive: `cutover_committed↔committed`, `cutover_aborted↔aborted_before_db_commit`, `cutover_recovery_required↔recovery_required`, `restore_committed↔restored_pre_frontier`, and `restore_recovery_required↔recovery_required`. The marker contains the exact receipt including its already-computed `receipt_digest`; the receipt contains no marker field. Marker/receipt swap, kind mismatch, inner or outer digest tamper, duplicate terminal files for one epoch, or terminal publication before required DB/profile/history/current readback is certain conflict. Exact rerun returns the nested receipt byte-identically. Supervisor unmatched-epoch scanning treats `cutover_aborted` as closed immediately. A `cutover_recovery_required` epoch remains blocking unless exactly one later `restore_committed` terminal contains a restore receipt whose required `cutover_terminal_marker_digest` names that outer cutover marker and whose prepared/snapshot/frontier bindings match; that exact pair terminally resolves the original cutover without rewriting either marker. Missing, duplicate, mismatched, or `restore_recovery_required` successors remain blocking.

## 5. `aar.activation-readback.v1`

This is the strict output of read-only `aar-admin activation status`; it has a self `readback_digest` under §0. Every property is required and no others are legal:

| Property | Exact wire domain |
|---|---|
| `schema_version` | literal `aar.activation-readback.v1` |
| `observed_at_unix_ms` | `UnixMs` |
| `state` | `unconfigured | migration_required | profile_invalid | profile_verified | starting | active | degraded | recovery_required` |
| `reason_code` | `none | REGISTRY_VERSION_UNSUPPORTED | ACTIVATION_PROFILE_INVALID | ACTIVATION_BINDING_MISMATCH | GRANT_POLICY_INVALID | ACTIVATION_HISTORY_CONFLICT | CUTOVER_RECOVERY_REQUIRED | ABORT_OR_APPLY_REQUIRED | RECONCILE_INPUT_REQUIRED | GRANT_BINDING_MISMATCH | PLANNER_UNAVAILABLE | CAPABILITY_UNAVAILABLE | STALE_ADAPTER_GENERATION` |
| `runtime_generation` | `PositiveCounter | null` |
| `supervisor_process_identity_digest` | `Digest | null` |
| `candidate` | `CandidateBinding | null` |
| `registry_schema_version` | integer `5 | 6 | null` |
| `registry_schema_digest` | `Digest | null` |
| `migration_attestation_digest` | `Digest | null` |
| `profile_id` | `OpaqueToken | null` |
| `activation_generation` | `PositiveCounter | null` |
| `intent_digest` | `Digest | null` |
| `profile_digest` | `Digest | null` |
| `previous_activation_authority_digest` | `Digest | null` |
| `activation_authority_digest` | `Digest | null` |
| `grant_set_digest` | `Digest | null` |
| `capability_digest` | `Digest | null` |
| `broker_catalog_digest` | `Digest | null` |
| `tool_surface_digest` | `Digest | null` |
| `methods` | exactly six strict frozen `BackendAvailability` rows in broker-catalog order |
| `planner` | strict object with required `mode: caller_delegated_ticketed | service_managed | null`, `ready: boolean`, `factory_id: CapabilityName | null`, `factory_digest: Digest | null`, `method_manifest_digest: Digest | null`; null mode requires all three bindings null and `ready=false`, while either non-null mode requires all three bindings equal the selected `model.request` manifest and `ready=true` only in `active` state |
| `route_catalog_digest` | `Digest | null` |
| `route_profile_ids` | sorted unique tuple of `ModelRouteValue`, length `0..64` |
| `authority_store_id` | authority-store ID or `null` |
| `authority_history_tip_digest` | `Digest | null` |
| `operator_epoch_kind` | `cutover | restore | null` |
| `operator_epoch` | `OpaqueToken | null` |
| `latest_operator_receipt_digest` | `Digest | null` |
| `evidence_sources` | sorted unique tuple, length `0..8`, values from `installed_assets | registry | activation_history | activation_current | epoch_marker | supervisor | capability_registry | grant_issuer` |
| `readback_digest` | self `Digest` |

Each `methods` row has exactly the frozen fields and domains: `method`, `contract_id`, request/response schema digests, `backend_kind`, `configured`, `reference_only`, nullable `adapter_id`, nullable positive `adapter_generation`, and frozen capability `evidence_tier`. Null `operator_epoch_kind` requires null epoch/receipt; a non-null kind requires its matching prepared/terminal authority, the exact epoch, and null receipt until a matching terminal marker exists. A non-null receipt digest is the nested strict receipt self digest, never the outer marker digest.

`BackendAvailability.backend_kind` is the readback-only domain `unconfigured | native | caller_driver | reference`; it deliberately extends the three-value activation-manifest backend domain with the observed `unconfigured` state. The document-local row matrix is exhaustive:

| `backend_kind` | `configured` | `reference_only` | `adapter_id` / `adapter_generation` | `evidence_tier` |
|---|---:|---:|---|---|
| `unconfigured` | `false` | `false` | both null | `unknown` |
| `reference` | `true` | `true` | both null | `unknown` |
| `native | caller_driver` | `true` | `false` | both non-null | any `CapabilityEvidenceTier`; exact manifest equality is composition evidence |

No fourth combination is valid. `artifact.put` remains ineligible for `caller_driver`. Manifest-tier equality, current-generation equality and adapter health are observer/composition proofs; the strict row validates only the supplied wire combination. In an `active | degraded` document, only native/caller-driver executable rows carry the current runtime generation; reference and unconfigured rows retain null adapter generation exactly as required by this matrix.

State/nullability rules are exhaustive:

| State | Required bindings |
|---|---|
| `unconfigured` | no profile/history/runtime/grant/capability binding; six methods are truthful unconfigured/reference rows; planner is null/not ready |
| `migration_required` | registry version is `5`, reason is `REGISTRY_VERSION_UNSUPPORTED`; every profile/history/runtime/grant/capability binding is null; all six methods are truthful unconfigured/reference rows and no mutation adapter is configured |
| `profile_invalid` | registry is `6`; reason is exactly `ACTIVATION_PROFILE_INVALID | ACTIVATION_BINDING_MISMATCH | GRANT_POLICY_INVALID`; candidate/registry observations are non-null, each profile-derived field is non-null iff its owning document validated before the named failure, runtime/grant/capability bindings are null and methods admit no mutation |
| `profile_verified` | reason is `none`; candidate/attestation/profile/history tip/current equality are non-null; runtime/grant/capability bindings remain null; planner is not ready |
| `starting` | reason is `none`; all candidate/profile/history bindings and a fresh runtime generation are non-null; grant-set/capability/catalog/tool/route bindings remain null and methods/planner remain non-ready until the single Ready publication transitions directly to `active` |
| `active` | reason is `none`; registry is `6`; every candidate/profile/history/runtime/grant/capability/catalog/tool/route field is non-null except the explicitly nullable predecessor-authority and operator tuple; history tip equals current; native/caller-driver executable rows carry the current runtime generation while reference/unconfigured rows retain null generation; planner is ready |
| `degraded` | a generation that previously reached exact `active` is positively observed but disabled after one runtime health/binding failure; reason is exactly `GRANT_BINDING_MISMATCH | PLANNER_UNAVAILABLE | CAPABILITY_UNAVAILABLE | STALE_ADAPTER_GENERATION`; all prior active candidate/profile/history/runtime/grant/capability/catalog/tool/route facts remain non-null, planner is not ready and issuer rejects/revokes grants |
| `recovery_required` | reason is exactly `ACTIVATION_HISTORY_CONFLICT | CUTOVER_RECOVERY_REQUIRED | ABORT_OR_APPLY_REQUIRED | RECONCILE_INPUT_REQUIRED`; detection synchronously removes Ready and retires any running generation/issuer before readback, so runtime/grant/capability fields are null and no mutation grant is authoritative |

Observation selection is deterministic and uses the first matching predicate in this precedence: (1) any ambiguous/conflicting/unmatched authority, DB commit, history/current or reconcile tuple → `recovery_required`; (2) exact v5 with no authority conflict → `migration_required`; (3) absent/uninitialized registry and no authority artifact → `unconfigured`; (4) exact current Ready/discovery plus all bindings → `active`; (5) positively observed running generation disabled by one runtime reason → `degraded`; (6) allocated generation without Ready → `starting`; (7) exact v6 profile/history/current with no generation → `profile_verified`; (8) exact v6 whose profile/policy/binding validation fails → `profile_invalid`. No reason belongs to two states. Any row combination outside this table rejects. A nullable observation is non-null only when its named source validates and remains authoritative under the selected state; otherwise it is null. Non-active states cannot publish a mutation grant. `status` performs no file/DB write, token refresh, provider call, grant mint, migration, host construction, or supervisor start.

For strict document-local validation, source-dependent nullability is preserved rather than guessed:

- `candidate` is an independently installed observation and MAY remain non-null in `unconfigured` or `migration_required`; it is required non-null in `profile_invalid | profile_verified | starting | active | degraded`.
- `migration_required` cannot carry `capability_digest` or a configured native/caller-driver method row; candidate and independently source-backed non-authority observations may still be present.
- `previous_activation_authority_digest` remains nullable in every state, including first-generation `active | degraded`; it is never used as a proxy for whether current history validated.
- `operator_epoch_kind`, `operator_epoch` and `latest_operator_receipt_digest` are governed only by their local triple: null kind requires both others null; non-null kind requires non-null epoch and permits a null receipt until a matching terminal exists. State alone does not make an independently observed operator tuple non-null.
- `profile_invalid` requires candidate plus v6 registry observations, but all profile-derived nullable values remain source-dependent; the pure document MUST NOT infer which owning document validated before the named failure. It only forbids runtime/grant/capability authority, a ready planner and configured mutation rows.
- `profile_verified` requires the validated profile/history bindings named by its state row and null runtime/grant/capability authority. Independently validated broker-catalog, tool-surface and route observations remain source-dependent and MAY be null or non-null; the pure document does not erase them by state alone.
- `active | degraded` require every prior-active binding named by their rows except the legitimately nullable predecessor-authority digest and independently nullable operator tuple above.
- `recovery_required` requires runtime-generation, supervisor, grant-set and capability bindings null. Broker/tool/route observations and validated profile/history/operator evidence remain source-dependent under the selected recovery reason; the pure document MUST NOT erase them merely because recovery is required. Planner readiness and configured mutation rows remain forbidden.

Focused strict-model tests MUST include positive witnesses for each permitted nullable variant above and one-axis negatives whose otherwise-correct root digest is recomputed. Python-mode tests use tuples; JSON-mode arrays are validated through `model_validate_json` so a `tuple_type` failure cannot substitute for the intended state/collection invariant.

## 6. Operator CLI and mutation ownership

```text
aar-admin cutover plan       --runtime-home ... --intent ... --profile-output ...   # canonical JSON on stdout only
aar-admin cutover apply      --plan - | --plan <operator-materialized-file>
aar-admin cutover abort      --plan ...                         # prepared, pre-DB only
aar-admin cutover reconcile  --runtime-home ... --epoch ... --plan ...
aar-admin cutover status     --runtime-home ...
aar-admin cutover restore    --plan ... --snapshot ...          # pre-frontier only
aar-admin runtime initialize --runtime-home ...                         # canonical empty-v5 bootstrap only
aar-admin activation verify  --runtime-home ... --profile ...
aar-admin activation status  --runtime-home ...
```

`plan`, `status`, and `activation verify` are read-only. `cutover plan` accepts no output-path option and emits exactly one canonical `aar.cutover-plan.v1` JSON document plus one trailing newline on stdout, with all diagnostics on stderr; `--plan -` makes `apply` consume those exact bytes from stdin. An operator may materialize stdout with shell redirection, but that external shell write is neither performed nor hidden by the plan command and its resulting file is untrusted input revalidated by `apply`. A plan that is lost before `apply` has authorized no snapshot, marker, DB/profile/history mutation, or epoch; the operator may generate and select a new plan. Once `apply|abort|reconcile|restore` starts, only the exact selected plan bytes embedded in or matching the prepared marker are resumable. `cutover apply|abort|reconcile|restore` and `runtime initialize` are explicit mutation contracts. Every mutator acquires the stable exclusive runtime-home lock, verifies exact command inputs against any immutable prepared marker and current DB/history/current-pointer tuple, and writes only the named authority/profile/receipt targets.

`runtime initialize` accepts only an absent registry file, an exact uninitialized SQLite file with no `schema_migrations`, user tables, WAL/SHM state or domain rows, or an exact canonical v5 registry with zero nonterminal/domain rows and no v6 attestation/history. It creates no initialization directory, cutover plan, snapshot, marker, epoch, receipt, final profile, activation-history record, or current pointer. For the first two cases it runs only the existing `Registry` initialization transaction, which deterministically commits schema versions 1–5; for an exact existing canonical empty v5 it performs no write. It then verifies canonical empty-v5 row bytes and returns ordinary read-only activation status `migration_required`. The operator must separately run `cutover plan`, retain one exact emitted plan, and pass that plan to ordinary `cutover apply`; only that later apply may emit the ordinary cutover receipt. `runtime initialize` never builds or applies a plan in memory. A caught fault before the v5 transaction commit rolls back to absent/uninitialized input; a caught fault after commit leaves canonical empty v5 and an exact rerun is read-only classification. Abrupt process loss is not retry authority: any resulting WAL/SHM residue is `UNINITIALIZED_RUNTIME_RESIDUE` and initialize fails closed without checkpoint, deletion, adoption, automatic retry, or recovery. Because no cutover effect starts inside initialize, plan/epoch/owner/timestamp recovery is owned solely by the later exact materialized plan and ordinary prepared-marker protocol. There is no direct-v6 initializer, synthetic v5 evidence, or separate initialization/fresh receipt/attestation/epoch authority.

Reconcile never silently reruns migration, initialization, restore, or provider work. For a prepared old DB it returns `ABORT_OR_APPLY_REQUIRED` readback and writes nothing. For a proven committed DB with missing profile/history/current/terminal artifacts, it deterministically reconstructs only bytes already fixed by the prepared marker and in-DB attestation, appends the one missing history record if its predecessor is still exact, repairs the derived current pointer, publishes the terminal marker/receipt once, and returns that strict receipt. Any conflicting history fork, stale pointer, ambiguous DB commit, changed path/input, or missing proof emits/preserves `recovery_required` and performs no authority overwrite. Exact completed reruns return the existing byte-identical receipt.

No command silently stops or starts a service. The service owner must stop it before exclusive mutation, and supervisor startup remains refused while any unmatched prepared/recovery marker exists.

## 7. Root-planner caller-work contract

Every planner ticket uses existing caller-work command schemas and `model.request` response contracts.

Strict `logical_owner_json` wire object:

```text
kind = planner
phase = initial | correction | recovery | finalizer
step_index = non-negative integer
```

`step_index` is the only frozen wire name for the planner ordinal; an `ordinal` field MUST NOT be emitted. `operation_id` is owned by the ticket/suspension relational columns. Route binding, planner request and directive-schema digests are owned by the strict canonical request JSON addressed by `request_digest`, as mapped below; they are not extra logical-owner properties.

### Registry-v6 field map

The caller-delegated planner path is admitted only because every authority-bearing value has an exact v6 owner. Implementations MUST use these fields; sidecar-only planner state is forbidden.

| Planner fact | Registry-v6 owner | Equality / uniqueness rule |
|---|---|---|
| operation, phase and planner step | `rlm_workbench_suspensions.operation_id` plus strict `logical_owner_json` fields `kind`, `phase`, `step_index` | canonical owner bytes validate the frozen planner-owner schema; `step_index` is the planner ordinal; owner digest equals `logical_owner_digest` |
| suspension order | `rlm_workbench_suspensions.suspension_revision` | monotonic per operation; primary key `(operation_id, suspension_revision)` |
| control/cancel view | `rlm_workbench_suspensions.control_revision` plus operation control/cancellation revisions | rechecked at settlement and successor transfer |
| method and contract | `broker_method`, `contract_id` | root planner requires `model.request` and exact frozen contract digest |
| planner request identity | `request_digest`, `ticket_id` | unique ticket; owner/request change conflicts rather than reuses identity |
| route and directive binding | canonical request JSON addressed by `request_digest` | request contains route binding/catalog/policy and directive schema digests; those bytes cannot be changed after ticket creation |
| physical attempt and receipt | `caller_work_tickets.physical_attempt_id` plus `caller_work_candidate_receipts` | candidate key is `(ticket_id, physical_attempt_id, receipt_digest)` |
| command CAS | `caller_work_command_receipts` | `(operation_id, idempotency_key)` binds command/result digest and result revision |
| authoritative settlement | caller ticket state plus suspension state; `rlm_workbench_successor_outbox.settlement_digest` exists only for frozen terminal states `settled_success`, `settled_failure`, and `cancelled_certain` | exactly one outbox row per eligible `(operation_id, suspension_revision)`; `cancelled_before_send`, `cancel_requested`, `outcome_unknown`, and `quarantined` have no successor outbox |
| planner successor authority | `operation_attempts`, released `operation_leases`, nullable-cell suspension, and `rlm_workbench_successor_outbox` | cell-free CAS below; successor fence/generation advances and the prior writer is stale |
| route/usage lineage | candidate receipt JSON plus model journal records bound by ticket/physical attempt/request | required receipt fields are joined, never inferred from launch flags |

For `observation.kind="model"` with `outcome="succeeded"`, `CallerWorkCommitInput.model_response` is mandatory and validates as the exact strict `ModelResponse`; for every other observation kind/outcome it is mandatory `null`. The command digest includes those bytes. In one shared SQLite transaction, the current ticket/claim/fence/physical attempt/external idempotency and request/route binding are rechecked, the `result_committed` journal row is inserted or exactly replayed, the candidate is appended, settlement/outbox projection is applied, and the command receipt is recorded. Any changed evidence, stale lineage, route/usage/host/output mismatch, or later insert failure rolls the entire transaction back. A late provider lookup/callback may publish model success only through the sealed equivalent that atomically records the same typed response and candidate; digest-only candidate append remains evidence retention, not model-journal authority.

### Cell-free planner successor CAS on frozen registry v6

A root planner is not a cell. For every planner phase, `rlm_workbench_suspensions.cell_execution_id` MUST be SQL `NULL`; no sentinel or synthetic `rlm_workbench_cells` row is legal. The cell-bound `rlm_workbench_attempt_authority` and `rlm_workbench_rebind_transfers` tables are neither read nor written by planner successor preparation/consumption.

The existing cell-free v6 authority is composed as follows:

1. Require a caller ticket in exactly one frozen outbox-eligible state: `settled_success`, `settled_failure`, or `cancelled_certain`; require exactly one corresponding settled/cancelled nullable-cell suspension and one matching `rlm_workbench_successor_outbox` row, with equal operation/suspension/settlement/outbox bytes and a strict planner owner. A ticket in `cancelled_before_send`, `cancel_requested`, `outcome_unknown`, or `quarantined` is ineligible and must have no successor outbox.
2. Select the predecessor attempt by the unique append-only `operation_events` row with `event_kind="workbench_waiting_external"`, the same operation, `payload_json.suspension_revision` equal to the suspension, and non-null `attempt_no`; join `operation_attempts` on `(operation_id, attempt_no)`. Require that row to be `suspended_external` and require no unreleased lease for it. Zero, duplicate or mismatched events are `PLANNER_AUTHORITY_CONFLICT`; no latest-attempt or timestamp inference is legal.
3. Require the exact current job/operation control, cancellation and cumulative-deadline revisions and an outbox-eligible caller ticket with the exact non-null outbox settlement digest. Only then may a known valid/correction/certain-terminal condition proceed. `cancel_requested`, `outcome_unknown`, and `quarantined` never reach this CAS and remain caller-work reconciliation/suspension-owned.
4. For `pending`, the dispatcher has already created the candidate successor attempt/fence under its existing claim/lease transaction. CAS the exact outbox tuple from `pending` to `prepared`, incrementing `rebind_generation` once and storing that `successor_attempt_id`, `successor_attempt_fence`, and `prepared_at_unix_ms`. A zero-row CAS is stale/conflict; an exact already-prepared candidate tuple is idempotent.
5. For `prepared` owned by a different candidate, takeover is legal only after proving the stored successor attempt has no live/unreleased lease and is no longer the running `operation_dispatch` attempt. In one transaction, recheck every value from steps 1–3 and CAS the exact old prepared tuple to another `prepared` tuple: keep `consumed_at_unix_ms=NULL`, increment `rebind_generation`, replace successor attempt/fence/prepared time with the new current claimed attempt, and append `planner_successor_prepare_rebound` containing old/new attempt IDs, fences, generations and outbox digest. This state-preserving update is permitted by the frozen v6 DDL. It is the takeover linearization point; a waking old attempt cannot match the new tuple.
6. The current successor validates the same settlement, owner/request, control/cancel/deadline, attempt/fence and prepared generation, then selects exactly one §7 outcome. In **one registry transaction**, it CASes that exact tuple from `prepared` to `consumed`, records `consumed_at_unix_ms`, applies only that row's valid-directive/correction/certain-terminal durable projection, and appends its event. The commit is the successor linearization point: before commit the row remains prepared and is eligible for fenced takeover; after commit both `consumed` and the one authoritative projection exist. No planner/provider send, invalid directive mutation or terminal projection is legal before this transaction.
7. Only when the selected valid-directive row chooses a real cell may that transaction initialize/update `rlm_workbench_attempt_authority` for the cell. Correction and certain-terminal rows create no cell authority. Later cell recovery continues to use `aar.workspace-successor-rebind.v1`; planner recovery never reads or writes cell-bound rebind-transfer authority.

The outbox primary/unique constraints, state-preserving prepared takeover, monotonic `rebind_generation`, operation-attempt/lease/dispatch fences, append-only predecessor event, strict suspension owner and exact CAS predicates supply durable exactly-once successor authority without a new table or schema-v7 migration. Validator and T2 tests inspect every named column, force crashes before/after prepare takeover and atomic consumption, and assert zero planner access to both cell-bound tables.

### Exhaustive planner settlement and successor outcomes

The prepare/takeover rules above do not imply success. After joining the authoritative caller ticket/candidate/command receipt, current control/cancellation/deadline view and directive validation, exactly one row applies:

| Authoritative condition | Atomic `prepared→consumed` projection | Further physical planner send |
|---|---|---|
| settled success, exact valid `RlmDirective`, no newer cancel and deadline live | apply that directive and its event/projection exactly once | only if the committed directive itself authorizes a later planner phase |
| settled success but directive invalid, correction budget remains and control/deadline remain live | record the validation failure, advance phase/`step_index`, create the next strict correction suspension/outbox/ticket in the same registry transaction; do not apply the invalid directive | allowed only under the new ticket/idempotency identity after commit |
| settled success but directive invalid and correction budget exhausted | terminal certain planner failure plus failure event; no cell/workspace mutation from the invalid directive | forbidden |
| `settled_failure` or `cancelled_certain`, with no outcome-unknown physical send | terminal failure/cancel projection and event | forbidden |
| `cancelled_before_send` | no planner successor/outbox; the existing operation cancellation/finalization CAS, which owns the current outer cancellation revision, terminalizes the outer operation after observing the durably cancelled suspension | forbidden |
| cancellation revision advances after a known settlement but before directive commit | consume without applying the directive; terminal certain cancellation and event win | forbidden |
| cumulative deadline expires after a known settlement but before directive commit | consume without applying the directive; terminal certain deadline failure and event win | forbidden |
| `cancel_requested`, `outcome_unknown`, or `quarantined`, including deadline/cancel observed after uncertain send | no successor outbox exists; do not prepare, take over, consume, terminalize or create a correction ticket; retain/park the exact ticket/suspension and require frozen caller-work lookup/reconcile | forbidden until reconciliation produces a frozen outbox-eligible terminal state; never blind retry |

Only a pending outbox created by frozen `settled_success`, `settled_failure`, or `cancelled_certain` obtains a fenced successor and passes through `pending→prepared→consumed`. A prepared owner that dies follows the same generation-advancing takeover rule regardless of which eligible row will be projected. Every consume transaction rechecks the exact settlement, attempt/fence/generation, cancellation/control revisions and cumulative deadline. `cancelled_before_send` follows the existing outer cancellation/finalization CAS without a planner successor. Cancellation/deadline after send-start without authoritative outcome stays `cancel_requested`/`outcome_unknown`/`quarantined`, with no outbox and reconciliation only. No plain exception, timeout, process death, or invalid text is upgraded to success, certain failure, outbox eligibility, or resend authority.

If strict schemas or invariants for `logical_owner_json`, planner request JSON, or this cell-free outbox CAS cannot be generated and enforced without changing frozen v6 DDL, schema v7 becomes mandatory and ADR-003 must be superseded. Silent sidecar extension is not allowed.

Ticket identity additionally binds suspension revision, method contract ID, and request digest. The same idempotency key with different owner/request/profile bytes is `IDEMPOTENCY_CONFLICT` and parks or fails certain before physical send.

A settled success MUST contain:

- exact `ModelRouteReceipt` matching the admitted binding;
- `ModelUsageRecord`; absent dimensions remain null;
- response/digest that validates as the exact `RlmDirective` contract;
- physical attempt/ticket/claim lineage;
- send-start evidence or provider lookup evidence accepted by the reconciler.

The coordinator MUST NOT accept a plain text directive, caller-provided schema override, route drift, forbidden fallback, estimated usage where provider-reported usage is required, or an observation from a stale claimant/adapter generation.

## 8. Method-scoped admission contract

The required set is a pure function of frozen job fields, resolved grant capabilities, and planner mode. It is computed before operation creation.

The sole mode-normalization function is:

| Job `model.execution_mode` | Required activated `planner.mode` |
|---|---|
| `caller_delegated` | `caller_delegated_ticketed` |
| `service_managed` | `service_managed` |

No other string, alias or coercion exists. All later references to a job/profile mode match mean equality after this table is applied.

| Condition | Required executable owner |
|---|---|
| `model.execution_mode = caller_delegated` | ticketed root planner plus `model.request` `caller_driver`; `start_only=true` |
| `model.execution_mode = service_managed` | exact activated planner mode `service_managed` plus the same package-owned native `model.request` manifest/factory projected in planner readback; it owns route/journal/cancel/reconcile behavior and caller-driver liveness is not inferred |
| effective grant contains `model.request` | executable `model.request` row, regardless of planner mode |
| `max_artifact_count > 0`, `max_artifact_bytes > 0`, or `require_named_artifacts=true` | executable `artifact.put` |
| `max_subagent_calls > 0`, or either subagent capability is granted | both `subagent.submit` and `subagent.result` |
| effective grant contains `evidence.query` | executable `evidence.query` |
| effective grant contains `effect.propose` | executable `effect.propose` |

A job's normalized mode must equal the activated profile's single `planner.mode`; the service-managed and caller-delegated cross-product is exercised with two separately digested profiles, never by allowing one profile to switch constructors per request. Mode mismatch is `GRANT_DENIED` before operation creation.

A budget or feature that implies a capability absent from the resolved coherent grant set is `GRANT_DENIED`. Factory/profile/digest drift retires authority and is also `GRANT_DENIED`; only a valid current grant whose already-instantiated same-generation adapter has become unavailable is `CAPABILITY_UNAVAILABLE`. Zero budget never expands authority. Native and caller-delegated rows are evaluated independently and cannot satisfy one another merely because they share a method name.

Every required method must be configured, non-reference, current-generation, factory-bound, contract-compatible and healthy. Optional unavailable methods are excluded from planner/facade capabilities. An attempted unavailable call is a certain pre-send failure. Cross-product acceptance covers both normalized execution modes, zero/nonzero artifact and subagent budgets, coherent optional grant subsets, retired factories, same-generation health loss, and conflicting grant/budget combinations.

## 9. Grant contract and `aar.workbench-grant-set.v1`

A workbench context/grant binds principal, session, current runtime/activation generations, capability/profile/authority/route/grant-set digests, exact capability, budget ceilings, issuance/expiry and absolute deadline. The client-supplied context is only a lookup/challenge; it never establishes those values.

After activation preflight and factory derivation, the supervisor constructs this strict server-side policy record. Every property is required, collections are sorted and unique, and no extras are legal:

| Property | Exact wire domain |
|---|---|
| `schema_version` | literal `aar.workbench-grant-set.v1` |
| `runtime_generation` | `PositiveCounter` |
| `activation_generation` | `PositiveCounter` |
| `profile_id` | `OpaqueToken` |
| `profile_digest` | `Digest` |
| `activation_authority_digest` | immutable activation-history tip `Digest` |
| `capability_digest` | exact derived capability-set `Digest` |
| `route_catalog_digest` | `Digest` |
| `principal_ids` | sorted unique tuple of exact `OpaqueToken` values copied from the legacy-named intent `principal_patterns`, length `1..64`; no glob/regex semantics |
| `session_binding_policy` | literal `bind_exact_request_session`; any syntactically valid requested session may be considered only for an allowed principal, and the issued grant is bound to that one exact session |
| `capabilities` | sorted unique tuple of `CapabilityName`, length `1..64`, equal to activated profile policy intersected with current executable capability truth |
| `budget_ceiling` | strict object with the six required bounded integer fields and exact minima/maxima from the activation intent |
| `max_ttl_ms` | integer `1000..900000`, no greater than intent `max_deadline_ms`; this lower bound equals the frozen mutation/job wall-time minimum so every active policy can admit at least one bounded request |
| `grant_set_digest` | self `Digest` |

The issuer stores each minted grant in a server-side strict `IssuedWorkbenchGrant` value with exactly: `grant_id`, `grant_set_digest`, `capability`, `principal_id`, `session_id`, `runtime_generation`, `activation_generation`, `profile_digest`, `activation_authority_digest`, `capability_digest`, `route_catalog_digest`, the six concrete budget ceilings, `issued_at_unix_ms: UnixMs`, `expires_at_unix_ms: UnixMs`, and `revoked: boolean`. Every concrete ceiling uses the exact matching `GrantBudgetCeiling` scalar domain. The internal record requires `issued_at_unix_ms < expires_at_unix_ms`; live-clock expiry, grant-set `max_ttl_ms`, profile deadline, job deadline and generation-retirement comparisons remain issuer/composition behavior. The record is not client-authored and is not a new MCP wire schema. Its strict package-owned type and focused tests prevent omission or extra fields; because no canonical public shape document exists, v1 deliberately does **not** place a non-recomputable internal-record-shape digest in `aar.workbench-grant-set.v1`.

Mutation validation resolves the frozen workbench context's sorted unique `grant_ids` array (`1..64`) before deriving required methods:

1. Look up every ID in the current in-memory issuer; missing, revoked, expired or prior-generation records are `GRANT_DENIED`.
2. Require every record to have the same `grant_set_digest`, principal/session, runtime/activation generations, profile/activation-authority/capability/route digests and six concrete budget ceilings. Mixed sets/identities/ceilings are `GRANT_DENIED`.
3. Require exactly one record per represented capability; duplicate capability records under different IDs are ambiguous and reject. The effective capability set is the sorted union of those record capabilities and must be a subset of the current grant-set policy.
4. Compare the request's principal/session and all current host/profile/authority fields exactly. Each requested budget is component-wise no greater than the common record ceiling; the absolute request deadline is no later than every record expiry, the profile `max_deadline_ms` from mint time, and any earlier job deadline.
5. Apply §8's feature/budget/mode function to the job and effective capabilities. A required capability absent from the resolved set is `GRANT_DENIED`.
6. Only after authority succeeds, resolve each required method against the current-generation executable capability rows. A factory-bound row that was valid at issuance but whose instantiated adapter becomes unavailable/unhealthy within the same still-current generation is `CAPABILITY_UNAVAILABLE`. Factory/profile/digest drift retires the generation and revokes its records, so it is `GRANT_DENIED`, not a constructible stale-factory `CAPABILITY_UNAVAILABLE` case.

The request's `runtime_generation`, `capability_digest`, `principal_id`, `session_id`, `grant_ids`, budgets and deadline are lookup/challenge inputs, never their own proof. Error precedence is: malformed strict context; issuer/grant identity/expiry/generation mismatch → `GRANT_DENIED`; capability/budget/mode entitlement mismatch → `GRANT_DENIED`; then current same-generation executable adapter loss → `CAPABILITY_UNAVAILABLE`. No operation row, ticket, workspace mutation or provider send occurs on any rejection.

The **AAR supervisor grant issuer** is the authority; the profile is policy input. Ordered publication is:

1. under the lifetime shared runtime lock, verify exact v6 attestation/final-profile/intent/candidate/factory/route bytes plus the immutable activation-history tip and derived current pointer;
2. start a fresh runtime generation and instantiate adapters;
3. derive method capability rows and capability digest from instantiated objects;
4. construct/validate the strict grant-set policy and compute `grant_set_digest`;
5. configure the in-memory issuer with that exact record and no prior-generation issued grants;
6. atomically publish Ready/discovery and activation readback containing the same runtime/profile/authority/capability/route/grant-set digests as the final startup step;
7. only a request matching the published generation may mint a bounded server-side issued-grant record and its frozen public `Grant` projection.

Shutdown/deactivation removes Ready first, then disables issuance and marks all generation records revoked. Grants expire at the earlier of TTL, absolute job deadline, or runtime-generation retirement. Profile/factory/route/history change requires a fresh runtime generation. Missing issuer state, stale generation, client-only digest claims, context mismatch, or grant-set/readback disagreement is `GRANT_DENIED` before operation creation. No provider request occurs during policy construction or grant minting.

## 10. `aar.provider-alias-attestation.v1`

This evaluator-owned metadata contract is not a runtime adapter or provider authority. Every property is required and no extras are legal:

| Property | Exact wire domain |
|---|---|
| `schema_version` | literal `aar.provider-alias-attestation.v1` |
| `launcher_alias` | literal `hermes-codex` |
| `canonical_provider_identity` | literal `openai-codex` |
| `wire_api` | literal `openai-codex-responses` |
| `prime_executable_digest` | `Digest` |
| `prime_config_digest` | `Digest` |
| `model_registry_digest` | `Digest` |
| `provider_entry_digest` | `Digest` |
| `credential_resolver_executable_digest` | executable-byte `Digest`; never credential output or token |
| `model` | literal `gpt-5.6-luna` |
| `requested_reasoning` | literal `max` |
| `created_at_unix_ms` | `UnixMs` |
| `alias_digest` | self `Digest` |

The read-only builder receives explicit operator/evaluator file paths, performs no login, token resolution, provider call, config rewrite, contender mutation, import side effect, or network access, and writes only the requested evidence output. The validator independently recomputes every file/entry/self digest. Accepted evidence is referenced by digest from the paired admission manifest. It normalizes requested provider identity only; it never attests effective route, reasoning, usage, or liveness.

## 11. `aar.evaluation-evidence-classification.v1`

This evaluator-owned strict document is the sole owner of route-component, usage and visibility classification for one arm/attempt. Runtime capability evidence tiers never populate it. Every property is required:

| Property | Exact wire domain |
|---|---|
| `schema_version` | literal `aar.evaluation-evidence-classification.v1` |
| `run_id` | `OpaqueToken` |
| `arm` | `aar | prime` |
| `attempt_id` | `OpaqueToken` |
| `requested_provider` | `ModelRouteValue` |
| `requested_model` | `ModelRouteValue` |
| `requested_reasoning` | `ModelRouteValue | null` |
| `requested_fallback_policy` | literal `none` |
| `requested_cache_policy` | literal `disabled` |
| `effective_provider` | `ModelRouteValue | null` |
| `effective_model` | `ModelRouteValue | null` |
| `effective_reasoning` | `ModelRouteValue | null` |
| `effective_fallback_policy` | `none | fallback_used | null` |
| `effective_cache_policy` | `disabled | cache_used | null` |
| `provider_tier`, `model_tier`, `reasoning_tier`, `fallback_tier`, `cache_tier` | each `attested | observed | requested_only | contradicted` |
| `route_qualification` | `aar_live_qualified | prime_live_qualified | insufficient | contradicted` |
| `usage_tier` | `request_receipt | session_aggregate | partial_events | unavailable` |
| `attempt_visibility` | `complete | aggregate_only | unknown` |
| `contradiction_components` | sorted unique tuple of `provider | model | reasoning | fallback | cache`, length `0..5` |
| `source_receipt_digests` | sorted unique tuple of `Digest`, length `1..128` |
| `classification_digest` | self `Digest` |

Component projection rules are exhaustive and applied independently. For Prime, `requested_provider` is the canonical provider identity from the validated alias attestation (`openai-codex`), while `launcher_alias` remains only in that attestation; alias normalization therefore occurs before classification and is never inferred from live output.

- `attested` requires a request-scoped accepted receipt that binds that requested/effective component; its effective field is non-null and equals the request.
- `observed` requires accepted contender-native execution evidence for that effective component but no accepted request-scoped attestation; its effective field is non-null and equals the request.
- `requested_only` requires frozen launch/profile/alias intent and no accepted effective observation for that component; its effective field is null. It is not silently copied into an effective field.
- `contradicted` requires accepted evidence whose non-null effective value differs from the request. The matching component appears exactly once in `contradiction_components`.
- `contradiction_components` is empty iff no component tier is `contradicted`; `route_qualification=contradicted` iff it is non-empty.
- `aar_live_qualified` requires `arm=aar`, all five component tiers `attested`, exact ticket/physical-attempt lineage, and no contradiction.
- `prime_live_qualified` requires `arm=prime`, provider and model each `observed | attested`, reasoning `requested_only | observed | attested`, fallback and cache each `observed | attested`, and no contradiction. Thus Prime may truthfully retain launch-bound requested reasoning with `effective_reasoning=null` while provider/model/fallback/cache remain live-observed. Any weaker non-contradictory mix is `insufficient`.

Usage rules remain independent:

- `request_receipt`: every in-scope physical attempt has a valid `ModelUsageRecord` with `accounting_source=provider_reported`, exact route/candidate/command receipt lineage, complete attempt visibility, and arithmetic pass. Retries and wasted/discarded spend are present.
- `session_aggregate`: one native aggregate has a declared complete session scope but no request rows; attempt visibility is `aggregate_only`.
- `partial_events`: some native usage exists but coverage/scope is incomplete; visibility is `aggregate_only` or `unknown`.
- `unavailable`: no defensible usage evidence; visibility is `unknown` unless complete non-usage attempt lineage is independently proven.

Missing/estimated `ModelUsageRecord` values cannot produce `request_receipt`. Alias attestation and launch flags alone produce only per-component `requested_only`. Capability `evidence_tier` values (`unknown | caller_observed | host_receipt_bound | provider_attested`) are configuration metadata and are forbidden inputs to this projection except as a consistency check that may downgrade/reject; they never upgrade any route component, qualification or usage evidence.

## 12. `aar.paired-evaluation-admission.v1`

This evaluator-owned immutable document binds one proposed two-arm paired benchmark block after both arms are live-qualified. It is an evaluation planning/evidence document, **not** runtime/provider, physical-launch, replay, or single-use-attempt authority, and it does not authorize qualification or benchmark calls. Every property is required and no extras are legal:

| Property | Exact wire domain |
|---|---|
| `schema_version` | literal `aar.paired-evaluation-admission.v1` |
| `aar_qualification_run_id` | `OpaqueToken`; equals the bound AAR classification `run_id` |
| `prime_qualification_run_id` | `OpaqueToken`; equals the bound Prime classification `run_id` |
| `paired_run_id` | new `OpaqueToken`, distinct from both qualification run IDs |
| `status` | literal `benchmark_ready_planned` |
| `created_at_unix_ms` | `UnixMs` |
| `expires_at_unix_ms` | `UnixMs` strictly greater than creation and no more than `900000` ms later |
| `candidate_digest` | exact accepted candidate `Digest` |
| `aar_profile_digest` | exact active AAR final-profile `Digest` |
| `prime_launch_digest` | exact frozen Prime executable/config/launch `Digest` |
| `prime_alias_digest` | validated `aar.provider-alias-attestation.v1` self `Digest` |
| `aar_classification_digest` | exact `aar_live_qualified` AAR classification `Digest` |
| `prime_classification_digest` | exact `prime_live_qualified` Prime classification `Digest` |
| `case_digest`, `fixture_digest`, `hidden_oracle_digest`, `artifact_contract_digest`, `tool_network_policy_digest`, `protocol_digest`, `budget_digest`, `stop_matrix_digest` | `Digest`; each identifies the one frozen paired treatment block |
| `arm_order` | exact two-item tuple containing `aar` and `prime` once each; this is experiment ordering only and contains no attempt/send authority |
| `operator_run_authority_digest` | `Digest` of CK/operator authorization to prepare this evaluation plan; it does not authorize a physical launch and is never inferred from T0–T4 evidence |
| `admission_digest` | self `Digest` from §0 |

Both classifications must be contradiction-free, bind the exact alias/profile/candidate/route evidence, and satisfy the two live-qualified rows before this document can validate. The builder reads explicit immutable inputs and writes only the requested planning document; it performs no provider call, contender start, attempt allocation, send reservation, or replay decision. Same `paired_run_id` with different bytes is conflict. For v1/0.6.0, no package component consumes this document as launch authority: T5 physical execution remains `NOT AUTHORIZED` until a future separately reviewed SDD defines a durable external launcher authority with physical-attempt CAS, lookup/reconcile and crash semantics. That future authority is outside this product scope. T0–T4 and release acceptance cannot infer live launch permission from this document.

## 13. Error taxonomy

| Code | Certainty | Meaning |
|---|---|---|
| `CUTOVER_OWNER_ACTIVE` | certain | Runtime/supervisor or another cutover owner is live. |
| `CUTOVER_PLAN_STALE` | certain | Database, owner, candidate, profile, or plan input changed. |
| `CUTOVER_AUTHORITY_CONFLICT` | certain | Epoch/authority CAS, immutable marker, history chain, or current-pointer binding conflicts. |
| `CUTOVER_RECOVERY_REQUIRED` | indeterminate | Crash boundary needs an explicit matching reconcile command before further mutation. |
| `ABORT_OR_APPLY_REQUIRED` | certain | Prepared pre-DB state is classified; reconcile writes nothing and the operator must explicitly abort or apply. |
| `ACTIVATION_HISTORY_CONFLICT` | certain | Immutable authority history is missing, forked, rolled back, or disagrees with a committed marker/current pointer. |
| `REGISTRY_VERSION_UNSUPPORTED` | certain | Source/target registry is not the planned v5→v6 path. |
| `ACTIVATION_PROFILE_INVALID` | certain | Strict schema/digest/secret-free policy failed. |
| `ACTIVATION_BINDING_MISMATCH` | certain | Installed candidate/migration/profile/route/grants differ. |
| `GRANT_POLICY_INVALID` | certain | A strict profile grant policy is wire-valid only if it can issue at least one bounded frozen context and all configured limits satisfy the exact domains. |
| `GRANT_BINDING_MISMATCH` | certain | Client context, resolved issued-grant array, grant-set digest, principal/session, budgets, or current runtime/profile/authority binding differs. |
| `PLANNER_UNAVAILABLE` | certain | Required root planner adapter is absent/stale/reference-only. |
| `PLANNER_AUTHORITY_CONFLICT` | certain | Planner predecessor event, suspension, attempt/lease/dispatch fence, prepared takeover, or atomic directive projection is missing, duplicate, stale or conflicting. |
| `CAPABILITY_UNAVAILABLE` | certain | Required or invoked method lacks an executable adapter. |
| `ROUTE_DRIFT` | certain | Effective provider/model/effort differs. |
| `FALLBACK_FORBIDDEN` | certain | Any fallback appears under `none`. |
| `USAGE_EVIDENCE_UNAVAILABLE` | certain for qualification; policy-dependent for ordinary use | Required usage evidence is absent or estimated. |
| `CALLER_OUTCOME_UNKNOWN` | indeterminate | Send may have occurred; lookup/reconcile or quarantine is required. |
| `STALE_ADAPTER_GENERATION` | certain | Caller observation is from a superseded adapter/profile generation. |

Unknown/indeterminate errors never authorize replay or success.
