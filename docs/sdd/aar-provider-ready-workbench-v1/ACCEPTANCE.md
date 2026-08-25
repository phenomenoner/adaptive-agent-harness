# Acceptance and claim gates — clean-install rev2

## 1. Counted atomic matrix

| Tier | Meaning | Live/provider inference | Release gate |
|---|---|---:|---:|
| T0 | static contract, identity, frozen-byte, receipt-shape, and claim separation | No | Yes |
| T1 | target, handle, staging, database, v6, publication, and zero-write behavior | No | Yes |
| T2 | generation ordering, grants, planner, reconciliation, and method admission | No | Yes |
| T3 | exact-wheel installed host, startup, Ready barrier, and conditional old-root boundary | No | Yes |
| T4 | separately authorized route/usage/evidence qualification | Yes | No |
| T5 | no execution authority in this release | N/A | No |

The machine matrix contains **214 counted atomic rows**: **T0=22, T1=91, T2=73, T3=15, T4=13, T5=0**. The mandatory no-live release set is **201 T0–T3 atomic rows**. The requirement register has **61 requirements**. These numbers are computed from the JSON, not inherited from a predecessor.

Each counted row has one scalar `operation`, one scalar `phase`, one singular non-empty `variant`, one exact executable `stimulus`, one expected observation, and one concrete `wrong_effect_absent`. The validator derives the exact stimulus and safety-partition wrong effect and rejects removal or weakening. Aggregate containers, recursive summaries, arrays of variants, and “all gates” rows are not acceptance cells. `A-TEST-001` is explicit non-counted metadata describing the exact-wheel summary assertion.

## 2. Atomic partitions that must remain visible

The matrix individually enumerates:

- nine existing target forms: regular file, empty directory, non-empty directory, symlink, broken symlink, database file, WAL/SHM tree, authority/history/current tree, and unknown entry;
- ten path/ancestor forms: relative, NUL, non-UTF-8 final, missing ancestor, file ancestor, symlink ancestor, non-UTF-8 ancestor, case alias, Unicode-normalization alias, and safe absolute absent final;
- retained parent/ancestor/stage handles, mode `0700`, same-filesystem creation, stage substitution, cleanup drift, parent/ancestor replacement, before/after identity-chain checks, target inode equality, parent fsync, EEXIST, caught cleanup, abrupt residue, backup removal, and native-Windows fail-closed behavior;
- every one of the 25 frozen v6 DDL statement `fail_after_statement_01` … `fail_after_statement_25` indexes, plus before-first, before attestation insert, before schema-migration insert, before commit, and after-commit readback;
- clean projection authority/snapshot/preparation/timestamp/attestation/profile cells;
- generation-independent prepublication and postpublication `runtime_generation=1`/factory/capability/grant/Ready order;
- each D1 phase, `start_only` condition, lease release, pending/prepared, live-owner refusal, dead-owner takeover, and one-time consume;
- each D2 settlement/correction/uncertainty/takeover/no-resend outcome, including settled success/failure, certain and before-send cancellation, cancel-requested, outcome-unknown, quarantined, deadline before/after send, invalid directive correction, send-start, exact receipt evidence, null usage, failed-source replay refusal, and possibly-sent no-resend;
- method admission variants for caller/native modes, zero/positive artifact and subagent budgets, evidence/effect methods, missing/reference/retired/stale/mismatched factories, current health loss, grant ceilings, and `start_only=false`;
- exact-wheel receipt/member/startup cases, installed-member readback without overall-wheel recomputation, damaged evidence, rerun refusal, status zero-write, source-import leakage, and separate old-root archive versus fresh-host no-old-root observations;
- T4 component-wise provider/model/reasoning/fallback/cache evidence and usage visibility cases.

A row may be merged only when operation, lifecycle phase, adversarial variant, and expected observation are behaviorally identical. No such merge is used for the partitions above.

## 3. Claim rules

A passing specification receipt proves documentation consistency only. It proves no implementation, wheel, database transaction, publication, startup, Ready, provider route, archive, or benchmark. Candidate/profile fields do not prove wheel bytes; staged v6 does not prove publication; capability does not prove live route; requested route does not prove effective route; and stale staging is never authority.

Prepublication never fabricates executable capability rows, `WorkbenchGrantSet`, session grants, or runtime health. Postpublication startup performs normal generation/grant writes only after immutable install/profile/evidence readback. Startup cannot initialize, migrate, repair, adopt, or recompute the deleted backup/overall wheel digest.

## 4. Tier gates

### T0

T0 binds the product contract, clean identity, strict receipt shape, authority/snapshot token projections, exact 14-schema/64-fixture preservation, frozen v6/v7/v8/D1/D2 inputs, credential-free identifiers, inert paired planning, validator oracles, clean-only modes, and status separation.

### T1

T1 covers every target/path form, no-write refusal, retained-handle staging, deterministic cleanup and substitution races, empty-v5, every v6 statement failpoint and boundary, exact attestation/preparation projection, positive backup lifecycle, no-replace publication, and native-Windows failure.

### T2

T2 covers the C1-prepublication/C2-postpublication split, runtime generation, exact factories and capability truth, generation-store CAS/restart behavior, server-owned memory-only session grants, activation no-effects, all D1/D2 outcomes, and method-scoped admission. `GRANT_DENIED` precedes availability; only valid current same-generation health loss is `CAPABILITY_UNAVAILABLE`.

### T3

T3 uses one exact wheel and receipt on an absent target, verifies installed member provenance and startup readback, refuses damaged evidence before Ready, keeps startup read-only with respect to schema/profile/install state, refuses rerun, and separates old-root archive/readback from fresh-host no-old-root proof.

### T4 and T5

T4 is separately authorized and cannot repair T0–T3. T5 physical paired execution, launcher, stop, rerun, score, winner, and expansion authority remain absent. `aar.paired-evaluation-admission.v1` is planning evidence only.

## 5. Specification exit

The specification phase exits only when both JSON assets parse, the validator passes twice with byte-identical receipt, frozen inputs pass, `git diff --check` passes, the exact 16-path write set is verified, forbidden product/SQL hashes remain unchanged, and no implementation/live claim is made. Historical ADR-001..004, BASELINE, predecessor SDD, and historical reviews remain untouched.
