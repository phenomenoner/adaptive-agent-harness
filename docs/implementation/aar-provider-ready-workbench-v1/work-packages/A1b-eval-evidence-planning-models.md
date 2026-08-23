# A1b-eval — Evaluator Evidence and Paired-Planning Strict Models

**Status:** `rev2_review_pending`

**Parent lane:** A1b — operator/readback/evaluation contract models

**Task-start candidate parent:** `893cbc8e626ea395daa9e479ae48d5326bd3dd72`

**SDD binding:** Generation-13 semantic tree `sha256:f676ed30a08e3f9cbfe38b091f69c69857fe7da047612a964565a76d0ab702fb`; complete 24-file SDD tree `sha256:d9373a26bb56e3604b08857eb81b0770bbcfd5f81f8d6174f89e130256330d6d`; receipt `sha256:c416093655ad0d2c311f132ea1e0e6408d8d60222b74cf9ea6cd04a2de2d3d8c`; requirements `38`; acceptance rows `45`; T5 rows `0`.

**Focused-green dependencies:** A1a `3ca321fd0df5a20a969965a0c1705fdd02dc83b6`; A1b-op1 `d03a10f4ac1cc18b43addb0e62245073f7274e3e`; A1b-op2 `d9e0705fc80d46adf48ae1b9112ce1f05a11e089`.

## 1. Exclusive writable paths

- `src/aar/provider_ready_evaluation_models.py`
- `tests/test_provider_ready_evaluation_models.py`

Both paths must be absent, untracked, unignored, non-symlinked and case/prefix collision-free immediately before dispatch. No other path is writable.

## 2. Public schema inventory

Implement exactly three strict top-level wire documents and an exact three-key module-local registry:

1. `aar.provider-alias-attestation.v1` → `ProviderAliasAttestation` — exactly 13 fields.
2. `aar.evaluation-evidence-classification.v1` → `EvaluationEvidenceClassification` — exactly 25 fields.
3. `aar.paired-evaluation-admission.v1` → `PairedEvaluationAdmission` — exactly 24 fields.

Every field is required, recursive extras are forbidden, scalar validation is strict/no-bool, collections are tuple-shaped, and each document has one root self-digest computed by omitting only that digest field. No model has a default, secret-bearing field, executable import target, path, URL, credential output, provider response body, or physical-launch token.

## 3. ProviderAliasAttestation

Fields and exact domains follow `CONTRACTS.md §10` without widening: schema literal; launcher `hermes-codex`; canonical provider `openai-codex`; wire API `openai-codex-responses`; five executable/config/registry/provider-entry/credential-resolver executable-byte `Digest` bindings; model `gpt-5.6-luna`; requested reasoning `max`; `created_at_unix_ms: UnixMs`; self `alias_digest`.

This slice accepts already-computed digests and issues/validates inert bytes only. It does **not** read files, resolve login/tokens, import executables, inspect effective routes, call a provider or implement the §10 file-path builder. That read-only builder is deferred to its later evaluator/asset owner.

## 4. EvaluationEvidenceClassification

Implement the exact 25 fields from `CONTRACTS.md §11`: IDs/arm; requested/effective provider/model/reasoning/fallback/cache; five component tiers; route qualification; usage tier; attempt visibility; contradiction components; source receipt digests; self digest.

### 4.1 Component-local matrix

Apply independently to provider, model, reasoning, fallback and cache:

- `attested | observed`: effective is non-null and equals requested.
- `requested_only`: effective is null.
- `contradicted`: effective is non-null and differs from requested; the matching component occurs exactly once in `contradiction_components`.
- A null requested reasoning cannot be `attested | observed`; it may be `requested_only` with null effective, or `contradicted` with non-null effective.
- `contradiction_components` is the exact sorted-unique set of contradicted tiers and is empty iff no tier is contradicted.

For Prime, `requested_provider` is exactly `openai-codex`. Alias, launch flags and capability evidence are not accepted as effective observations. The pure document never upgrades from `CapabilityEvidenceTier` and must not import/use that type.

### 4.2 Route qualification

- Any contradicted component requires `route_qualification=contradicted`, and that qualification is legal iff the contradiction set is non-empty.
- `aar_live_qualified`: arm `aar`, all five tiers `attested`, no contradiction, `usage_tier=request_receipt` and `attempt_visibility=complete`.
- `prime_live_qualified`: arm `prime`; provider/model each `observed|attested`; reasoning `requested_only|observed|attested`; fallback/cache `observed|attested`; no contradiction.
- Every other contradiction-free combination is exactly `insufficient`; in particular, an otherwise fully attested AAR row with any weaker usage/visibility pair is `insufficient`.

### 4.3 Usage and visibility

- `request_receipt` requires `attempt_visibility=complete`.
- `session_aggregate` requires `aggregate_only`.
- `partial_events` allows `aggregate_only|unknown`.
- `unavailable` allows `unknown|complete`; complete is only a local representation of externally proven complete non-usage lineage and the pure document does not manufacture that proof.
- `source_receipt_digests` is sorted unique length `1..128`; `contradiction_components` sorted unique length `0..5` over the exact five-component domain.

This model classifies already-projected evidence bytes. It does not collect events, validate external receipts, calculate usage, inspect sessions, call providers or implement a live evaluator.

## 5. PairedEvaluationAdmission

Implement the exact 24 fields from `CONTRACTS.md §12`. Local document rules:

- status literal `benchmark_ready_planned`;
- three run IDs are pairwise distinct;
- `created_at_unix_ms < expires_at_unix_ms <= created_at_unix_ms + 900000`;
- `arm_order` is exactly a two-item tuple containing `aar` and `prime` once each, in either order;
- all listed candidate/profile/launch/alias/classification/treatment/operator planning fields are `Digest`;
- root `admission_digest` is self-bound.

Provide a pure composition constructor that receives already-validated `ProviderAliasAttestation`, AAR `EvaluationEvidenceClassification`, Prime classification and explicit treatment digests. Before issuing the scalar admission document it requires:

- AAR arm/run/qualification are `aar` and `aar_live_qualified`;
- Prime arm/run/qualification are `prime` and `prime_live_qualified`;
- both contradiction sets are empty;
- the AAR classification has `usage_tier=request_receipt` and `attempt_visibility=complete`;
- Prime requested provider equals alias canonical provider and Prime requested model/reasoning equal alias model/requested reasoning;
- the admission run IDs equal the classification run IDs and are distinct from the new paired run ID;
- stored alias/classification digests are copied exactly from those documents.

Direct wire validation enforces only document-local fields/digest; it does not falsely claim to revalidate absent external documents. Same-ID conflict persistence belongs to a future owner and is not implemented here.

## 6. Focused test obligations

Start with assertion-level module-missing RED and one behavioral RED. Final focused tests must prove:

1. exact model inventory, field order/counts `13/25/24`, exact local registry, no extra public schema;
2. all required/no-default/recursive-extra/strict/no-bool rules;
3. direct resolved `model_json_schema()` collection bounds/enums (`0..5`, `1..128`, arm-order exactly 2) using local `$defs/$ref` resolution;
4. exact alias constants and an independent canonical root-digest oracle for **each** of alias, classification and admission; each document separately rejects a dedicated one-field stale-root mutation with its exact digest error;
5. table-driven component matrix for all four tiers on each of five components, including null requested reasoning seams;
6. exact contradiction-set equality/order/duplicate and contradicted qualification iff rules;
7. exhaustive AAR/Prime qualification boundaries plus weaker non-contradictory `insufficient` witnesses; table-drive AAR all-attested × every legal usage/visibility pair so only `request_receipt/complete` is live-qualified;
8. all usage-tier/visibility legal and illegal pairs;
9. source receipt min/max/order/duplicate, exact max and max+1 with direct target error; no strict-list or stale-digest masking;
10. paired run-ID distinctness, both arm orders, expiry lower/upper/exact-900000/max+1, and self digest;
11. pure composition positive witness and one-axis alias/provider/model/reasoning/run/qualification/contradiction/AAR-usage mismatches;
12. a separate correctly re-digested direct classification negative for `arm=prime` with a different otherwise-valid `requested_provider`, asserting the exact requested-provider error independently of composition;
13. static absence of attempt allocation, launch/send/replay/provider/network/login/token/file/env/process/clock behavior and of runtime/provider/MCP/CLI/supervisor/launcher authority consumers. The deferred A1c canonical registry/generator/schema/fixture owner is explicitly permitted to import/consume these model types without gaining launch authority.

Every non-digest negative begins from a fully valid witness, mutates one axis, recomputes the correct root digest and asserts the target location/message. Only dedicated digest-tamper tests retain stale digests. Python payloads retain tuples; JSON-array seams intentionally use `model_validate_json`. Finite-domain/max+1 tests must not pass because of duplicate/order/enum rejection.

## 7. RED → GREEN → verify commands

```bash
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-cache --isolated --python 3.12 --frozen pytest -q -p no:cacheprovider tests/test_provider_ready_evaluation_models.py
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-cache --isolated --python 3.12 --frozen ruff check --no-cache src/aar/provider_ready_evaluation_models.py tests/test_provider_ready_evaluation_models.py
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-cache --isolated --python 3.12 --frozen ruff check --no-cache --select W291,W293 src/aar/provider_ready_evaluation_models.py tests/test_provider_ready_evaluation_models.py
```

Do not run T2+, full suite or live tests in this slice.

## 8. Forbidden scope

No edits to existing A1a/op1/op2 files, shared registry/generator/schema/fixture/package assets, SDD/control/evidence, runtime, provider, MCP, supervisor, operator, evaluator collection or CLI modules. No file reading/writing, login, credential resolution, event collection, route/usage observation, provider request, contender start, attempt allocation, send reservation, replay, scoring, winner, stop action, expansion, T4/T5 authority or package launch consumer. T5 remains zero rows and `NOT AUTHORIZED`.

## 9. Stop and handoff

Stop `BLOCKED` rather than infer external evidence semantics, add persistence, create a launcher or widen paths. On final focused GREEN and both Ruff PASS, stage exactly the two owned paths and commit `feat(contracts): add evaluation planning models`. Return RED evidence, inventory, exact focused outputs, hashes, commit/status and explicit confirmation that no physical execution authority or external behavior exists.
