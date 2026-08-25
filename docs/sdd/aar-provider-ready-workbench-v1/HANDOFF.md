# Specification handoff — clean-install rev2

## 1. Status

- Product lane: **AR-PRW — Provider-Ready RLM Workbench**
- Target: **`adaptive-agent-runtime 0.6.0a0`**
- Release mode: **CLEAN-INSTALL-ONLY**
- Specification status: **superseding rev2 candidate; implementation not claimed**
- Candidate counts: **61 requirements; 214 counted atomic rows; T0=22, T1=91, T2=73, T3=15, T4=13, T5=0; 201 T0–T3 release rows**
- Public mutation: `aar-admin runtime install --runtime-home <absolute-runtime-home> --intent <exact-intent> --candidate-receipt <absolute-json> --wheel <absolute-wheel>`
- Real install, v6 publication, startup/Ready, provider qualification, benchmark, commit, stage, push, and release: **not performed**

## 2. Superseding contract

The release constructs only an absent safe target in one retained-handle-owned mode-0700 sibling staging directory, applies unchanged v6 DDL/attestation to staged empty v5, composes generation-1 install evidence, verifies it, and publishes once with Linux renameat2 `RENAME_NOREPLACE`. Existing target forms refuse before mutation. No aar-admin cutover command exists. The product does not preserve v0.5 in place, does not adopt an old root, does not downgrade, and does not mutate old-root authority.

C1 prepublication verifies candidate receipt/wheel/assets, factories, route/grant policy, v6, profile, and generation-1 history/current but creates no executable capability rows, WorkbenchGrantSet, session grants, or runtime health. C2 postpublication allocates/fences the normal runtime generation and invokes the standalone activation coordinator; its exact-generation grant-set store must read back before Ready. Startup never initializes, migrates, repairs, adopts, or rewrites install state.

## 3. Frozen identities and receipt

The shared target algorithm is exact: resolved parent plus original final component, no Unicode normalization/case folding, canonical UTF-8 POSIX JSON, and `runtime_home_digest` over exactly `{database, runtime_home}`. The existing database identity remains `db-` plus canonical digest hex over the specified schema version, runtime-home digest, and `reference.sqlite3`.

The strict v6 clean projection uses direct `canonical_sha256(...).removeprefix("sha256:")` formulas for `authority-<64hex>` and `empty-v5-<64hex>`. `install_epoch` is one invocation-owned `install-<64hex>` token equal to attestation `cutover_epoch`; the intent is generation 1/null predecessor. One exact preparation object excludes transient staging paths. The Linux SQLite adapter is retained-stage-fd bound. The backup is verified/reopened read-only, used through final staged verification, then removed before publication; startup never rereads it.

The planned `aar.install-candidate-receipt.v1` contains exact intent candidate equality, repeated digest fields, positive wheel size, sorted unique factory entries, raw member implementation digests, and a receipt self-digest. Exact absolute receipt/wheel descriptors are read once; startup revalidates installed member digests but does not recompute the overall wheel digest from installed files. T3 separately proves wheel provenance.

## 4. Compatibility

The existing **14 schemas and 64 fixtures remain exact compatibility bytes and are not regenerated**. The language is **14 preserved + 1 planned receipt schema**. New clean-install integration fixtures and planned receipt fixtures are additive future implementation assets. Frozen v7/v8, migration-v6.sql, D1/D2 owner bytes, historical ADR-001..004, BASELINE, predecessor SDD, and historical reviews remain outside this candidate's writable scope.

## 5. Validation and next boundary

`verification/validate_spec.py` enforces the exact structured product contract, atomic row fields and count/tier arithmetic, receipt/temporal/identity semantics, frozen-input checks before status computation, explicit semantic negation, and self-test probes for positive cutover, truthful negation, preservation masking, frozen drift, and stale counts. The final receipt is generated only after all edits and is run twice with `--receipt`.

Residual implementation work is the product receipt parser/loader, standalone installer and activation-store modules, retained-fd SQLite/staging/cleanup, narrow C2 injection, renameat2 binding, additive integration fixtures, and later exact-wheel/runtime evidence. Their ownership is fixed; no product implementation is claimed by this handoff.
