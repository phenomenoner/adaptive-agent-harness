# A1c-R — Canonical Provider-Ready Registry and Strict Raw-Byte Loader

**Status:** `rev2_review_pending`

**Parent lane:** A1c — registry/generator/schema/fixture convergence

**Task-start candidate parent:** `82c698bb627a9e077e4a3e0bf54b1ed0ab8232f8`

## 1. Authority and decision boundary

This serial prerequisite consumes the exact focused-green A1a/A1b model bytes and implements only the canonical fourteen-model registry plus a raw-byte duplicate-key-rejecting loader/dispatcher.

Frozen authority requires strict Draft 2020-12 schemas and cross-validator agreement but does not require the legacy bounded `src/aar/schema_profile.py`. A1c-R does not generate or validate checked-in schemas. A1c-GF will use `jsonschema.Draft202012Validator` as the independent structural validator and the strict loader/Pydantic models as the project semantic validator. `schema_profile.py` is explicitly excluded and its known pattern incompatibility is not represented as PASS.

This slice closes no acceptance row and performs no runtime/provider/live action.

## 2. Exclusive writable paths

```text
src/aar/provider_ready_contract.py
tests/test_provider_ready_contract.py
```

Both are new. Every other path is read-only.

## 3. Exact canonical inventory

The new module imports one-way from exactly these existing registries:

```text
PROVIDER_READY_SCHEMA_MODELS                  # 3
PROVIDER_READY_OPERATOR_SCHEMA_MODELS         # 6
PROVIDER_READY_RUNTIME_SCHEMA_MODELS          # 2
PROVIDER_READY_EVALUATION_SCHEMA_MODELS       # 3
```

It exposes:

```text
PROVIDER_READY_SCHEMA_MODELS: dict[str, type[StrictModel]]
PROVIDER_READY_SCHEMA_ORDER: tuple[str, ...]
```

Module-level construction must call one module-private pure `_merge_schema_registries(*registries)` helper. That exact production helper rejects duplicate source keys and is directly exercised by the synthetic collision test; tests may not substitute a test-local merger.

Rules:

1. Exact union count is fourteen with no key collision and no overwrite-on-merge behavior.
2. Keys are the exact fourteen public schema-version literals and `PROVIDER_READY_SCHEMA_ORDER == tuple(sorted(PROVIDER_READY_SCHEMA_MODELS))`.
3. Values are the exact public top-level classes from the four local registries.
4. Internal `IssuedWorkbenchGrant`, `CandidateBinding`, nested records and umbrella constants are absent.
5. A duplicate key across source registries fails closed during canonical registry construction; it is never silently overwritten.
6. Existing legacy registries (`SCHEMA_MODELS`, `CONTRACT_MODELS`) and all four source registries remain byte-unchanged.
7. The four model modules never import this A1c consumer, preserving acyclic import direction.

## 4. Strict raw-byte loader

Expose one public inert `ProviderReadyContractError(ValueError)` base with a stable `.code` string. Concrete public subclasses/codes are exact:

```text
ProviderReadyInputTypeError          INPUT_TYPE
ProviderReadyBomError                UTF8_BOM
ProviderReadyUtf8Error               UTF8_DECODE
ProviderReadyDuplicateKeyError       DUPLICATE_KEY
ProviderReadyFloatError              FLOAT_NOT_ALLOWED
ProviderReadyNonfiniteError          NONFINITE_NOT_ALLOWED
ProviderReadyJsonSyntaxError         JSON_SYNTAX
ProviderReadyJsonTrailingDataError   JSON_TRAILING_DATA
ProviderReadyNonObjectError          NON_OBJECT_ROOT
ProviderReadySchemaMissingError      SCHEMA_VERSION_MISSING
ProviderReadySchemaTypeError         SCHEMA_VERSION_TYPE
ProviderReadySchemaUnknownError      SCHEMA_VERSION_UNKNOWN
ProviderReadySchemaMismatchError     SCHEMA_VERSION_MISMATCH
```

Every message begins exactly `<CODE>:` followed by stable human text; duplicate-key text may append the offending key. Pydantic `ValidationError` is not wrapped and propagates unchanged after dispatch.

Expose these functions:

```text
reject_duplicate_object_keys(pairs)
load_provider_ready_json_bytes(data: bytes) -> dict[str, object]
validate_provider_ready_document_bytes(
    data: bytes,
    *,
    expected_schema_id: str | None = None,
) -> StrictModel
```

### 4.1 Parsing order and byte policy

`load_provider_ready_json_bytes` must:

1. accept exact `bytes` only; reject `str`, `bytearray`, mappings and file paths;
2. decode strict UTF-8 and reject BOM/non-UTF-8;
3. parse with `json.loads(..., object_pairs_hook=reject_duplicate_object_keys)`;
4. reject duplicate keys at every root/nested object before schema/model dispatch, even if duplicate values are identical;
5. reject JSON floats and non-standard `NaN`, `Infinity`, `-Infinity` at loader level because canonical contract JSON has no floats;
6. reject trailing JSON/syntax errors distinctly;
7. reject non-object roots distinctly;
8. return only the parsed object and perform no I/O, filesystem, env, clock, network, process or runtime work.

Exact precedence is:

```text
INPUT_TYPE → UTF8_BOM → UTF8_DECODE → parser lexical/hook result
→ NON_OBJECT_ROOT → SCHEMA_VERSION_MISSING → SCHEMA_VERSION_TYPE
→ SCHEMA_VERSION_UNKNOWN → SCHEMA_VERSION_MISMATCH → Pydantic ValidationError
```

Within JSON parsing, the first condition reached by the standard parser wins. A complete duplicate object reaches `DUPLICATE_KEY`; malformed JSON may raise `JSON_SYNTAX` before `object_pairs_hook` can run. `JSONDecodeError` with `msg == "Extra data"` maps to `JSON_TRAILING_DATA`; other decode errors map to `JSON_SYNTAX`. No claim is made that duplicates outrank syntax in malformed JSON.

### 4.2 Model dispatch

`validate_provider_ready_document_bytes` must:

1. call the strict raw-byte loader first;
2. require one string `schema_version` field;
3. reject missing/unknown schema IDs and an `expected_schema_id` mismatch before model validation;
4. dispatch through the canonical fourteen-key registry only;
5. preserve strict JSON-array semantics by validating canonical reserialized JSON bytes with `model_validate_json(..., strict=True)`, rather than passing parsed lists to Python tuple fields and false-failing with `tuple_type`;
6. return the exact model instance type;
7. retain every model's required/default/extra/self-digest/cross-field rejection;
8. never infer or repair a digest, order, duplicate value or missing field.

Canonical reserialization after successful raw-byte parsing is allowed solely to preserve JSON-array semantics; raw duplicate-key/floating-point/syntax errors have already been rejected and cannot be collapsed before dispatch.

## 5. Required RED and focused tests

1. Assertion-level RED that the new module/imports are absent before implementation.
2. Exact fourteen-key/class/order inventory and `3+6+2+3` source-registry join.
3. Synthetic duplicate source-registry key counterprobe directly exercising the same production `_merge_schema_registries` helper used by module-level construction, proving construction fails rather than overwrites.
4. Static import-direction proof: all four model modules do not import `provider_ready_contract`.
5. Raw duplicate root-key and nested-key rejection, including identical duplicate values.
6. Invalid UTF-8, UTF-8 BOM, malformed/trailing JSON, every non-object JSON root (`null`, booleans, integer, string, array), float and `NaN|Infinity|-Infinity` loader errors, asserting exact public subtype/code/message prefix.
7. Exact-type rejection for `str`, `bytearray`, `memoryview`, mapping, path-like values and a `bytes` subclass; only `type(data) is bytes` is accepted.
8. Missing/unknown/non-string schema ID and exact expected-schema mismatch.
9. Compound ordering witnesses: unknown schema plus a duplicate key and unknown schema plus a JSON float each return the raw-loader error before schema dispatch; expected-schema mismatch plus an otherwise model-invalid payload returns `SCHEMA_VERSION_MISMATCH` before Pydantic. A malformed duplicate object may return `JSON_SYNTAX`, matching parser reachability.
10. At least one valid self-digested JSON document from each of the four source registries round-trips to its exact model type; JSON arrays reach tuple fields without `tuple_type` masking.
11. For **each** of the four source registries require both: (a) one correctly re-digested one-axis non-digest semantic negative reaching the exact intended cross-field error, and (b) one dedicated stale-self-digest witness reaching that registry's exact digest error. A disjunction is insufficient and the dispatcher may not repair either witness.
12. A correctly re-digested unsorted JSON-array witness and a separate duplicate-value JSON-array witness reach their exact model order/duplicate errors with no `tuple_type` masking; canonical reserialization may not sort or deduplicate arrays.
13. Unknown/extra field and strict-bool negatives assert exact Pydantic location/type after loader dispatch.
14. Static absence of legacy registry/generator edits and of runtime/provider/MCP/CLI/supervisor/launcher/DB/filesystem/env/network/process/clock imports or behavior.
15. No generated schemas, fixtures, manifests, package metadata or bundled assets are written in this slice.

Tests must not import helper functions from other test modules. Dedicated digest-tamper cases may retain stale digests; every semantic non-digest mutation must recompute the correct root and containing digests and assert the intended target location/message.

## 6. Commands

```bash
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-cache --isolated --python 3.12 --frozen pytest -q -p no:cacheprovider tests/test_provider_ready_contract.py
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-cache --isolated --python 3.12 --frozen ruff check --no-cache src/aar/provider_ready_contract.py tests/test_provider_ready_contract.py
UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-cache --isolated --python 3.12 --frozen ruff check --no-cache --select W291,W293 src/aar/provider_ready_contract.py tests/test_provider_ready_contract.py
```

Do not run T2+, full suite, generators or live tests in this slice.

## 7. Forbidden scope

```text
provider_ready_models.py
provider_ready_operator_models.py
provider_ready_runtime_models.py
provider_ready_evaluation_models.py
src/aar/contract.py
src/aar/schema_generator.py
src/aar/schema_profile.py
src/aar/canonical.py
src/aar/schemas.py
src/aar/versions.py
schemas/**
tests/fixtures/**
pyproject.toml
src/aar/mcp/**
src/aar/runtime/**
skills/**
profiles/**
SDD/control/evidence files
```

No filesystem/path loader, generator, CLI, MCP surface, package consumer, provider call, credential lookup, runtime host construction, grant issuance, attempt allocation, send/replay, scoring, physical launch, migration or persistence is permitted.

## 8. Completion gate

Focused-green requires exact two-path custody, all focused tests and both Ruff gates PASS, final status/hash/secret/forbidden checks, and a product commit. It remains prerequisite evidence only. A1c-GF receives a fresh serial packet and separate review after A1c-R completion.
