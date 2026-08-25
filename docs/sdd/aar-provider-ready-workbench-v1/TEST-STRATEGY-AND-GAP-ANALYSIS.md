# Test strategy and gap analysis — clean-install rev2

**Status:** specification and test-planning artifact; successor execution not performed.

**Target:** `adaptive-agent-runtime 0.6.0a0`, clean-install-only.

## 1. Claim boundary and journey

The strategy selects the lowest deterministic evidence that can falsify the exact claim. It does not claim a product implementation, wheel, installation, startup, Ready state, provider route, or benchmark.

```text
intent + exact receipt/wheel -> absent-target safety -> retained handles/staging
-> canonical empty v1-v5 -> unchanged v6 DDL/attestation -> profile/history/current
-> generation-independent C1 evidence -> fsync + renameat2 no-replace
-> post-publication runtime_generation=1/factories/capabilities/grant-set -> Ready/readback
```

The predecessor in-place transition journey is outside this release. Existing transition-shaped schemas remain frozen/inert evidence only.

## 2. Environments

- **ENV-0 static:** strict documents, exact 14 schemas/64 fixtures, planned receipt examples, frozen v6/v7/v8/D1/D2 inputs, canonical digest tooling, no credentials, no cache/bytecode.
- **ENV-1 fresh installer:** disposable Linux/WSL parent with absent target, each target/path/ancestor variant, retained fds, deterministic barriers, SQLite failpoints, backup API, fsync instrumentation, and publication actors.
- **ENV-2 lifecycle:** real registry/supervisor composition where practical, fake factories, fake caller driver, fake clock, grant/capability projection, D1/D2, and method-admission canaries; no provider.
- **ENV-3 exact wheel:** one immutable wheel and exact receipt in a neutral non-editable environment; real absent-target install, installed-member readback, startup/Ready, rerun, status zero-write, and conditional old-root evidence.
- **ENV-4 T4:** separately authorized route/usage evidence with credentials resolved outside SDD artifacts and no automatic rerun.
- **ENV-5 platform:** Linux/WSL renameat2 constructibility and race tests; native Windows fails closed unless an equivalent primitive is independently proven.

## 3. Discriminating obligations

The 214-row matrix is atomic. `A-TARGET-001` through `A-TARGET-009` are separate target forms; `A-PATH-001` through `A-PATH-010` are separate path/ancestor variants; `A-STAGE-001` through `A-STAGE-020` cover handle ownership, cleanup, substitution, parent replacement, publication, and backup lifecycle. `A-V6-001` through `A-V6-030` cover the exact 25 `fail_after_statement` indexes and five named boundaries. There is no sleep-based race proof. T5 remains not authorized.

T2 separately drives generation-independent prepublication, postpublication `runtime_generation=1`, exact native/caller/reference/unconfigured/stale factories, server grants, activation no-effects, all D1 phases, D2 settlement/correction/uncertainty/takeover outcomes, and the method-admission budget/grant/factory variants. T3 separately proves exact-wheel input, fixed member bytes, installed-member readback, startup no-overall-wheel-recompute, damaged evidence barriers, rerun refusal, source isolation, and old-root conditional alternatives. T4 keeps component and usage evidence independent.

## 4. Oracles

- Target identity uses the exact canonical object and rejects unresolved path, inode/dev, staging-path, normalization, and case-fold substitutes.
- The strict v6 attestation uses legal `authority-<64hex>` and `empty-v5-<64hex>` tokens, one preparation object, positive backup size, exact integer timestamps, and the complete payload digest.
- Receipt checks use exact candidate equality, repeated digest equality, positive wheel size, sorted unique factory entries, raw wheel bytes, fixed members, and self-digest.
- Cleanup checks compare retained identities and refuse drift; publication checks renameat2 EEXIST, target/stage inode equality, parent fsync, and pre/post identity chains.
- Generation checks prohibit prepublication executable capability/grant/runtime-health rows and require postpublication runtime_generation=1 before those records.
- Startup checks receipt/member/attestation/profile/inventory only; it cannot reread the deleted backup or claim an installed-file recomputation of the overall wheel digest.

Every row has one exact `stimulus` and one concrete `wrong_effect_absent` assertion derived from its operation/phase/variant and safety partition. Removal or generic weakening fails validation. Generic nonzero status is insufficient where another guard could reject first.

## 5. Known implementation seams

The exact product receipt parser/loader, constructibility-proven Linux `SQLiteStageAdapter`, renameat2 binding, standalone installer/activation modules, narrow C2 injection, and additive future receipt/integration fixtures are not implemented by this task. Their contracts are fixed here; they remain implementation work, not unresolved ownership choices. Native Windows is fail-closed. No provider or live T4 evidence is requested here.

## 6. Exit evidence

Run with `PYTHONDONTWRITEBYTECODE=1` and `python -B`; do not create caches. Parse both JSON files, run the validator once for diagnosis, repair with a changed strategy after a same-cause failure, then run the final receipt-bound invocation twice and require byte-identical receipts. Finish with `git diff --check`, exact 16-path verification, forbidden hashes, and no product/runtime mutation.
