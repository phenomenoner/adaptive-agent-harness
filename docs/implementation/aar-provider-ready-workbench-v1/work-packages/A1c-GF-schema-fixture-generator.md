# A1c-GF — Provider-Ready Schema Bundle, Dual Validator, Fixtures and Manifest

**Status:** `rev3_review_pending`

**Parent lane:** A1c — registry/generator/schema/fixture convergence

**Required parent:** A1c-R focused-green final head `0048d4110d9a272b67a05ce5b9360a28036a5de2`; control/evidence head `09629f28a624c8d55fab07beb124f3253c61723f`

## 1. Authority and non-goals

This serial slice consumes the exact fourteen-key A1c-R registry and produces an isolated successor schema bundle plus provider-ready fixtures. It does not modify or append to the legacy contract bundle, MCP v7/v8 projections, package metadata, runtime, provider, CLI or activation behavior.

Dual validation is composite:

- independent structural engine: `jsonschema.Draft202012Validator` / `check_schema`;
- project semantic engine: A1c-R raw-byte loader plus strict Pydantic/self-digest/cross-field models;
- raw-loader-only cases have independent result `not_applicable`;
- semantic-only invalid fixtures may structurally pass and project-fail;
- agreement means each engine equals the per-fixture expected result and the composite validity is correct, not identical standalone error messages.

Legacy `src/aar/schema_profile.py` is excluded as known-incompatible and must not be changed or represented as PASS.

This slice may provide focused T0 evidence but closes no acceptance row until PMO separately integrates and audits the generated set.

Rev2 is jointly bound to the separately staged normative fixture specification:

```text
docs/implementation/aar-provider-ready-workbench-v1/work-packages/A1c-GF-fixture-spec.json
```

The packet and fixture spec form one fixed-byte review candidate. The spec embeds every valid document, every parsed-invalid base/mutation/re-digest chain, every loader-invalid raw hex string, graph scenarios and expected normalized errors. Product code must not read this docs path at runtime; focused tests compare generator output independently against it.

Exact normative spec identity:

```text
raw sha256  a49428b890f0c8e612fe677b24ca4fd3decc83ad71674a699eddc8f7403605a8
specDigest  sha256:1b45466190c7b54d493ef3d68857633f5b8f2643cd827a602faa976aafc85379
```

It contains ordered arrays `validFixtures` (25), `invalidFixtures` (31 = 14 stale + 17 semantic), `loaderFixtures` (8), and `graphScenarios` (`primary`, `alternative`), with 14-schema valid coverage and 64 unique generated fixture paths. `alternative` is exactly a caller-delegated nested-role projection over the same coherent bytes; it is not a service-owned chain and makes no service-mode coverage claim. The external derivation script is evidence only (`sha256:dcfc1f287a27767e00e74225e35c7e3f8683704c0809fae23e76749b83543de6`) and is not a repository/package dependency. PMO reran it twice under the pinned uv environment and obtained byte-identical spec output.

## 2. Exclusive writable paths

```text
src/aar/provider_ready_contract_generator.py
tests/test_provider_ready_contract_generator.py
schemas/aar-provider-ready-schemas-v1.json
tests/fixtures/provider-ready/manifest.json
tests/fixtures/provider-ready/valid/**
tests/fixtures/provider-ready/invalid/**
```

The leading-space-free authorized test path is exactly `tests/test_provider_ready_contract_generator.py`. The two source/test files and every generated artifact path are new; the writer owns no ancestor directory outside the new `tests/fixtures/provider-ready/**` subtree.

## 3. Exact schema bundle contract

Checked-in path:

```text
schemas/aar-provider-ready-schemas-v1.json
```

Exact top-level shape:

```json
{
  "schema_version": "aar.provider-ready-schema-bundle.v1",
  "schemas": {"<exact schema id>": {"...Draft 2020-12 schema...": "..."}},
  "schema_digests": {"<exact schema id>": "sha256:<64 lowercase hex>"},
  "bundle_digest": "sha256:<64 lowercase hex>"
}
```

Rules:

1. `schemas` and `schema_digests` have exactly the fourteen A1c-R keys in `PROVIDER_READY_SCHEMA_ORDER`, with no fifteenth/internal model.
2. Each schema is exactly `model.model_json_schema(mode="validation")`; `$defs` and local `$ref` values are preserved as emitted.
3. `Draft202012Validator.check_schema()` passes independently for every schema.
4. `schema_digests[id] = canonical_sha256(schemas[id])`.
5. `bundle_digest = canonical_sha256({schema_version, schemas, schema_digests})`, excluding only `bundle_digest`.
6. Emitted bytes use `pretty_json_bytes()` and are deterministic without timestamps, paths, host identity or commit IDs.
7. Generation twice in separate temporary roots is byte-identical.
8. Verify compares exact checked-in bytes, not parsed-object equality.

No per-model schema files are created in v1.

## 4. Fixture manifest contract

Checked-in path:

```text
tests/fixtures/provider-ready/manifest.json
```

Exact top-level shape:

```text
schema_version = aar.provider-ready-fixture-manifest.v1
schema_bundle_digest
fixtures[]
fixture_set_digest
```

Each fixture entry has exact fields:

```text
path
kind = valid | semantic_invalid | digest_invalid | loader_invalid
schema_id = exact id | null only for loader_invalid
project_expected = pass | fail
independent_expected = pass | fail | not_applicable
graph_expected = pass | fail | not_applicable
composite_expected = pass | fail
graph_scenario = string | null
graph_dependencies = array[relative fixture path]
graph_replacements = object[role, relative fixture path]
expected_error_engine = loader | project | independent | graph | null
expected_error_code = string | null
expected_error_location = array[string|integer] | null
expected_error_fragment = string | null
document_digest = sha256 | null
raw_bytes_sha256 = sha256
```

Rules:

1. Entry order is lexicographic by `path`; paths are unique, relative POSIX paths under `valid/` or `invalid/`, with no absolute, `..`, backslash or symlink target.
2. Parsed fixtures use `document_digest = canonical_sha256(parsed object)` and exact raw-file `raw_bytes_sha256`; loader-invalid fixtures have null `document_digest` and bind raw bytes only.
3. `fixture_set_digest = canonical_sha256({schema_version, schema_bundle_digest, fixtures})`, excluding only itself.
4. No fixture points to the enclosing manifest/fixture-set digest. `PairedEvaluationAdmission.fixture_digest` binds an independent frozen case/treatment digest to avoid a cycle.
5. Verify checks manifest self-digest, exact file set (no missing/extra files), exact raw hashes, parsed document digests and bundle join.

Digest wire domains are exact:

- `document_digest`, every per-schema digest, `bundle_digest`, `schema_bundle_digest`, and `fixture_set_digest` are typed lowercase `sha256:<64 hex>` values produced by `canonical_sha256`;
- `raw_bytes_sha256` is **bare** lowercase `hashlib.sha256(raw).hexdigest()` with exactly 64 hex characters.

Engine/composite truth is exact:

```text
standalone valid: project=pass, independent=pass, graph=pass|not_applicable, composite=pass
local semantic/digest invalid: project=fail, independent=pass|fail, graph=not_applicable, composite=fail
graph-only invalid: project=pass, independent=pass, graph=fail, composite=fail
loader invalid: project=fail, independent=not_applicable, graph=not_applicable, composite=fail
```

`project_expected` means A1c-R raw loader plus Pydantic/document-local semantics only. Graph validation is never silently folded into it.

Error normalization is exact:

1. loader: engine `loader`, code is the stable A1c-R `.code`, location is null;
2. project/Pydantic: engine `project`, code is `errors()[target_index]["type"]`, location is its tuple converted to a JSON array (`[]` for root); the fixture spec freezes `target_index` and target fields so multi-error selection cannot drift;
3. independent/jsonschema: engine `independent`, code is the failing validator keyword, location is `absolute_path` as a JSON array (`[]` for root);
4. graph: engine `graph`, code is a frozen uppercase `GRAPH_*` code and location is `["$graph", scenario, join_name]`;
5. passing or not-applicable target: engine/code/location/fragment are null.

`expected_error_fragment` is a stable case-sensitive substring. Null location means no location applies; root errors use `[]`, never null.

## 5. Required valid fixtures

Generate at least these exact paths; additional variants require packet amendment rather than writer invention:

```text
valid/method-adapter.json
valid/activation-intent.json
valid/final-profile.json
valid/activation-generation-authority-first.json
valid/cutover-plan.json
valid/operator-prepared-cutover.json
valid/operator-prepared-restore.json
valid/cutover-receipt-committed.json
valid/cutover-receipt-aborted.json
valid/cutover-receipt-recovery-required.json
valid/restore-receipt-committed.json
valid/restore-receipt-recovery-required.json
valid/operator-terminal-cutover-committed.json
valid/operator-terminal-cutover-aborted.json
valid/operator-terminal-cutover-recovery-required.json
valid/operator-terminal-restore-committed.json
valid/operator-terminal-restore-recovery-required.json
valid/activation-readback-active.json
valid/activation-readback-unconfigured.json
valid/workbench-grant-set.json
valid/provider-alias-attestation.json
valid/evaluation-classification-aar.json
valid/evaluation-classification-prime.json
valid/evaluation-classification-insufficient.json
valid/paired-admission.json
```

All fourteen public schema IDs must have at least one valid fixture. Variants cover cutover/restore unions and terminal mappings. Valid fixtures pass both independent structural validation and project semantic validation.

The fixture spec freezes named primary and alternative graph scenarios. Each scenario binds explicit fixture paths to roles and exact joins without filename inference. Required joins cover adapter→intent→profile, generation authority, plan→prepared→receipt→terminal, readback→authority/profile/grants and alias+AAR/Prime classifications→admission. `graph_dependencies` and `graph_replacements` make graph-only invalid fixtures representable. A graph validator in this module receives parsed documents/mappings only, returns normalized graph errors, and performs no runtime or filesystem mutation.

## 6. Required invalid fixtures

### 6.1 Digest-invalid inventory

Create exactly one fixture for each public schema at these exact paths:

```text
invalid/stale-method-adapter.json
invalid/stale-activation-intent.json
invalid/stale-final-profile.json
invalid/stale-activation-generation-authority.json
invalid/stale-cutover-plan.json
invalid/stale-operator-prepared-marker.json
invalid/stale-cutover-receipt.json
invalid/stale-restore-receipt.json
invalid/stale-operator-terminal-marker.json
invalid/stale-activation-readback.json
invalid/stale-workbench-grant-set.json
invalid/stale-provider-alias-attestation.json
invalid/stale-evaluation-classification.json
invalid/stale-paired-admission.json
```

Each starts from its valid witness, changes only the root self-digest to a different valid digest, and expects the exact project digest error. These may structurally pass.

### 6.2 Semantic-invalid inventory

Create these exact paths from valid witnesses. Every non-digest mutation changes one axis, recomputes the affected root and every containing outer digest, and records exact project error evidence:

```text
invalid/unknown-field.json
invalid/wrong-schema-version.json
invalid/strict-bool.json
invalid/method-adapter-reference-truth.json
invalid/activation-intent-artifact-put-caller-driver.json
invalid/profile-intent-digest-mismatch.json
invalid/authority-generation-prior-mismatch.json
invalid/plan-owner-authority-mismatch.json
invalid/prepared-union-mismatch.json
invalid/cutover-outcome-mapping.json
invalid/restore-frontier-proof-false.json
invalid/terminal-receipt-kind-swap.json
invalid/readback-active-null-binding.json
invalid/grant-set-unsorted-principals.json
invalid/alias-constant-tamper.json
invalid/evaluation-tier-effective-mismatch.json
invalid/paired-expiry-overflow.json
```

Cross-document-only mismatches must be rejected by the focused fixture-graph validator and recorded with `project_expected=pass`, `independent_expected=pass`, `graph_expected=fail` and `composite_expected=fail`; they may pass standalone validation by design. Exact bases, pointers, replacement fixtures/values, re-digest chains, target indices, outcomes and graph joins come from the normative fixture spec rather than filename inference.

### 6.3 Loader-invalid raw bytes

Create exact raw-byte paths:

```text
invalid/raw-duplicate-root.json
invalid/raw-duplicate-nested.json
invalid/raw-float.json
invalid/raw-nonfinite.json
invalid/raw-bom.json
invalid/raw-invalid-utf8.json
invalid/raw-trailing-data.json
invalid/raw-nonobject.json
```

They bind `raw_bytes_sha256`, null `document_digest`, `independent_expected=not_applicable`, and exact A1c-R loader codes. They must not be normalized or parsed before raw-byte validation.

The total generated path set is exact: 25 valid + 14 digest-invalid + 17 semantic-invalid + 8 loader-invalid + manifest = 65 fixture-tree files. The fixture spec contains exactly 64 fixture records and is a reviewed control artifact, not generated output.

## 7. Generator and verifier surface

Expose only:

```text
provider_ready_schema_bundle()
provider_ready_fixture_documents()
provider_ready_asset_bytes()
generate_provider_ready_contract(root)
validate_provider_ready_contract(root)
verify_provider_ready_assets(root)
validate_provider_ready_fixture_graph(documents)
```

Rules:

1. Pure projection functions return JSON-compatible objects or `dict[relative_posix_path, bytes]` without writing.
2. `generate_provider_ready_contract(root)` writes only the exact schema bundle and provider-ready fixture subtree beneath an explicit repository-like root; rejects symlink/path escape and does not touch legacy assets. Preflight requires both targets absent. Any existing target, unexpected entry, partial provider-ready subtree, symlink, non-directory ancestor or case-fold alias fails before mutation with zero writes; generation never deletes or prunes a pre-existing entry.
3. `validate_provider_ready_contract(root)` performs strict loader, manifest/file/hash/digest, independent schema, project model and graph checks without mutation.
4. `verify_provider_ready_assets(root)` regenerates expected bytes in memory and compares exact checked-in bytes/file set; zero writes.
5. No CLI is added in this slice.

Generation constructs every byte in memory first, writes only to a new contained staging subtree, and installs the two absent targets after complete staging. If any write/install fails, it removes only entries created by that same invocation after no-follow containment checks and leaves zero generated residue. Recursive deletion of any pre-existing path is forbidden. `verify` and `validate` reject contaminated/extra/case-fold/symlink trees and are zero-write. Focused fault injection proves preflight zero-write and cleanup of invocation-owned partial staging.

## 8. Secret, injection and legacy-byte canaries

Focused tests must prove:

1. generated assets contain no secret values placed in prohibited semantic/extra fields and no environment-expanded values;
2. in-memory secret/executable material in prohibited semantic or extra fields is rejected and is not checked in;
3. permitted opaque identifiers, URLs and `OperatorPath` values that resemble path/shell text are preserved byte-for-byte as inert data; positive canaries prove no environment expansion, URL/path resolution, import, shell execution or network/process call;
4. secret scan distinguishes allowed `credential_resolver_executable_digest` field names and inert opaque IDs/operator paths from actual secret-bearing extra fields, and checks generated assets/log output for non-leak;
5. generation/verification leave these exact raw SHA-256 values unchanged:

```text
schemas/aar-mcp-tools-v7.json          6ebf848eee74795771af23dfaaeb70fd5030cb88878327f76ffc10d1c4639ab8
schemas/aar-mcp-tools-v8.json          41e97f76f8f371ad2c12d1c4bcba0131491a903cfdf6760430bdeffe4ab2e82f
schemas/aar-mcp-tools-v8-combined.json b4dc4988d081131bff451e740a29e8df55844c3e160a2142368e2bf77dfb5a6d
schemas/aar-schemas-v1.json            02b894c6a1396c2a3f3d3238e44a3f26a5f1e3126bc3e3e4bac5946cd13c33ca
tests/fixtures/manifest.json           69214e21f14be8800e386c7a8f4800311c3fabcc12015a843031b8b3cd3324b0
```

**Post-A1 contract amendment:** the values above remain the historical A1c-GF freeze. The independently reviewed public caller/journal contradiction required `CallerWorkCommitInput.model_response`; the standalone v7 and v8 descriptor assets remain unchanged, while the regenerated dependency-closed `schemas/aar-mcp-tools-v8-combined.json` now has raw SHA-256 `36b41abbcab2e5b3a60dff6a1de85e7a3168cc01135d42c5a88b416abfbca424`. Current generator/checked-in/package verification uses that successor value and does not rewrite the historical row.

No legacy generator is invoked in write mode.

## 9. Required tests and anti-false-pass

1. Assertion-level and behavioral RED before implementation/assets.
2. Exact fourteen bundle keys, per-schema digests, bundle self-digest and independent `check_schema`.
3. Direct resolved `$defs/$ref` assertions for representative nested collection bounds/enums.
4. Two-temp-root generation byte identity and checked-in exact-byte verification.
5. Exact manifest count/file set/order/path safety, the 64-record normative fixture-spec join and all typed/bare digest domains.
6. Every valid fixture passes both engines and graph checks where applicable.
7. Every invalid fixture produces its manifest-declared project/independent/graph/composite result and exact normalized engine/code/location/fragment; tests independently apply the fixture-spec target index and no invalid may pass because another earlier validator masks the intended target.
8. All fourteen stale-root fixtures independently hit the correct digest validator.
9. All semantic negatives are correctly re-digested; dedicated digest fixtures alone retain stale roots.
10. Duplicate/order/max+1 negatives use otherwise-valid unique/domain-correct witnesses and assert no `tuple_type`, duplicate or enum masking unless that is the target.
11. Raw duplicate-key cases reach A1c-R before parsing/schema/Pydantic.
12. Independent JSON Schema limitations are explicit: semantic-only invalid fixtures may structurally pass but composite result remains fail.
13. Frozen graph scenarios, replacements, cycle/swap/tamper probes prove acyclic adapter→intent→profile→authority, plan→prepared→receipt→terminal, readback→authority/profile/grants and alias/classification→admission joins with exact `GRAPH_*` codes/locations.
14. Exact legacy hashes and zero legacy path modifications.
15. Generator refuses any pre-existing/contaminated target, symlink, case alias or traversal before mutation; injected partial-write failure cleans only invocation-owned staging and leaves zero residue; verify/validate are zero-write.
16. Full generated-tree/log secret scan, prohibited-extra-field negatives and permitted-inert-value positive canaries.
17. Static imports exclude runtime/provider/MCP/CLI/supervisor/network/database/credential/env/process authority.

## 10. Commands

Focused suite:

```bash
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-cache --isolated --python 3.12 --frozen pytest -q -p no:cacheprovider tests/test_provider_ready_contract_generator.py tests/test_provider_ready_contract.py
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-cache --isolated --python 3.12 --frozen ruff check --no-cache src/aar/provider_ready_contract_generator.py tests/test_provider_ready_contract_generator.py
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-cache --isolated --python 3.12 --frozen ruff check --no-cache --select W291,W293 src/aar/provider_ready_contract_generator.py tests/test_provider_ready_contract_generator.py
```

The writer must generate exact checked-in assets, then rerun focused tests and both Ruff gates on final bytes. Do not run T2+, full suite, wheel build or live tests in this slice.

## 11. Forbidden scope

```text
src/aar/provider_ready_contract.py
all four provider_ready_*_models.py
src/aar/contract.py
src/aar/schema_generator.py
src/aar/schema_profile.py
src/aar/canonical.py
src/aar/schemas.py
src/aar/versions.py
schemas/aar-schemas-v1.json
schemas/aar-mcp-tools-v7.json
schemas/aar-mcp-tools-v8.json
schemas/aar-mcp-tools-v8-combined.json
tests/fixtures/manifest.json
pyproject.toml
src/aar/mcp/**
src/aar/runtime/**
skills/**
profiles/**
SDD/control/evidence files
```

No package metadata, bundled projection, runtime/profile loader, activation, migration, provider call, credential resolution, grant issuance, attempt allocation, send/replay, scoring, physical launch, persistence, network or live behavior.

## 12. Completion gate

Focused-green requires exact generated path custody, deterministic assets, all focused tests and both Ruff gates PASS, secret/legacy-hash/forbidden-path checks, exact final file inventory/hashes and a product commit. PMO must still run an independent affected integration audit before any T0 row closure or A2 dispatch.
