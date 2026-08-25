# Work Package A1a — Activation and Adapter Strict Models

**Status:** `blocked_spec_successor_review`

**Lane:** A — frozen contracts and generators

**Implementation model:** `openai-codex / gpt-5.6-luna / max`

**Accepted product-source parent:** `bd30df40a9f3e77bcf2d244dbf4fd9bba0148ba1`

**Generation-10 S0 binding:** semantic tree `sha256:cf6874ee870a1e2bcca3fb83b785213ea8b2f0205e4a54afaf9161b609925ab1`; complete 24-file SDD tree `sha256:fb4dbd69802191f411c6df9281b57e8ca45085012179ff56c403ca940dfbef3a`; deterministic receipt file SHA-256 `bd63c52ce6eb88bc6ceac7a5adb1d80f546bfbe2cd7eae08728b39e5ca4154f2`; requirements file SHA-256 `50b41b0552807d92eb209aad4bee61688852cd2a93e88b3deef245dd90870290`; acceptance-matrix file SHA-256 `0bf0d9e7f341282f2db0e22240a43df850e16f302faff97e441a5a01716ecdc1`. This packet is non-authorizing until Generation-10 S0 and this exact packet each receive separate independent current-byte PASS.

These exact bindings, the two writable paths below, and this packet's own raw SHA-256 are the only valid review/dispatch envelope. A reviewer or writer brief that names another source parent, S0 digest, packet digest, writable path, or alias path is invalid and must return `INCOMPLETE` before reading or mutating product bytes.

**Normative sources:** `CONTRACTS.md §§0–2, §5 planner readback ownership, §8 admission mode binding`; frozen v8 capability method/evidence/reference semantics in `ARCHITECTURE.md §§5–7,9`; rationale and compatibility boundaries in `decisions/ADR-002-ticket-root-planner.md`, `ADR-003-preserve-v8-method-scoped-admission.md`, and `ADR-004-freeze-activation-contract-domains.md`

## Purpose and claim

Implement the smallest transport-neutral typed foundation for the activation intent, generated final profile, and package-registered method-adapter manifest. This slice stops before shared contract registries, schema/fixture generation, bundled assets, runtime factories, activation, migration, grants, CLI, provider work, installation, push, tag, publication, or release.

A1a alone closes no acceptance row. It supplies a directly tested model seam required by A1c, where `A-ACT-001` and the contract part of `A-SEC-001` can become executable.

## Entry gates

- [x] P1 exact-base source baseline terminal: pytest `743 passed, 5 skipped, 1 warning`; Ruff PASS.
- [x] A0 Luna/max read-only seam map completed and PMO source-checked.
- [ ] S0 successor validates deterministically and receives independent current-byte PASS.
- [ ] This S0-rebound packet receives independent current-byte PASS. The earlier packet PASS applied only to the superseded S0 digest.
- [ ] PMO records the exact task-start commit and confirms both writable paths below are absent/clean.
- [ ] No other writer owns contract/model registry paths.

## Exclusive writable paths

1. `src/aar/provider_ready_models.py` (new)
2. `tests/test_provider_ready_models.py` (new)

These exact two paths are also the review and dispatch allowlist; no alias path is valid. No other task-owned path may be intentionally created, staged, formatted, or modified. Commands below disable task caches. Never touch `versions.py`, `contract.py`, `schema_generator.py`, root `schemas/`, `tests/fixtures/`, bundled assets, profiles, package metadata, runtime/provider/MCP code, SDD/control/evidence files, or another worker's files.

## Required public model surface

Reuse `aar.schemas.StrictModel`, `Digest`, `OpaqueToken`, `CapabilityName`, `PositiveCounter`, `BudgetCounter`; `aar.broker_models.ModelRouteValue`; and `aar.canonical.canonical_sha256`. Define the two new strict lexical aliases `PackageVersion` and `LocalAuthorityStoreId`, plus exact local literals `WorkbenchActivationMethodName` for the six frozen v8 catalog methods and `CapabilityEvidenceTier` for the frozen capability domain. Do not reuse generic seven-value `BrokerMethodName` for an activation manifest.

Freeze these public classes:

1. `ProviderReadyCandidate`
2. `ActivationRuntimeBinding`
3. `ActivationPlannerBinding`
4. `ActivationRoutePolicy`
5. `GrantBudgetCeiling`
6. `ActivationGrantPolicy`
7. `MethodAdapterManifest`
8. `HostActivationIntent`
9. `HostActivationProfile`

Freeze the local top-level registry keys:

```text
aar.method-adapter-manifest.v1
aar.host-activation-intent.v1
aar.host-activation-profile.v1
```

`PROVIDER_READY_SCHEMA_MODELS` maps exactly those three keys to the corresponding three top-level classes. A1a MUST NOT wire the local registry into `aar.contract.CONTRACT_MODELS`. Every listed property of every public class is required, uses no default/default factory, and the emitted schema has `additionalProperties=false`; “required” includes nullable `previous_activation_authority_digest`.

## Field constraints

Implement the exact S0 table from `CONTRACTS.md §1`:

- activation `profile_id`, database identity, adapter ID and exact principal IDs use `OpaqueToken`;
- `activation_generation` uses `PositiveCounter`; the model validates the wire range only. `previous_activation_authority_digest` is strict `Digest | None`; the model binds the observed immutable history tip (which runtime must require to equal derived `current.json`) but A1a does not implement history, pointer repair, or CAS;
- package version uses the exact S0 1–64 canonical ASCII regex, including rejection of leading-zero/non-canonical numeric components; source commit is exactly 40 lowercase hexadecimal characters;
- all named digests use `Digest`;
- registry version, runtime and security fields use the exact contract literals; `ActivationPlannerBinding` is one strict four-property mode-tagged object with required `mode`, `method`, `directive_schema_version`, `directive_schema_digest` and no branch-only/defaulted property; `method` is the literal `model.request`, `directive_schema_version` is the frozen `aar.rlm-directive.v1`, and `planner.mode` is `caller_delegated_ticketed | service_managed`;
- route profile IDs use `ModelRouteValue` and are inert catalog identifiers, never provider coordinates;
- `principal_patterns` retains its wire name but v1 values are exact principal IDs only; do not implement glob, regex, prefix or substring matching;
- capabilities use `CapabilityName`;
- authority-store ID uses the exact `local-file-authority-v1:` lexical domain;
- adapter method uses `WorkbenchActivationMethodName`; contract/factory IDs use `CapabilityName`; backend kind and generation policy use their frozen literals; evidence tier uses `unknown | caller_observed | host_receipt_bound | provider_attested`;
- budget fields apply the exact contract bounds: `wall_time_ms 1000..900000`, `model_requests 1..128`, `input_tokens/output_tokens 0..9223372036854775807`, `child_operations 0..64`, `artifact_bytes 0..33554432`, and `max_deadline_ms 1000..900000`; coercions and out-of-range values reject.

No import path, command, endpoint, environment variable, credential, arbitrary code, provider client, transport object, path-resolution behavior, or string expansion belongs in the models.

## Canonical collection invariants

- `adapters`: length `1..6`, a unique subset in exact frozen catalog rank `model.request`, `subagent.submit`, `subagent.result`, `evidence.query`, `artifact.put`, `effect.propose`; generic `artifact.read` rejects;
- `routes.allowed_profile_ids`: length `1..64`, ascending validated value, unique;
- `grant_policy.principal_patterns`: length `1..64`, ascending exact principal ID, unique;
- `grant_policy.capabilities`: length `1..64`, ascending capability name, unique.

Collection order carries no preference. An `issue`/builder may sort validated operator input. Direct model/JSON validation MUST reject empty, duplicate, or non-canonical supplied document arrays rather than silently rewriting digest-covered bytes.

`backend_kind="reference"` MUST require `reference_only=true` and `evidence_tier="unknown"`; `backend_kind="native"` or `"caller_driver"` MUST require `reference_only=false`. Frozen capability projection later reports an exact reference factory as configured/reference-only/unknown but never admission-usable. Fake/deterministic properties not represented by `backend_kind` remain A1c/runtime immutable-factory-registry checks; do not infer them from identifiers.

`method="artifact.put"` plus `backend_kind="caller_driver"` MUST reject because frozen registry-v6 caller-work request/DDL domains do not carry `artifact.put`. Native and reference artifact manifests remain valid. This is an activation restriction only; do not edit frozen v8 capability/catalog bytes.

## Planner-mode cross-object invariant

`HostActivationIntent` MUST validate its complete `planner` plus `adapters` collection after each nested manifest has passed its own digest check:

- `planner.mode="caller_delegated_ticketed"` requires exactly one `method="model.request"` manifest and that manifest has `backend_kind="caller_driver"` and `reference_only=false`;
- `planner.mode="service_managed"` requires exactly one `method="model.request"` manifest and that manifest has `backend_kind="native"` and `reference_only=false`;
- the opposite kind, `reference`, missing `model.request`, or any attempt to select a mode per job rejects the intent;
- the matching manifest's `factory_id`, `factory_digest`, contract fields and digest remain nested in and self-digest-bound by the intent and final profile. No extra planner factory/import field is added.

A1a owns this typed cross-object gate. Runtime factory resolution, job-mode admission and activation-readback projection (`factory_id`, `factory_digest`, `method_manifest_digest`) remain A1c/C/D work and MUST consume these exact bound fields rather than create another constructor seam.

Raw JSON duplicate-object-key rejection is intentionally **not** claimed by Pydantic model construction. A1c owns the byte loader that rejects duplicate keys before `json.loads`/model validation; A1a tests only recursive property-set uniqueness and must not claim raw duplicate-key coverage.

## Self-digest contract

Provide an `issue`/established deterministic constructor and `model_validator(mode="after")` for each top-level self-digest document. Constructor and validator MUST share one small pure helper that computes from the complete validated strict document mapping while omitting exactly the named root digest field; they MUST NOT maintain separate hand-written included-field lists.

- manifest: omit only root `manifest_digest`;
- intent: omit only root `intent_digest`, retaining nested manifest digests and `previous_activation_authority_digest`;
- final profile: omit only root `profile_digest`, retaining the complete intent, its digest, and `migration_attestation_digest`.

`schema_version` is always inside the digest domain. The existing migration attestation is not nested in the final profile; A1a models only its `Digest`. The legacy attestation equality (`profile_digest == intent_digest`) is a later integration check.

## RED-first discriminator

Preserve an assertion-level RED before production behavior:

1. first test uses `importlib.util.find_spec("aar.provider_ready_models")` and an explicit assertion that the required module/model surface exists; the observed assertion failure is the initial RED, not a collection/import error;
2. after adding an importable model skeleton, behavioral REDs precede GREEN implementations.

Minimum focused cases:

1. valid strict construction and JSON-boundary round-trip for every public model;
2. exact local registry keys and schema literals;
3. independent digest oracle for each top-level document: `model_dump(mode="json")`, pop only its own digest, assert `schema_version` remains, then compare `canonical_sha256`;
4. wrong root digest rejection for all three documents;
5. adapter payload tamper with stale `manifest_digest` rejects the adapter;
6. re-issue the changed adapter but keep stale `intent_digest` to prove outer intent binding;
7. re-issue the changed intent but keep stale `profile_digest` to prove final-profile binding;
8. change only `migration_attestation_digest` while retaining stale `profile_digest` to prove that field is bound;
9. empty, duplicate, wrong frozen-rank order, exact-minimum, exact-maximum and maximum-plus-one cases for all four canonical collections; duplicate methods reject, `artifact.read` rejects, and valid partial subsets that include the mode-matching `model.request` preserve frozen catalog order. Independently resolve local `$ref` entries through each model's emitted `$defs` and assert the JSON Schema array bounds themselves: `HostActivationIntent.adapters` has `minItems=1` and `maxItems=6`; `RouteProfileConstraint.allowed_profile_ids`, `GrantIssuancePolicy.principal_patterns`, and `GrantIssuancePolicy.capabilities` each have `minItems=1` and `maxItems=64`. These are direct `model_json_schema()` assertions and MUST still fail if model-level planner, uniqueness, or canonical-order validators would reject the same empty/oversized payload for another reason; generic validation rejection or `too_short`/`too_long` error typing is not sufficient evidence of the emitted wire-schema bounds;
10. wrong source-commit/package/identifier/digest shapes, including `00.06.000a00` and `1.2.3rc01.post02.dev03`, generation range, literals, policies, backend kind, generation policy and frozen capability evidence tier;
11. both directions of backend/reference truth: reference with false/non-unknown rejects, native/caller-driver with true rejects; `artifact.put + caller_driver` rejects while native/reference artifact manifests validate;
12. valid caller profile with caller-driver `model.request` and valid service-managed profile with native `model.request`; missing/reference/opposite-kind model manifest rejects in both directions, `ActivationPlannerBinding` schema has exactly four required properties and no `oneOf`, nested factory/manifest tamper follows inner-then-outer digest sequencing, and no planner factory/import field appears;
13. absent/prior authority digest is bound by intent self-digest, while equal/lower/higher generation history is explicitly not claimed by this model-only slice;
14. exact budget minima/maxima validate; zero rejects for wall-time/model-request/deadline while zero remains valid only for token/child/artifact ceilings; every below-minimum/maximum-plus-one/scalar coercion/unknown field rejects;
15. principal IDs are exact inert values: glob/regex-shaped values reject lexically and no matching behavior exists;
16. recursive schema-property/required inventory matches `CONTRACTS.md §§1–2`, every public property is required with no default/default factory and `additionalProperties=false`, no prohibited semantic field; module imports contain no runtime/provider/MCP/transport implementation, and no expansion/resolution/execution behavior is introduced. Do not claim raw duplicate-key rejection or classify allowed identifier contents as commands, URLs or credentials.

## Focused verification

```bash
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-cache --isolated --python 3.12 --frozen pytest -q -p no:cacheprovider tests/test_provider_ready_models.py
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-cache --isolated --python 3.12 --frozen ruff check --no-cache src/aar/provider_ready_models.py tests/test_provider_ready_models.py
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-cache --isolated --python 3.12 --frozen ruff check --no-cache --select W291,W293 src/aar/provider_ready_models.py tests/test_provider_ready_models.py
```

If isolated execution is materially slow on DrvFS, report the exact gate and use only the PMO-approved environment split; do not weaken tests or timeouts.

Only when the final PMO dispatch explicitly authorizes a local commit:

```bash
git add -- src/aar/provider_ready_models.py tests/test_provider_ready_models.py
git diff --cached --check -- src/aar/provider_ready_models.py tests/test_provider_ready_models.py
git commit -m "feat(contracts): add provider-ready activation models"
```

## Handoff contract

Return:

- exact starting HEAD and final changed paths;
- assertion-level initial RED command/output, later behavioral RED, GREEN commands and complete summaries;
- invariant/field map and any unresolved normative ambiguity;
- scoped worktree/index diff and confirmation forbidden paths were untouched;
- local commit ID only if the dispatch authorized commit;
- explicit statement that no shared registry/generator/fixture/package/runtime/provider/install/push/tag/publication/release action occurred.
