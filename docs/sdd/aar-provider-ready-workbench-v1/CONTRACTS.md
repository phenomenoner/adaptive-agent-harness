# Contracts — clean-install provider-ready rev2

## 1. Frozen wire boundary

The existing 14 strict schemas and 64 fixtures remain byte-identical compatibility/evidence assets. They are not regenerated. The new `aar.install-candidate-receipt.v1` is one planned strict public schema in addition to those 14; it is not claimed to exist in generated assets. New clean-install integration fixtures are additive future implementation assets.

All self-digested documents use canonical SHA-256 over the complete validated object with only that document's own digest field omitted. Canonical JSON is UTF-8. Raw or unresolved target text, transient staging paths/names, ambient environment values, unbound timestamps, whitespace, and post-validation map order never enter a digest domain. The canonical target paths in `runtime_home_digest` and the two explicit ordered attestation timestamps are intentional bound fields.

## 2. Exact target and database identity

The target algorithm is shared by issuer, installer, staging verifier, and startup verifier. Require Linux/WSL v0.6 support conditions: absolute path, no NUL, absent final component, every existing ancestor including parent a UTF-8-encodable non-symlink directory. Resolve the existing parent strictly and set `canonical_target = resolved_parent / original_final_component`. Do not normalize Unicode or fold case. Serialize canonical JSON with POSIX `/` and UTF-8.

```json
{"database": "<canonical_target>/reference.sqlite3", "runtime_home": "<canonical_target>"}
```

`runtime_home_digest` is `canonical_sha256` of that exact object. Then:

```json
{
  "schema_version": "aar.clean-install-database-identity.v1",
  "runtime_home_digest": "<runtime_home_digest>",
  "database_name": "reference.sqlite3"
}
```

`database_identity` is `db-` followed by the 64 lowercase hex characters of the canonical digest after removing `sha256:`. Inode/dev is temporal fencing only; raw/unresolved path and staging path are forbidden semantic inputs.

Clean-install preflight also requires the supplied self-digested intent to have `activation_generation == 1` and `previous_activation_authority_digest == null`. Any other initial-authority input fails with `FRESH_INSTALL_INITIAL_AUTHORITY_INVALID` before staging; the installer never rewrites or reissues the supplied intent.

## 3. Exact clean v6 projection

The strict attestation domain allows only `[A-Za-z0-9._~-]` for its opaque tokens. Therefore the intent's full `LocalAuthorityStoreId` is never copied into `external_authority_store_id`. Project it exactly as follows:

```text
authority_material = {
  "schema_version": "aar.clean-install-authority-projection.v1",
  "intent_authority_store_id": "<full LocalAuthorityStoreId>"
}
projected_external_authority_store_id =
  "authority-" + canonical_sha256(authority_material).removeprefix("sha256:")
```

After every input descriptor and target/parent preflight passes, but before staging is created, the installer issues exactly one invocation token as `"install-" + secrets.token_hex(32)`. Its grammar is `^install-[0-9a-f]{64}$`. It is frozen once for the CLI invocation, reused unchanged in that invocation's preparation, snapshot material, stage evidence, and attestation `cutover_epoch`, and is never reused by a new CLI invocation. There is no automatic invocation retry or stale-stage adoption; a new invocation issues a new token.

The snapshot token is also strict-domain legal:

```text
snapshot_material = {
  "schema_version": "aar.clean-install-snapshot-id.v1",
  "empty_v5_backup_digest": "<sha256:...>",
  "empty_v5_backup_size_bytes": <positive integer>,
  "install_epoch": "install-<64 lowercase hex>"
}
snapshot_id = "empty-v5-" + canonical_sha256(snapshot_material).removeprefix("sha256:")
```

The one and only `aar.clean-install-preparation.v1` object is:

```json
{
  "schema_version": "aar.clean-install-preparation.v1",
  "install_epoch": "install-<64 lowercase hex>",
  "runtime_home_digest": "sha256:<64 lowercase hex>",
  "database_identity": "db-<64 lowercase hex>",
  "database_name": "reference.sqlite3",
  "empty_v5_backup_digest": "sha256:<64 lowercase hex>",
  "empty_v5_backup_size_bytes": 1,
  "canonical_v5_row_set_digest": "sha256:<64 lowercase hex>",
  "intent_digest": "sha256:<64 lowercase hex>",
  "candidate": {
    "source_commit": "<40 lowercase hex>",
    "wheel_digest": "sha256:<64 lowercase hex>",
    "contract_manifest_digest": "sha256:<64 lowercase hex>",
    "skill_digest": "sha256:<64 lowercase hex>"
  },
  "migration_sql_digest": "sha256:<64 lowercase hex>",
  "projected_external_authority_store_id": "authority-<64 lowercase hex>"
}
```

The shown `1` is a domain placeholder: the actual field is the verified positive byte size. There is no alternative inventory, package path, staging path, or transient file field. `external_authority_prepared_digest` is `canonical_sha256` of exactly this object.

The existing `MigrationAttestationPayload` remains complete and unchanged in field set: `schema_version`, `migration_version`, `cutover_epoch`, `snapshot_id`, `snapshot_sha256`, `snapshot_size_bytes`, `canonical_v5_row_set_digest`, `source_commit`, `wheel_digest`, `profile_digest`, `skill_digest`, `contract_manifest_digest`, `migration_sql_digest`, `external_authority_store_id`, `external_authority_prepared_digest`, `started_at_unix_ms`, `completed_at_unix_ms`, `foreign_key_violation_count`, and `integrity_result`. `attestation_digest` is the canonical digest of that exact payload with no extra field. `started_at_unix_ms` and `completed_at_unix_ms` are exact integers with `started_at_unix_ms <= completed_at_unix_ms`; the existing v6 transaction is retained.

The temporary empty-v5 backup is positive-size, closed, fsynced, reopened read-only, checked for integrity/FK and canonical rows, and used through final staged verification. It is removed before final fsync/publication. Startup checks attestation/profile/inventory but never claims to reread the deleted backup.

On Linux/WSL, a standalone installer-owned `SQLiteStageAdapter` reuses the existing `OperationRegistry`, `create_sqlite_backup`, and `apply_registry_v6` primitives without changing their ownership. Before any database mutation it proves `/proc/self/fd/<decimal-stage-fd>` resolves to the retained stage dev/ino; otherwise it returns `FRESH_INSTALL_PUBLICATION_UNSUPPORTED`. Only `/proc/self/fd/<stage_fd>/reference.sqlite3` and the fixed backup member below that root are passed to the existing pathname APIs. The adapter fstats the retained stage before and after every helper, inspects database/backup/WAL/SHM entries fd-relative with no-follow semantics, rejects symlinks/unknown residue, closes connections before cleanup, and never treats the `/proc` pathname as semantic identity. A parent-name or stage-name replacement cannot redirect SQLite work away from the retained inode and causes publication/cleanup refusal at the later retained-handle fence.

## 4. Planned candidate-receipt contract

The exact public mutation is:

```text
aar-admin runtime install --runtime-home <absolute-runtime-home> --intent <exact-intent> --candidate-receipt <absolute-json> --wheel <absolute-wheel>
```

One new strict schema is planned: `aar.install-candidate-receipt.v1`. Its exact shape is:

```json
{
  "schema_version": "aar.install-candidate-receipt.v1",
  "candidate": {
    "package_version": "<exact intent candidate package_version>",
    "source_commit": "<exact intent candidate source_commit>",
    "wheel_digest": "sha256:<64 lowercase hex>",
    "contract_manifest_digest": "sha256:<64 lowercase hex>",
    "skill_digest": "sha256:<64 lowercase hex>"
  },
  "wheel_size_bytes": 1,
  "wheel_digest": "sha256:<64 lowercase hex>",
  "contract_manifest_digest": "sha256:<64 lowercase hex>",
  "skill_digest": "sha256:<64 lowercase hex>",
  "factory_entries": [
    {
      "factory_id": "<factory id>",
      "wheel_member": "aar/<relative member>",
      "implementation_digest": "sha256:<64 lowercase hex>"
    }
  ],
  "receipt_digest": "sha256:<64 lowercase hex>"
}
```

The `candidate` object is exactly equal to `intent.candidate`, including exact `source_commit` and `package_version`. Repeated digest fields equal the candidate fields. `wheel_size_bytes` is positive. `factory_entries` are sorted by `factory_id`, unique, and each exact factory ID/digest matches an intent method manifest. `receipt_digest` is the self-digest over the complete strict receipt with only `receipt_digest` omitted.

Receipt and wheel paths are absolute existing regular non-symlink files. Each is opened once with a retained no-follow descriptor and fstat identity; hashing and all reads use those same opened bytes. `wheel_digest` is the SHA-256 of exact wheel bytes. The same bytes are inspected as ZIP: duplicate names, traversal, backslash, absolute names, symlinks, and noncanonical duplicate paths are rejected. Fixed contract members are `aar/bundled/schemas/aar-provider-ready-schemas-v1.json`, `aar/bundled/fixtures/provider-ready/manifest.json` plus exactly its declared provider-ready fixture members, and `aar/bundled/aar-operations/SKILL.md`. Define:

```text
contract_manifest_digest = canonical_sha256({
  "schema_bundle_digest": <bundle.bundle_digest>,
  "fixture_set_digest": <manifest.fixture_set_digest>
})
skill_digest = sha256(raw bytes of aar/bundled/aar-operations/SKILL.md)
implementation_digest = sha256(raw bytes of the declared aar/ wheel member)
```

No ambient import or installed-byte inference is valid. The exact validated credential-free receipt is copied to staged `authority/install-candidate-receipt.json`. Startup revalidates it and installed distribution member digests, but cannot recompute the overall wheel digest from installed files; the attestation/profile binds the previously verified wheel digest. T3 separately proves exact installed distribution origin from that wheel.

## 5. Activation, capability, grant, planner, evaluation

Before publication, **C1** verifies candidate, assets, factory declarations, route policy, grant policy, v6, profile, and generation-1 history/current only. It does not construct executable capability rows, `WorkbenchGrantSet`, session grants, or runtime health. After publication, **C2** runs through the standalone `aar.runtime.provider_ready_activation` coordinator: normal host construction allocates/fences the runtime generation, then one explicit activation injection instantiates exact factories, projects capabilities, and invokes `ProviderReadyActivationStore` before Ready.

`ProviderReadyActivationStore` atomically publishes mode-`0600` canonical grant-set bytes at `authority/runtime-generations/<runtime_generation:020d>/workbench-grant-set.json` under a mode-`0700` generation directory and fsyncs/readbacks the result. Byte-identical replay for the same current generation is idempotent; different bytes or unknown siblings at that generation are `ACTIVATION_GENERATION_CONFLICT` and are never replaced. A restart allocates a successor runtime generation and a new immutable file; prior generation files remain historical, while all prior in-memory session grants are invalid by generation. Session grants are memory-only, issued only after an explicit policy-approved request, and bind grant-set digest, principal, session, capability, expiry, activation generation, and current runtime generation; revocation or restart makes them `GRANT_DENIED`. The existing registry/migration owners do not acquire this filesystem authority.

Executable native/caller rows require exact current-generation factories. Reference rows are truthful but admission-unusable; unconfigured rows are unusable. Sorted unique `grant_ids` resolution yields `GRANT_DENIED` for authority mismatch before availability; only current same-generation health loss is `CAPABILITY_UNAVAILABLE`.

D1/D2 remain on frozen v6 caller-work owners with exact `{kind, phase, step_index}`, waiting-lease release, claim/send-start, reconcile-only uncertainty, cell-free successor CAS, and no blind resend. Route/usage/evaluator evidence remains a separate owner. `aar.evaluation-evidence-classification.v1` and `aar.paired-evaluation-admission.v1` remain inert planning/evidence contracts; T5 physical execution remains `NOT AUTHORIZED`.

## 6. Non-claims

This document does not claim generated implementation, a live receipt schema, a built wheel, an installed distribution, startup, Ready, provider evidence, or T5 execution.
