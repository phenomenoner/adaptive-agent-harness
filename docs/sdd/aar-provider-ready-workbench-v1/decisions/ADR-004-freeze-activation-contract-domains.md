# ADR-004 — Freeze activation lexical and canonical collection domains

**Status:** proposed successor decision

**Date:** 2026-08-22

## Context

The first activation-model implementation packet exposed a genuine specification gap. The reviewed field inventory named arrays and identifiers but did not own their lexical domains, cardinality, empty-list behavior, ordering, uniqueness, principal matching language, or the stateful owner for `activation_generation`. A worker could therefore produce mutually incompatible strict schemas while still appearing faithful to the examples.

## Decision

1. Activation identifiers reuse predecessor aliases only where their lexical semantics match exactly. New package-version and local-authority-store domains are declared directly in `CONTRACTS.md`.
2. `adapters` contains a `1..6` unique subset of the exact frozen v8 workbench catalog and is sorted by that catalog's published rank. Generic `artifact.read` is intentionally outside the activation domain.
3. `artifact.put + caller_driver` is invalid because frozen registry-v6 caller-work request/DDL domains omit `artifact.put`; activatable artifact manifests are native or reference. This activation restriction does not change frozen v8 bytes.
4. Route profile IDs, principal IDs, and capability lists each contain `1..64` sorted unique values. Empty or non-canonical document arrays are rejected.
5. The legacy field name `principal_patterns` remains for the v1 wire shape, but v1 matching is exact principal-ID equality. No glob or regex language exists in v1.
6. `activation_generation` uses the predecessor positive-counter wire domain. Immutable per-profile `aar.activation-generation-authority.v1` history records form the unique monotonicity/non-reuse chain; atomic history publication under the exclusive runtime-home lock is the commit linearization point. `current.json` is only a derived repairable pointer. A transport-neutral intent alone cannot establish history.
7. `backend_kind="reference"` requires `reference_only=true` and capability `evidence_tier=unknown`. Frozen v8 truth reports an exact reference factory as configured-but-reference-only; separate admission logic rejects it as unusable. Other fake/deterministic implementation properties are verified against the immutable factory registry, not inferred from identifier text.
8. Adapter manifest `evidence_tier` uses only the frozen capability domain. Provider execution/evaluation classification is owned solely by strict `aar.evaluation-evidence-classification.v1` per-component provider/model/reasoning/fallback/cache tiers, derived route qualification, usage and visibility fields. A Prime observed-provider/model plus requested-only-reasoning record is representable without inventing effective effort; capability truth never upgrades it.
9. Allowed identifiers are inert data. Security verification inspects schema-property inventory, imports, expansion/resolution/execution behavior, and known credential canaries; it does not classify permitted identifier contents as commands, URLs, or secrets.
10. Issue/build helpers may canonicalize operator input, but strict validation of an existing JSON/document rejects non-canonical arrays rather than silently rewriting bytes covered by a digest.

## Rejected alternatives

- **Inherit `ModelRouteCatalog` rules by analogy:** rejected because lower-layer source conventions do not own a new public contract.
- **Leave arrays in authored order:** rejected because order has no preference semantics and would create multiple digests for the same intended set.
- **Allow glob or regex principal patterns:** rejected because no matching language, escaping rule, compatibility contract, or injection boundary was reviewed.
- **Let the model prove generation monotonicity:** rejected because historical authority is external to one immutable document.
- **Scan every identifier string for URL/command-like text:** rejected as non-falsifiable and prone to rejecting legitimate opaque identities.

## Compatibility and non-goals

This decision adds no MCP method, provider client, credential field, database migration, matching engine, or live-call authority. It adds one strict local-file activation-generation authority record without altering registry-v6 SQL. A future principal matching language, additional workbench method, or multiple adapters per method requires a new schema version and independent compatibility review.

## Acceptance

`A-ACT-001`, `A-ACT-003`, `A-ACT-005`, `A-COMP-001`, and `A-SEC-001` own invalid fixtures for canonical package versions, the six-method catalog, forbidden `artifact.read` and `artifact.put + caller_driver` combinations, empty/duplicate/unsorted collections, generation wire range and stateful CAS, exact-principal semantics, reference capability truth, distinct evidence domains, prohibited semantic fields, nested digests, and independent digest recomputation.
